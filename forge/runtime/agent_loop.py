'''Multi-step model and tool execution for the M1 Agent Loop.'''

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import cache
from itertools import count
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge.context.compactor import CompactionConfig
from forge.context.manager import (
    CompactionReport,
    ContextManager,
    ContextStats,
)
from forge.context.working import WorkingState
from forge.hooks import HookEvent, HookManager, HookOutcome
from forge.permissions.policy import PermissionManager, PermissionMode
from forge.permissions.risk import classify_tool_call
from forge.runtime.intent import (
    infer_change_required,
    infer_explore_delegation_required,
    infer_full_test_suite_required,
    infer_test_execution_required,
    infer_verification_required,
)
from forge.runtime.model_client import (
    AnthropicModelClient,
    ModelCallError,
    ModelClient,
    ModelOutputTruncatedError,
    ModelProtocolError,
)
from forge.runtime.completion import (
    CompletionGate,
    TaskPolicy,
    verification_command_runs_full_suite,
    verification_command_runs_tests,
)
from forge.runtime.state import (
    CompletionBlocked,
    ConversationEvent,
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    ModelTextDelta,
    ModelToolCallCompleted,
    ModelUsageUpdate,
    TokenUsage,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
    TurnResult,
    ToolCall,
    VerificationCompleted,
    VerificationEvidence,
    WorkspaceChanged,
)
from forge.runtime.workspace import WorkspaceTracker
from forge.sessions.checkpoint import CheckpointError, CheckpointStore
from forge.tasks.manager import TaskManager
from forge.tools.base import ToolRegistry, ToolResult
from forge.tools.task import create_task_tools

if TYPE_CHECKING:
    from forge.mcp.manager import MCPClientManager
    from forge.sessions.store import SessionJournal, SessionStore
    from forge.tasks.state import ActiveTask


EDIT_RECOVERY_READ_TOOLS = frozenset(
    {'read_file', 'grep'}
)


class ModelResponseError(RuntimeError):
    '''Raised when a model response cannot continue the Agent Loop.'''


@cache
def load_system_prompt() -> str:
    '''Load the packaged ForgeCode identity and behavior prompt.'''
    prompt_path = Path(__file__).resolve().parents[1] / 'prompts' / 'system.md'
    prompt = prompt_path.read_text(encoding='utf-8').strip()
    if not prompt:
        raise RuntimeError('ForgeCode system prompt is empty.')
    return prompt


class Conversation:
    '''Keep model-visible message history for an interactive session.'''

    def __init__(
        self,
        client: ModelClient | None = None,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        registry: ToolRegistry | None = None,
        max_iterations: int | None = 80,
        task_policy: TaskPolicy | None = None,
        context_config: CompactionConfig | None = None,
        context_root: Path | None = None,
        max_protocol_recoveries: int = 2,
        max_tool_protocol_recoveries: int = 6,
        max_output_continuations: int = 2,
        repeated_tool_limit: int = 2,
        stagnation_warning: int = 4,
        stagnation_limit: int = 8,
        change_exploration_limit: int = 12,
        completion_decision_limit: int = 3,
        mutation_recovery_limit: int = 4,
        max_tool_calls: int | None = 120,
        max_turn_input_tokens: int | None = None,
        initial_messages: list[dict[str, Any]] | None = None,
        active_task: ActiveTask | None = None,
        session_journal: SessionJournal | None = None,
        checkpoint_store: CheckpointStore | None = None,
        session_store: SessionStore | None = None,
        hook_manager: HookManager | None = None,
        permission_manager: PermissionManager | None = None,
        mcp_manager: MCPClientManager | None = None,
        include_task_tools: bool = True,
    ) -> None:
        if tools is not None and registry is not None:
            raise ValueError('Pass tools or registry, not both.')
        if max_iterations is not None and max_iterations < 1:
            raise ValueError('max_iterations must be positive')
        if max_protocol_recoveries < 0:
            raise ValueError('max_protocol_recoveries must not be negative')
        if max_tool_protocol_recoveries < 1:
            raise ValueError(
                'max_tool_protocol_recoveries must be positive'
            )
        if max_output_continuations < 0:
            raise ValueError('max_output_continuations must not be negative')
        if repeated_tool_limit < 1:
            raise ValueError('repeated_tool_limit must be positive')
        if stagnation_warning < 1:
            raise ValueError('stagnation_warning must be positive')
        if stagnation_limit <= stagnation_warning:
            raise ValueError(
                'stagnation_limit must be greater than stagnation_warning'
            )
        if change_exploration_limit < 1:
            raise ValueError('change_exploration_limit must be positive')
        if completion_decision_limit < 1:
            raise ValueError('completion_decision_limit must be positive')
        if mutation_recovery_limit < 1:
            raise ValueError('mutation_recovery_limit must be positive')
        if max_tool_calls is not None and max_tool_calls < 1:
            raise ValueError('max_tool_calls must be positive')
        if max_turn_input_tokens is not None and max_turn_input_tokens < 1:
            raise ValueError('max_turn_input_tokens must be positive')
        self.client = (
            client if client is not None else AnthropicModelClient.from_config()
        )
        self.system_prompt = (
            system_prompt
            if system_prompt is not None
            else load_system_prompt()
        )
        self.messages: list[dict[str, Any]] = [
            dict(message) for message in (initial_messages or [])
        ]
        self.session_journal = session_journal
        self.checkpoint_store = checkpoint_store
        self.session_store = session_store
        self.hook_manager = hook_manager
        self._hooks_started = False
        self._pending_hook_context: list[str] = []
        self.registry = registry
        self.max_iterations = max_iterations
        tracker = (
            getattr(registry, 'workspace_tracker', None)
            if registry is not None
            else None
        )
        if task_policy is not None and tracker is None:
            raise ValueError(
                'task_policy requires a ToolRegistry with a '
                'WorkspaceTracker'
            )
        self.workspace_tracker: WorkspaceTracker | None = tracker
        resolved_context_root = (
            context_root
            if context_root is not None
            else tracker.root
            if tracker is not None
            else Path.cwd()
        )
        self.task_manager = TaskManager(resolved_context_root)
        self.task_manager.restore(active_task)
        self.permission_manager = permission_manager or PermissionManager(
            resolved_context_root,
            journal=session_journal,
        )
        self.mcp_manager = mcp_manager
        if self.mcp_manager is not None:
            self.mcp_manager.bind(
                self.permission_manager,
                session_journal,
            )
        self.working_state = WorkingState()
        if registry is not None and include_task_tools:
            for task_tool in create_task_tools(
                resolved_context_root,
                self.task_manager,
            ):
                registry.register(task_tool)
        self.tools = registry.definitions if registry is not None else tools
        self.finish_protocol = (
            registry is not None and 'finish_task' in registry.names
        )
        self.context = ContextManager(
            self.messages,
            resolved_context_root,
            context_config,
        )
        self.completion_gate = (
            CompletionGate(tracker.root, task_policy)
            if tracker is not None
            else None
        )
        self.max_protocol_recoveries = max_protocol_recoveries
        self.max_tool_protocol_recoveries = max_tool_protocol_recoveries
        self.max_output_continuations = max_output_continuations
        self.repeated_tool_limit = repeated_tool_limit
        self.stagnation_warning = stagnation_warning
        self.stagnation_limit = stagnation_limit
        self.change_exploration_limit = change_exploration_limit
        self.completion_decision_limit = completion_decision_limit
        self.mutation_recovery_limit = mutation_recovery_limit
        self.max_tool_calls = max_tool_calls
        self.max_turn_input_tokens = max_turn_input_tokens
        self._last_repository_context = self.context.repository.system_suffix('')
        self._last_task_context = ''

    def _tool_definitions(self) -> list[dict[str, Any]] | None:
        if self.registry is not None:
            return self.registry.definitions
        return self.tools

    @property
    def context_stats(self) -> ContextStats:
        '''Return current committed conversation context statistics.'''
        return self.context.stats_for_request(
            system_prompt=self._system_prompt_with_task(),
            repository_context=self._last_repository_context,
            tools=self._tool_definitions(),
            context_window_tokens=getattr(
                self.client,
                'context_window',
                None,
            ),
            reserved_output_tokens=getattr(self.client, 'max_tokens', 0),
        )

    async def stream(self, prompt: str) -> AsyncIterator[ConversationEvent]:
        '''Run model-tool cycles until the model returns a final text answer.'''
        if not prompt.strip():
            raise ValueError('prompt must not be empty')

        await self.session_start(source='stream')
        if self.mcp_manager is not None:
            await self.mcp_manager.ensure_connected()

        self.task_manager.begin_turn(prompt)
        self.working_state = WorkingState()
        self._last_task_context = self.task_manager.system_suffix()
        user_message = {'role': 'user', 'content': prompt}
        request_messages = [*self.messages, user_message]
        checkpoint_id: str | None = None
        if self.checkpoint_store is not None:
            checkpoint_id = self.checkpoint_store.begin()
            if self.session_journal is not None:
                self.session_journal.record_checkpoint_created(
                    checkpoint_id,
                    self.messages,
                    self.task_manager.active,
                )
        if self.session_journal is not None:
            self.session_journal.record_user_message(
                user_message,
                self.task_manager.active,
            )
        completed_usage = TokenUsage(input_tokens=0, output_tokens=0)
        all_tool_calls: list[ToolCall] = []
        latest_verification: VerificationEvidence | None = None
        mutation_attempted = False
        change_required = bool(
            (
                self.completion_gate is not None
                and self.completion_gate.policy.require_changes
            )
            or (
                self.workspace_tracker is not None
                and infer_change_required(prompt)
            )
        )
        verification_required = bool(
            self.completion_gate is not None
            and (
                self.completion_gate.policy.require_verification
                or infer_verification_required(prompt)
            )
        )
        tests_required = bool(
            self.completion_gate is not None
            and infer_test_execution_required(prompt)
        )
        full_tests_required = bool(
            tests_required and infer_full_test_suite_required(prompt)
        )
        exploration_delegation_pending = bool(
            change_required
            and tests_required
            and infer_explore_delegation_required(prompt)
            and self.registry is not None
            and 'explore_repository' in self.registry.names
        )
        if exploration_delegation_pending:
            request_messages.append(
                {
                    'role': 'user',
                    'content': (
                        'ForgeCode large-task routing: delegate the initial '
                        'cross-file investigation with explore_repository now. '
                        'Use its compact structured report before editing; do '
                        'not manually scan the repository in the parent context.'
                    ),
                }
            )
        tool_attempts: dict[str, tuple[int, bool]] = {}
        calls_without_progress = 0
        mutation_failure_count = 0
        mutation_failure_total = 0
        mutation_failure_targets: tuple[str, ...] = ()
        mutation_failures: list[dict[str, Any]] = []
        mutation_recovery_read_used = False
        mutation_recovery_context = ''
        mutation_text_recoveries = 0
        force_synthesis = False
        change_convergence_required = False
        change_convergence_read_used = False
        change_convergence_extra_reads = 0
        post_mutation_convergence = False
        verification_recovery = False
        tool_protocol_failures = 0
        synthesis_retries = 0
        incomplete_declaration_recoveries = 0
        finalization_recovery = False
        completion_ready_revision: int | None = None
        completion_decision_calls = 0
        completion_ready_context = ''
        completion_reviewed_paths: set[str] = set()
        if self.workspace_tracker is not None:
            await self.workspace_tracker.begin_turn()

        self._last_repository_context = (
            self.context.repository.system_suffix(prompt)
        )
        reactive_compaction_attempted = False
        protocol_recoveries = 0
        output_continuations = 0
        continued_text_parts: list[str] = []

        iterations = (
            count(1)
            if self.max_iterations is None
            else range(1, self.max_iterations + 1)
        )
        for iteration in iterations:
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            request_usage: TokenUsage | None = None

            if (
                self.max_turn_input_tokens is not None
                and completed_usage.total_input_tokens
                >= self.max_turn_input_tokens
            ):
                reason = (
                    'Stopped after the turn consumed '
                    f'{completed_usage.total_input_tokens} input tokens, '
                    'reaching the configured cumulative input-token limit '
                    f'of {self.max_turn_input_tokens}.'
                )
                self.task_manager.stuck((reason,))
                self.messages[:] = request_messages
                yield TurnCompleted(
                    result=TurnResult(
                        text=reason,
                        usage=completed_usage,
                        model_calls=iteration - 1,
                        tool_calls=tuple(all_tool_calls),
                        status='stuck',
                        changed_paths=(
                            self.workspace_tracker.changed_paths
                            if self.workspace_tracker is not None
                            else ()
                        ),
                        verification=latest_verification,
                        completion_reasons=(reason,),
                    )
                )
                return

            delegation_request_active = exploration_delegation_pending
            if delegation_request_active:
                request_tools = [
                    definition
                    for definition in self._tool_definitions() or ()
                    if str(definition.get('name', ''))
                    == 'explore_repository'
                ]
            elif finalization_recovery:
                request_tools = None
            elif mutation_failures:
                request_tools = self._edit_recovery_tools(
                    read_available=not mutation_recovery_read_used,
                )
            elif verification_recovery:
                request_tools = self._verification_tools(
                    require_tests=tests_required,
                )
            elif change_convergence_required:
                read_available = (
                    not change_convergence_read_used
                    or change_convergence_extra_reads > 0
                )
                if post_mutation_convergence:
                    request_tools = self._post_mutation_tools(
                        read_available=read_available,
                    )
                else:
                    request_tools = self._edit_recovery_tools(
                        read_available=read_available,
                    )
            else:
                request_tools = self._tool_definitions()
            request_tools = self._permission_filtered_tools(request_tools)
            request_tool_names = {
                str(definition.get('name', ''))
                for definition in request_tools or ()
            }
            request_system_prompt = self._request_system_prompt(
                force_synthesis=force_synthesis,
                mutation_recovery_context=mutation_recovery_context,
                finalization_recovery=finalization_recovery,
                completion_ready_context=completion_ready_context,
                change_required=change_required,
                mutation_attempted=mutation_attempted,
                verification_required=verification_required,
                tests_required=tests_required,
                full_tests_required=full_tests_required,
                verification_recovery=verification_recovery,
            )
            context_window_tokens = getattr(
                self.client, 'context_window', None
            )
            reserved_output_tokens = getattr(
                self.client, 'max_tokens', 0
            )
            compaction_required = self.context.compaction_required(
                request_messages,
                system_prompt=request_system_prompt,
                repository_context=self._last_repository_context,
                tools=request_tools,
                context_window_tokens=context_window_tokens,
                reserved_output_tokens=reserved_output_tokens,
            )
            compaction_allowed = compaction_required
            if compaction_required:
                before_compact = await self._emit_hook(
                    HookEvent(
                        name='BeforeCompact',
                        session_id=self._session_id(),
                        payload={
                            'automatic': True,
                            'message_count': len(request_messages),
                        },
                    )
                )
                self._queue_hook_context(before_compact)
                compaction_allowed = before_compact.allowed
            pending_hook_context = tuple(self._pending_hook_context)
            self._pending_hook_context.clear()
            model_hook = await self._emit_hook(
                HookEvent(
                    name='BeforeModelCall',
                    session_id=self._session_id(),
                    payload={
                        'iteration': iteration,
                        'message_count': len(request_messages),
                    },
                )
            )
            if not model_hook.allowed:
                reason = (
                    'BeforeModelCall hook blocked the model request: '
                    f'{model_hook.reason}'
                )
                self.task_manager.stuck((reason,))
                self.messages[:] = request_messages
                yield TurnCompleted(
                    result=TurnResult(
                        text=reason,
                        usage=completed_usage,
                        model_calls=iteration - 1,
                        tool_calls=tuple(all_tool_calls),
                        status='failed',
                        changed_paths=(
                            self.workspace_tracker.changed_paths
                            if self.workspace_tracker is not None
                            else ()
                        ),
                        verification=latest_verification,
                        completion_reasons=(reason,),
                    )
                )
                return
            hook_context = (
                *pending_hook_context,
                *model_hook.additional_context,
            )
            if hook_context:
                request_system_prompt = (
                    '{}\n\n<forge_hook_context>\n{}'
                    '\n</forge_hook_context>'
                ).format(
                    request_system_prompt,
                    '\n\n'.join(hook_context),
                )
            compaction = None
            if not compaction_required or compaction_allowed:
                compaction = await self.context.compact_history(
                    request_messages,
                    self.client,
                    system_prompt=request_system_prompt,
                    repository_context=self._last_repository_context,
                    tools=request_tools,
                    context_window_tokens=context_window_tokens,
                    reserved_output_tokens=reserved_output_tokens,
                )
            if (
                compaction is not None
                and compaction.success
                and self.session_journal is not None
            ):
                self.session_journal.record_context_compacted(
                    request_messages
                )
            yield ModelCallStarted(iteration=iteration)
            try:
                async for event in self.client.stream(
                    messages=self.context.prepare(request_messages),
                    tools=request_tools,
                    system=request_system_prompt,
                ):
                    if isinstance(event, ModelTextDelta):
                        text_parts.append(event.text)
                        yield event
                    elif isinstance(event, ModelToolCallCompleted):
                        tool_calls.append(event.tool_call)
                        yield event
                    elif isinstance(event, ModelUsageUpdate):
                        request_usage = event.usage
                        yield ModelUsageUpdate(
                            usage=add_token_usage(
                                completed_usage,
                                request_usage,
                            ),
                            request_usage=request_usage,
                            model_calls=iteration,
                        )
                    else:
                        yield event
            except Exception as error:
                partial_text = ''.join(text_parts)
                if (
                    isinstance(error, ModelOutputTruncatedError)
                    and not error.tool_names
                    and not tool_calls
                    and partial_text.strip()
                ):
                    if (
                        output_continuations
                        < self.max_output_continuations
                        and request_usage is not None
                    ):
                        output_continuations += 1
                        completed_usage = add_token_usage(
                            completed_usage,
                            request_usage,
                        )
                        continued_text_parts.append(partial_text)
                        request_messages.extend(
                            [
                                {
                                    'role': 'assistant',
                                    'content': partial_text,
                                },
                                build_output_continuation_feedback(
                                    attempt=output_continuations,
                                    maximum=self.max_output_continuations,
                                ),
                            ]
                        )
                        yield ModelCallFailed(
                            iteration=iteration,
                            reason=error.reason,
                            retryable=True,
                        )
                        continue
                    yield ModelCallFailed(
                        iteration=iteration,
                        reason=error.reason,
                        retryable=False,
                    )
                    raise
                if (
                    isinstance(error, ModelCallError)
                    and error.reason == 'context_overflow'
                    and not reactive_compaction_attempted
                ):
                    reactive_compaction_attempted = True
                    reactive_hook = await self._emit_hook(
                        HookEvent(
                            name='BeforeCompact',
                            session_id=self._session_id(),
                            payload={
                                'automatic': True,
                                'reactive': True,
                                'message_count': len(request_messages),
                            },
                        )
                    )
                    self._queue_hook_context(reactive_hook)
                    report = None
                    if reactive_hook.allowed:
                        report = await self.context.compact_history(
                            request_messages,
                            self.client,
                            force=True,
                        )
                    if report is not None and report.success:
                        if self.session_journal is not None:
                            self.session_journal.record_context_compacted(
                                request_messages
                            )
                        continue
                if (
                    isinstance(error, ModelProtocolError)
                    and protocol_recoveries < self.max_protocol_recoveries
                ):
                    protocol_recoveries += 1
                    if request_usage is not None:
                        completed_usage = add_token_usage(
                            completed_usage,
                            request_usage,
                        )
                    yield ModelCallFailed(
                        iteration=iteration,
                        reason=error.reason,
                        retryable=True,
                    )
                    request_messages.extend(
                        build_protocol_recovery_feedback(
                            error,
                            attempt=protocol_recoveries,
                            maximum=self.max_protocol_recoveries,
                            available_tools=(
                                tuple(sorted(request_tool_names))
                            ),
                        )
                    )
                    continue
                yield ModelCallFailed(
                    iteration=iteration,
                    reason=(
                        error.reason
                        if isinstance(
                            error,
                            (ModelCallError, ModelProtocolError),
                        )
                        else type(error).__name__
                    ),
                    retryable=(
                        error.retryable
                        if isinstance(error, ModelCallError)
                        else False
                    ),
                )
                raise
            yield ModelCallCompleted(iteration=iteration)

            text = ''.join(text_parts).strip()
            complete_text = ''.join(
                [*continued_text_parts, text]
            ).strip()
            if not text and not tool_calls:
                if (
                    request_usage is not None
                    and self._pending_required_change(change_required)
                ):
                    completed_usage = add_token_usage(
                        completed_usage,
                        request_usage,
                    )
                    reason = required_change_block_reason()
                    self.task_manager.stuck((reason,))
                    self.messages[:] = request_messages
                    yield CompletionBlocked(attempt=1, reasons=(reason,))
                    yield TurnCompleted(
                        result=TurnResult(
                            text=reason,
                            usage=completed_usage,
                            last_request_usage=request_usage,
                            model_calls=iteration,
                            tool_calls=tuple(all_tool_calls),
                            status='stuck',
                            changed_paths=(),
                            verification=latest_verification,
                            completion_reasons=(reason,),
                        )
                    )
                    return
                if (
                    force_synthesis
                    and request_usage is not None
                ):
                    completed_usage = add_token_usage(
                        completed_usage,
                        request_usage,
                    )
                    reason = (
                        'The model returned no usable answer after ForgeCode '
                        'requested a final synthesis or completion recovery.'
                    )
                    reasons = (reason,)
                    self.task_manager.stuck(reasons)
                    self.messages[:] = request_messages
                    yield TurnCompleted(
                        result=TurnResult(
                            text=reason,
                            usage=completed_usage,
                            last_request_usage=request_usage,
                            model_calls=iteration,
                            tool_calls=tuple(all_tool_calls),
                            status='stuck',
                            changed_paths=(
                                self.workspace_tracker.changed_paths
                                if self.workspace_tracker is not None
                                else ()
                            ),
                            verification=latest_verification,
                            completion_reasons=reasons,
                        )
                    )
                    return
                if self.finish_protocol and request_usage is not None:
                    completed_usage = add_token_usage(
                        completed_usage,
                        request_usage,
                    )
                    reason = (
                        'The model returned no text or tool action, so the '
                        'trajectory cannot continue.'
                    )
                    self.task_manager.stuck((reason,))
                    self.messages[:] = request_messages
                    yield TurnCompleted(
                        result=TurnResult(
                            text=reason,
                            usage=completed_usage,
                            last_request_usage=request_usage,
                            model_calls=iteration,
                            tool_calls=tuple(all_tool_calls),
                            status='stuck',
                            changed_paths=(
                                self.workspace_tracker.changed_paths
                                if self.workspace_tracker is not None
                                else ()
                            ),
                            verification=latest_verification,
                            completion_reasons=(reason,),
                        )
                    )
                    return
                raise ModelResponseError(
                    'Model response did not contain any text or tool calls.'
                )
            if request_usage is None:
                raise ModelResponseError(
                    'Model response did not contain token usage.'
                )

            completed_usage = add_token_usage(
                completed_usage,
                request_usage,
            )
            tool_calls.sort(key=lambda call: call.index)
            request_messages.append(
                build_assistant_message(text, tool_calls)
            )
            if self.session_journal is not None:
                self.session_journal.record_assistant_message(
                    request_messages[-1]
                )

            if finalization_recovery and tool_calls:
                all_tool_calls.extend(tool_calls)
                reason = (
                    'The model requested another tool during the dedicated '
                    'finalization recovery instead of returning its final '
                    'evidence-based answer.'
                )
                self.task_manager.stuck((reason,))
                self.messages[:] = request_messages
                yield TurnCompleted(
                    result=TurnResult(
                        text=reason,
                        usage=completed_usage,
                        last_request_usage=request_usage,
                        model_calls=iteration,
                        tool_calls=tuple(all_tool_calls),
                        status='stuck',
                        changed_paths=(
                            self.workspace_tracker.changed_paths
                            if self.workspace_tracker is not None
                            else ()
                        ),
                        verification=latest_verification,
                        completion_reasons=(reason,),
                    )
                )
                return

            if not tool_calls:
                if mutation_failures:
                    if mutation_text_recoveries < 1:
                        mutation_text_recoveries += 1
                        force_synthesis = True
                        calls_without_progress = 0
                        request_messages.append(
                            build_mutation_text_retry_feedback(
                                mutation_failures,
                            )
                        )
                        continue
                    reason = (
                        f'Stopped after {mutation_failure_count} failed '
                        'workspace-write attempt(s) because the model '
                        'returned text without correcting the latest edit '
                        'failure.'
                    )
                    self.task_manager.stuck((reason,))
                    self.messages[:] = request_messages
                    yield TurnCompleted(
                        result=TurnResult(
                            text=reason,
                            usage=completed_usage,
                            last_request_usage=request_usage,
                            model_calls=iteration,
                            tool_calls=tuple(all_tool_calls),
                            status='stuck',
                            changed_paths=(
                                self.workspace_tracker.changed_paths
                                if self.workspace_tracker is not None
                                else ()
                            ),
                            verification=latest_verification,
                            completion_reasons=(reason,),
                        )
                    )
                    return
                incomplete_reasons = self_declared_incomplete_reasons(
                    complete_text,
                    require_tests=tests_required,
                )
                if change_required and incomplete_reasons:
                    incomplete_declaration_recoveries += 1
                    if incomplete_declaration_recoveries > 2:
                        reason = (
                            'The model repeatedly declared the requested '
                            'implementation incomplete after producing a Diff.'
                        )
                        self.task_manager.stuck((reason, *incomplete_reasons))
                        self.messages[:] = request_messages
                        yield TurnCompleted(
                            result=TurnResult(
                                text=complete_text,
                                usage=completed_usage,
                                last_request_usage=request_usage,
                                model_calls=iteration,
                                tool_calls=tuple(all_tool_calls),
                                status='stuck',
                                changed_paths=(
                                    self.workspace_tracker.changed_paths
                                    if self.workspace_tracker is not None
                                    else ()
                                ),
                                verification=latest_verification,
                                completion_reasons=(
                                    reason,
                                    *incomplete_reasons,
                                ),
                            )
                        )
                        return
                    finalization_recovery = False
                    verification_recovery = False
                    change_convergence_required = True
                    change_convergence_read_used = False
                    change_convergence_extra_reads = 0
                    post_mutation_convergence = False
                    force_synthesis = True
                    calls_without_progress = 0
                    request_messages.append(
                        build_incomplete_declaration_feedback(
                            incomplete_reasons,
                        )
                    )
                    continue
                if self._pending_required_change(change_required):
                    reason = required_change_block_reason()
                    self.task_manager.stuck((reason,))
                    self.messages[:] = request_messages
                    yield CompletionBlocked(attempt=1, reasons=(reason,))
                    yield TurnCompleted(
                        result=TurnResult(
                            text=reason,
                            usage=completed_usage,
                            last_request_usage=request_usage,
                            model_calls=iteration,
                            tool_calls=tuple(all_tool_calls),
                            status='stuck',
                            changed_paths=(),
                            verification=latest_verification,
                            completion_reasons=(reason,),
                        )
                    )
                    return
                if (
                    force_synthesis
                    and self.working_state.evidence_paths
                    and not self.working_state.answer_mentions_evidence(
                        complete_text
                    )
                ):
                    synthesis_retries += 1
                    reason = (
                        'The synthesis did not reference any collected '
                        'repository evidence.'
                    )
                    if synthesis_retries <= 1:
                        request_messages.append(
                            build_synthesis_retry_feedback(
                                self.task_manager.system_suffix(),
                                self.working_state.system_suffix(),
                            )
                        )
                        continue
                    self.task_manager.stuck((reason,))
                    self.messages[:] = request_messages
                    yield TurnCompleted(
                        result=TurnResult(
                            text=complete_text,
                            usage=completed_usage,
                            last_request_usage=request_usage,
                            model_calls=iteration,
                            tool_calls=tuple(all_tool_calls),
                            status='stuck',
                            changed_paths=(
                                self.workspace_tracker.changed_paths
                                if self.workspace_tracker is not None
                                else ()
                            ),
                            verification=latest_verification,
                            completion_reasons=(reason,),
                        )
                    )
                    return
                if (
                    self.workspace_tracker is not None
                    and self.completion_gate is not None
                ):
                    change = await self.workspace_tracker.refresh()
                    if change is not None:
                        self.working_state.advance_revision(
                            change.revision,
                            change.paths,
                        )
                        yield WorkspaceChanged(
                            revision=change.revision,
                            paths=change.paths,
                        )
                    decision = await self.completion_gate.evaluate(
                        self.workspace_tracker,
                        latest_verification,
                        mutation_attempted=(
                            mutation_attempted or change_required
                        ),
                        require_verification=verification_required,
                        require_tests=tests_required,
                        require_full_tests=full_tests_required,
                    )
                    if not decision.allowed:
                        yield CompletionBlocked(
                            attempt=1,
                            reasons=decision.reasons,
                        )
                        self.task_manager.stuck(decision.reasons)
                        self.messages[:] = request_messages
                        self.context.capture_explicit_memory(prompt)
                        yield TurnCompleted(
                            result=TurnResult(
                                text=complete_text,
                                usage=completed_usage,
                                last_request_usage=request_usage,
                                model_calls=iteration,
                                tool_calls=tuple(all_tool_calls),
                                status='stuck',
                                changed_paths=(
                                    self.workspace_tracker.changed_paths
                                ),
                                verification=latest_verification,
                                completion_reasons=decision.reasons,
                            )
                        )
                        return
                self.task_manager.complete()
                self.messages[:] = request_messages
                self.context.capture_explicit_memory(prompt)
                yield TurnCompleted(
                    result=TurnResult(
                        text=complete_text,
                        usage=completed_usage,
                        last_request_usage=request_usage,
                        model_calls=iteration,
                        tool_calls=tuple(all_tool_calls),
                        changed_paths=(
                            self.workspace_tracker.changed_paths
                            if self.workspace_tracker is not None
                            else ()
                        ),
                        verification=latest_verification,
                    )
                )
                return

            if self.registry is None:
                raise ModelResponseError(
                    'Model requested tools, but no ToolRegistry is configured.'
                )

            if (
                self.max_tool_calls is not None
                and len(all_tool_calls) + len(tool_calls) > self.max_tool_calls
            ):
                reason = (
                    'Stopped before executing more than '
                    f'{self.max_tool_calls} tool calls in one turn.'
                )
                self.task_manager.stuck((reason,))
                self.messages[:] = request_messages
                yield TurnCompleted(
                    result=TurnResult(
                        text=reason,
                        usage=completed_usage,
                        last_request_usage=request_usage,
                        model_calls=iteration,
                        tool_calls=tuple(all_tool_calls),
                        status='stuck',
                        changed_paths=(
                            self.workspace_tracker.changed_paths
                            if self.workspace_tracker is not None
                            else ()
                        ),
                        verification=latest_verification,
                        completion_reasons=(reason,),
                    )
                )
                return

            all_tool_calls.extend(tool_calls)
            tool_results: list[tuple[ToolCall, ToolResult]] = []
            last_workspace_change_position = -1
            last_workspace_write_change_position = -1
            task_progressed = False
            workspace_write_results: list[
                tuple[int, ToolCall, ToolResult, bool]
            ] = []
            accepted_finish: ToolResult | None = None
            terminal_finish_reasons: tuple[str, ...] = ()
            terminal_permission_denial: ToolResult | None = None
            for tool_position, tool_call in enumerate(tool_calls):
                finish_rejection: tuple[str, ...] = ()
                pre_tool = await self._emit_hook(
                    HookEvent(
                        name='PreToolUse',
                        session_id=self._session_id(),
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        arguments=tool_call.arguments,
                        paths=mutation_target_paths(
                            tool_call, maximum=None
                        ),
                    )
                )
                if pre_tool.arguments is not None:
                    tool_call = ToolCall(
                        index=tool_call.index,
                        id=tool_call.id,
                        name=tool_call.name,
                        arguments=pre_tool.arguments,
                    )
                self._queue_hook_context(pre_tool)
                tool_effect = self.registry.effect(tool_call.name)
                hook_rejection: ToolResult | None = None
                if not pre_tool.allowed:
                    hook_rejection = hook_denied_result(
                        'PreToolUse', pre_tool.reason
                    )
                if (
                    tool_effect == 'workspace_write'
                    and hook_rejection is None
                ):
                    before_edit = await self._emit_hook(
                        HookEvent(
                            name='BeforeFileEdit',
                            session_id=self._session_id(),
                            tool_name=tool_call.name,
                            tool_call_id=tool_call.id,
                            arguments=tool_call.arguments,
                            paths=mutation_target_paths(
                                tool_call, maximum=None
                            ),
                        )
                    )
                    if before_edit.arguments is not None:
                        tool_call = ToolCall(
                            index=tool_call.index,
                            id=tool_call.id,
                            name=tool_call.name,
                            arguments=before_edit.arguments,
                        )
                    self._queue_hook_context(before_edit)
                    if not before_edit.allowed:
                        hook_rejection = hook_denied_result(
                            'BeforeFileEdit', before_edit.reason
                        )
                permission_rejection: ToolResult | None = None
                if hook_rejection is None:
                    permission_request = (
                        self.registry.permission_request(
                            tool_call.name,
                            tool_call.arguments,
                        )
                        if self.registry is not None
                        else None
                    ) or classify_tool_call(tool_call, tool_effect)
                    permission_decision = await self.permission_manager.authorize(
                        permission_request
                    )
                    if permission_decision.action == 'deny':
                        permission_rejection = ToolResult.fail(
                            'permission_denied',
                            permission_decision.reason,
                            details={
                                'tool': tool_call.name,
                                'capability': permission_request.capability,
                                'risk': permission_request.risk,
                                'targets': list(permission_request.targets),
                                'source': permission_decision.source,
                            },
                        )
                if tool_effect == 'workspace_write':
                    mutation_attempted = True
                    change_required = True
                if (
                    tool_call.name == 'finish_task'
                    and tool_call.arguments.get('task_kind') == 'change'
                ):
                    change_required = True
                if (
                    tool_effect == 'workspace_write'
                    and self.workspace_tracker is not None
                ):
                    self.workspace_tracker.watch_paths(
                        mutation_target_paths(tool_call)
                    )
                convergence_read_call = (
                    not mutation_failures
                    and change_convergence_required
                    and tool_call.name in EDIT_RECOVERY_READ_TOOLS
                )
                convergence_read_blocked = (
                    convergence_read_call
                    and change_convergence_read_used
                    and change_convergence_extra_reads <= 0
                )
                if convergence_read_call and not convergence_read_blocked:
                    if change_convergence_read_used:
                        change_convergence_extra_reads -= 1
                    else:
                        change_convergence_read_used = True
                mutation_read_call = (
                    bool(mutation_failures)
                    and tool_call.name in EDIT_RECOVERY_READ_TOOLS
                )
                mutation_read_blocked = (
                    mutation_read_call and mutation_recovery_read_used
                )
                if mutation_read_call and not mutation_read_blocked:
                    mutation_recovery_read_used = True
                yield ToolExecutionStarted(tool_call=tool_call)
                revision = (
                    self.workspace_tracker.revision
                    if self.workspace_tracker is not None
                    else 0
                )
                signature = tool_call_signature(tool_call, revision)
                previous_count, previous_success = tool_attempts.get(
                    signature,
                    (0, True),
                )
                should_block_repeat = (
                    tool_call.name != 'finish_task'
                    and (
                        previous_count >= self.repeated_tool_limit
                        or (previous_count >= 1 and not previous_success)
                    )
                )
                finish_mixed = (
                    tool_call.name == 'finish_task' and len(tool_calls) != 1
                )
                semantic_repeat = self.working_state.preflight(
                    tool_call,
                    revision,
                    signature,
                )
                edit_phase_names = {
                    str(definition.get('name', ''))
                    for definition in (
                        self._edit_recovery_tools(read_available=False) or ()
                    )
                }
                phase_allowed_names = set(request_tool_names)
                phase_tool_unavailable = False
                if delegation_request_active:
                    phase_allowed_names = {'explore_repository'}
                    phase_tool_unavailable = (
                        tool_call.name != 'explore_repository'
                    )
                elif verification_recovery:
                    phase_allowed_names = {'verify'}
                    phase_tool_unavailable = tool_call.name != 'verify'
                elif mutation_failures:
                    phase_allowed_names = set(edit_phase_names)
                    if mutation_read_call and not mutation_read_blocked:
                        phase_allowed_names.add(tool_call.name)
                    phase_tool_unavailable = (
                        tool_call.name not in phase_allowed_names
                    )
                elif change_convergence_required:
                    phase_allowed_names = (
                        set(request_tool_names)
                        if post_mutation_convergence
                        else set(edit_phase_names)
                    )
                    if convergence_read_call and not convergence_read_blocked:
                        phase_allowed_names.add(tool_call.name)
                    phase_tool_unavailable = (
                        tool_call.name not in phase_allowed_names
                    )
                if hook_rejection is not None:
                    result = hook_rejection
                elif permission_rejection is not None:
                    result = permission_rejection
                elif convergence_read_blocked or mutation_read_blocked:
                    result = ToolResult.fail(
                        'recovery_read_already_used',
                        'The targeted read/search allowance for this recovery '
                        'phase is exhausted. Use the collected results and '
                        'make a corrected workspace edit now.',
                    )
                elif finish_mixed:
                    result = ToolResult.fail(
                        'finish_must_be_alone',
                        'finish_task must be the only tool call in its model '
                        'response. Complete other actions first, then declare '
                        'the outcome in a separate response.',
                    )
                elif phase_tool_unavailable:
                    result = ToolResult.fail(
                        'tool_not_available_in_phase',
                        f'{tool_call.name} is not available in the current '
                        'recovery phase. Use one of the tools included with '
                        'this request.',
                        details={
                            'available_tools': sorted(phase_allowed_names),
                        },
                    )
                elif should_block_repeat:
                    result = repeated_tool_result(
                        tool_call,
                        previous_count,
                        previous_success=previous_success,
                    )
                elif semantic_repeat is not None:
                    result = semantic_repeat
                    tool_attempts[signature] = (
                        previous_count + 1,
                        result.success,
                    )
                else:
                    checkpoint_paths = (
                        mutation_target_paths(tool_call, maximum=None)
                        if tool_effect == 'workspace_write'
                        and checkpoint_id is not None
                        and self.checkpoint_store is not None
                        else ()
                    )
                    try:
                        if checkpoint_paths:
                            self.checkpoint_store.capture_before(
                                checkpoint_id,
                                checkpoint_paths,
                            )
                        result = await self.registry.execute(
                            tool_call.name,
                            tool_call.arguments,
                        )
                        if (
                            tool_effect == 'workspace_write'
                            and result.success
                        ):
                            after_edit = await self._emit_hook(
                                HookEvent(
                                    name='AfterFileEdit',
                                    session_id=self._session_id(),
                                    tool_name=tool_call.name,
                                    tool_call_id=tool_call.id,
                                    arguments=tool_call.arguments,
                                    paths=mutation_target_paths(
                                        tool_call, maximum=None
                                    ),
                                    payload={
                                        'success': result.success,
                                        'summary': result.summary,
                                    },
                                )
                            )
                            self._queue_hook_context(after_edit)
                        if checkpoint_paths:
                            self.checkpoint_store.record_after(
                                checkpoint_id,
                                checkpoint_paths,
                            )
                    except CheckpointError as error:
                        result = ToolResult.fail(
                            'checkpoint_failed',
                            'ForgeCode refused the workspace edit because '
                            'its pre-edit checkpoint could not be created.',
                            content=str(error),
                        )
                    if tool_call.name != 'finish_task':
                        tool_attempts[signature] = (
                            previous_count + 1,
                            result.success,
                        )
                if tool_call.name == 'finish_task' and result.success:
                    finish_reasons = await self._finish_rejection_reasons(
                        result,
                        mutation_attempted=mutation_attempted,
                        change_required=change_required,
                        verification=latest_verification,
                        verification_required=verification_required,
                        tests_required=tests_required,
                        full_tests_required=full_tests_required,
                    )
                    if (
                        result.metadata.get('status') != 'blocked'
                        and mutation_failures
                    ):
                        finish_reasons = (
                            'A workspace-write failure is still unresolved. '
                            'Produce a real workspace revision that clears '
                            'Edit Recovery before declaring completion.',
                            *finish_reasons,
                        )
                        finish_reasons = tuple(
                            dict.fromkeys(finish_reasons)
                        )
                    if finish_reasons:
                        finish_rejection = finish_reasons
                        result = ToolResult.fail(
                            'finish_rejected',
                            'The finish_task declaration did not match the '
                            'available execution evidence.',
                            details={'reasons': list(finish_reasons)},
                        )
                        terminal_finish_reasons = finish_reasons
                    else:
                        accepted_finish = result
                post_tool = await self._emit_hook(
                    HookEvent(
                        name='PostToolUse',
                        session_id=self._session_id(),
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        arguments=tool_call.arguments,
                        paths=mutation_target_paths(
                            tool_call, maximum=None
                        ),
                        payload={
                            'success': result.success,
                            'summary': result.summary,
                            'error_code': (
                                result.error.code
                                if result.error is not None
                                else None
                            ),
                        },
                    )
                )
                self._queue_hook_context(post_tool)
                self.working_state.observe(
                    tool_call,
                    result,
                    revision,
                    signature,
                )
                tool_results.append((tool_call, result))
                yield ToolExecutionCompleted(
                    tool_call=tool_call,
                    result=result,
                )
                if (
                    result.error is not None
                    and result.error.code == 'permission_denied'
                ):
                    terminal_permission_denial = result
                    break
                if finish_rejection:
                    yield CompletionBlocked(
                        attempt=1,
                        reasons=finish_rejection,
                    )
                tool_changed_workspace = False
                if self.workspace_tracker is not None:
                    change = await self.workspace_tracker.refresh()
                    if change is not None:
                        tool_changed_workspace = True
                        last_workspace_change_position = tool_position
                        if tool_effect == 'workspace_write' and result.success:
                            last_workspace_write_change_position = tool_position
                        self.working_state.advance_revision(
                            change.revision,
                            change.paths,
                        )
                        if tool_effect == 'process':
                            mutation_attempted = True
                        yield WorkspaceChanged(
                            revision=change.revision,
                            paths=change.paths,
                        )
                elif tool_effect == 'workspace_write' and result.success:
                    tool_changed_workspace = True
                    last_workspace_change_position = tool_position
                    last_workspace_write_change_position = tool_position
                if tool_effect == 'workspace_write':
                    workspace_write_results.append(
                        (
                            tool_position,
                            tool_call,
                            result,
                            tool_changed_workspace,
                        )
                    )
                if tool_call.name == 'verify':
                    latest_verification = verification_from_result(result)
                    verification_command = str(
                        tool_call.arguments.get('command', '')
                    )
                    command_runs_tests = verification_command_runs_tests(
                        verification_command
                    )
                    verification_contract_satisfied = bool(
                        latest_verification is not None
                        and latest_verification.success
                        and (
                            not tests_required
                            or verification_command_runs_tests(
                                verification_command
                            )
                        )
                        and (
                            not full_tests_required
                            or verification_command_runs_full_suite(
                                verification_command
                            )
                        )
                    )
                    verification_recovery = bool(
                        latest_verification is not None
                        and latest_verification.success
                        and tests_required
                        and command_runs_tests
                        and not verification_contract_satisfied
                    )
                    if (
                        post_mutation_convergence
                        and verification_contract_satisfied
                    ):
                        post_mutation_convergence = False
                        change_convergence_required = False
                        change_convergence_read_used = False
                        change_convergence_extra_reads = 0
                        force_synthesis = False
                    if (
                        latest_verification is not None
                        and not latest_verification.success
                        and self.workspace_tracker is not None
                        and self.workspace_tracker.changed_paths
                    ):
                        change_convergence_required = True
                        change_convergence_read_used = False
                        change_convergence_extra_reads = 0
                        post_mutation_convergence = False
                        force_synthesis = True
                    if latest_verification is not None:
                        verification_hook = await self._emit_hook(
                            HookEvent(
                                name='AfterVerification',
                                session_id=self._session_id(),
                                tool_name=tool_call.name,
                                tool_call_id=tool_call.id,
                                arguments=tool_call.arguments,
                                payload={
                                    'success': latest_verification.success,
                                    'command': latest_verification.command,
                                    'exit_code': (
                                        latest_verification.exit_code
                                    ),
                                    'timed_out': (
                                        latest_verification.timed_out
                                    ),
                                    'workspace_revision': (
                                        latest_verification.workspace_revision
                                    ),
                                },
                            )
                        )
                        self._queue_hook_context(verification_hook)
                        yield VerificationCompleted(
                            evidence=latest_verification
                        )
                if tool_call.name == 'explore_repository':
                    exploration_delegation_pending = False
                    task_progressed = True
                    if result.success and change_required:
                        change_convergence_required = True
                        change_convergence_read_used = False
                        # Cross-file Explore reports identify targets but do not
                        # carry complete editable source. Permit eight focused
                        # parent reads before requiring the first mutation.
                        change_convergence_extra_reads = 7
                        post_mutation_convergence = False
                        force_synthesis = True
                if tool_call.name == 'task_update' and result.success:
                    task_progressed = True
            if terminal_permission_denial is not None:
                for skipped_call in tool_calls[len(tool_results):]:
                    tool_results.append(
                        (
                            skipped_call,
                            ToolResult.fail(
                                'not_executed_after_permission_denial',
                                'Not executed because an earlier tool call was denied.',
                            ),
                        )
                    )
            request_messages.append(build_tool_result_message(tool_results))
            if any(
                tool_call.name == 'explore_repository' and result.success
                for tool_call, result in tool_results
            ):
                request_messages.append(build_explore_handoff_feedback())
            if self.session_journal is not None:
                self.session_journal.record_tool_result_message(
                    request_messages[-1],
                    self.task_manager.active,
                )
            failed_verification = next(
                (
                    result
                    for tool_call, result in reversed(tool_results)
                    if tool_call.name == 'verify' and not result.success
                ),
                None,
            )
            if failed_verification is not None:
                request_messages.append(
                    build_verification_failure_feedback(failed_verification)
                )

            if terminal_permission_denial is not None:
                reason = self._permission_denial_message(
                    terminal_permission_denial
                )
                reasons = (reason,)
                self.task_manager.block(reasons)
                self.messages[:] = request_messages
                yield TurnCompleted(
                    result=TurnResult(
                        text=reason,
                        usage=completed_usage,
                        last_request_usage=request_usage,
                        model_calls=iteration,
                        tool_calls=tuple(all_tool_calls),
                        status='blocked',
                        changed_paths=(
                            self.workspace_tracker.changed_paths
                            if self.workspace_tracker is not None
                            else ()
                        ),
                        verification=latest_verification,
                        completion_reasons=reasons,
                    )
                )
                return

            if terminal_finish_reasons:
                self.task_manager.stuck(terminal_finish_reasons)
                self.messages[:] = request_messages
                yield TurnCompleted(
                    result=TurnResult(
                        text=(
                            'ForgeCode rejected the completion declaration '
                            'because it did not match the current evidence.'
                        ),
                        usage=completed_usage,
                        last_request_usage=request_usage,
                        model_calls=iteration,
                        tool_calls=tuple(all_tool_calls),
                        status='stuck',
                        changed_paths=(
                            self.workspace_tracker.changed_paths
                            if self.workspace_tracker is not None
                            else ()
                        ),
                        verification=latest_verification,
                        completion_reasons=terminal_finish_reasons,
                    )
                )
                return

            if accepted_finish is not None:
                declaration_status = str(
                    accepted_finish.metadata['status']
                )
                summary = str(accepted_finish.metadata['summary'])
                blocked_reasons = tuple(
                    str(reason)
                    for reason in accepted_finish.metadata.get(
                        'blocked_reasons',
                        [],
                    )
                )
                if declaration_status == 'blocked':
                    self.task_manager.block(blocked_reasons)
                else:
                    self.task_manager.complete()
                self.messages[:] = request_messages
                self.context.capture_explicit_memory(prompt)
                yield TurnCompleted(
                    result=TurnResult(
                        text=summary,
                        usage=completed_usage,
                        last_request_usage=request_usage,
                        model_calls=iteration,
                        tool_calls=tuple(all_tool_calls),
                        status=(
                            'blocked'
                            if declaration_status == 'blocked'
                            else 'completed'
                        ),
                        changed_paths=(
                            self.workspace_tracker.changed_paths
                            if self.workspace_tracker is not None
                            else ()
                        ),
                        verification=latest_verification,
                        completion_reasons=blocked_reasons,
                    )
                )
                return

            workspace_progressed = last_workspace_change_position >= 0
            workspace_write_progressed = (
                last_workspace_write_change_position >= 0
            )
            batch_reverted_to_baseline = (
                workspace_progressed
                and self.workspace_tracker is not None
                and not self.workspace_tracker.changed_paths
            )
            if batch_reverted_to_baseline:
                workspace_progressed = False
                workspace_write_progressed = False
            if workspace_write_progressed:
                mutation_failure_count = 0
                mutation_failure_total = 0
                mutation_failure_targets = ()
                mutation_failures.clear()
                mutation_recovery_read_used = False
                mutation_recovery_context = ''
                mutation_text_recoveries = 0
                force_synthesis = False
                synthesis_retries = 0
                completion_ready_revision = None
                completion_decision_calls = 0
                completion_ready_context = ''
                completion_reviewed_paths.clear()
                # A successful write can be only one part of a larger change.
                # Keep implementation tools available; the stagnation and
                # completion paths enforce fresh verification before finish.
                verification_recovery = False
            pending_write_results = [
                (call, result)
                for position, call, result, changed
                in workspace_write_results
                if (
                    position > last_workspace_write_change_position
                    and not changed
                    and not is_tool_protocol_failure(result)
                    and (
                        result.error is None
                        or result.error.code
                        not in {'permission_denied', 'repeated_tool_call'}
                    )
                )
            ]
            if batch_reverted_to_baseline and workspace_write_results:
                _, last_call, last_result, _ = workspace_write_results[-1]
                pending_write_results = [(last_call, last_result)]
            if pending_write_results:
                mutation_recovery_read_used = False
                mutation_text_recoveries = 0
                current_failure_targets = tuple(
                    sorted(
                        {
                            target
                            for failed_call, _ in pending_write_results
                            for target in (
                                mutation_target_paths(
                                    failed_call,
                                    maximum=None,
                                )
                                or (f'@tool:{failed_call.name}',)
                            )
                        }
                    )
                )
                if current_failure_targets != mutation_failure_targets:
                    mutation_failure_count = 0
                    mutation_failures.clear()
                mutation_failure_targets = current_failure_targets
                mutation_failure_count += len(pending_write_results)
                mutation_failure_total += len(pending_write_results)
                for failed_call, failed_result in pending_write_results:
                    mutation_failures.append(
                        mutation_failure_record(
                            failed_call,
                            failed_result,
                        )
                    )
                mutation_failures = mutation_failures[-3:]
            if mutation_failures:
                mutation_recovery_context = (
                    render_mutation_recovery_context(
                        mutation_failures,
                        mutation_failure_total,
                    )
                )
                if workspace_write_results:
                    request_messages.append(
                        build_mutation_recovery_feedback(
                            mutation_failures,
                            mutation_failure_total,
                            self.task_manager.system_suffix(),
                        )
                    )
                if (
                    mutation_failure_count >= self.mutation_recovery_limit
                    or mutation_failure_total
                    >= self.mutation_recovery_limit * 2
                ):
                    reason = mutation_recovery_stuck_reason(
                        mutation_failures,
                        mutation_failure_total,
                    )
                    self.task_manager.stuck((reason,))
                    self.messages[:] = request_messages
                    yield TurnCompleted(
                        result=TurnResult(
                            text=reason,
                            usage=completed_usage,
                            last_request_usage=request_usage,
                            model_calls=iteration,
                            tool_calls=tuple(all_tool_calls),
                            status='stuck',
                            changed_paths=(
                                self.workspace_tracker.changed_paths
                                if self.workspace_tracker is not None
                                else ()
                            ),
                            verification=latest_verification,
                            completion_reasons=(reason,),
                        )
                    )
                    return
            protocol_failure = bool(tool_results) and all(
                is_tool_protocol_failure(result)
                for _, result in tool_results
            )
            if protocol_failure:
                tool_protocol_failures += 1
            elif any(result.success for _, result in tool_results):
                tool_protocol_failures = 0
            completion_ready = (
                not protocol_failure
                and await self._can_finalize_after_stagnation(
                    mutation_attempted=mutation_attempted,
                    verification=latest_verification,
                    mutation_failures=mutation_failures,
                    verification_required=verification_required,
                    tests_required=tests_required,
                    full_tests_required=full_tests_required,
                )
            )
            if completion_ready:
                if self.workspace_tracker is None:
                    raise AssertionError(
                        'Completion readiness requires a workspace tracker.'
                    )
                revision = self.workspace_tracker.revision
                new_ready_revision = completion_ready_revision != revision
                if new_ready_revision:
                    completion_ready_revision = revision
                    completion_decision_calls = 0
                    completion_reviewed_paths.clear()
                    force_synthesis = False
                    synthesis_retries = 0
                reviewed_now = completion_review_paths(
                    tool_results,
                    self.workspace_tracker.changed_paths,
                )
                new_reviews = reviewed_now - completion_reviewed_paths
                completion_reviewed_paths.update(reviewed_now)
                if not new_ready_revision and not new_reviews:
                    completion_decision_calls += 1
                completion_ready_context = render_completion_ready_context(
                    self.workspace_tracker.changed_paths,
                    latest_verification,
                    completion_decision_calls,
                    self.completion_decision_limit,
                    completion_reviewed_paths,
                )
                calls_without_progress = 0
                if (
                    completion_decision_calls
                    >= self.completion_decision_limit
                ):
                    finalization_recovery = True
                    force_synthesis = True
                    request_messages.append(
                        build_finalization_recovery_feedback(
                            self.task_manager.system_suffix(),
                            self.working_state.system_suffix(),
                            self.workspace_tracker.changed_paths,
                            latest_verification,
                        )
                    )
                continue
            completion_ready_revision = None
            completion_decision_calls = 0
            completion_ready_context = ''
            completion_reviewed_paths.clear()
            if workspace_progressed or task_progressed:
                calls_without_progress = 0
                if workspace_progressed and change_required:
                    change_convergence_required = True
                    change_convergence_read_used = False
                    change_convergence_extra_reads = 3
                    post_mutation_convergence = True
                    force_synthesis = True
                    request_messages.append(
                        build_post_mutation_convergence_feedback()
                    )
                elif not change_convergence_required:
                    force_synthesis = False
                    post_mutation_convergence = False
                    change_convergence_read_used = False
                    change_convergence_extra_reads = 0
                synthesis_retries = 0
            elif protocol_failure:
                # Malformed tool arguments are a protocol-recovery problem,
                # not evidence that the task itself is stuck.
                request_messages.append(
                    build_tool_protocol_feedback(
                        tool_protocol_failures,
                        self.task_manager.system_suffix(),
                        tool_results,
                    )
                )
                if (
                    tool_protocol_failures
                    >= self.max_tool_protocol_recoveries
                ):
                    reason = (
                        'Stopped after repeated malformed or schema-invalid '
                        'tool requests. The repository task may still be '
                        'solvable, but this agent trajectory is stuck.'
                    )
                    self.task_manager.stuck((reason,))
                    self.messages[:] = request_messages
                    yield TurnCompleted(
                        result=TurnResult(
                            text=reason,
                            usage=completed_usage,
                            last_request_usage=request_usage,
                            model_calls=iteration,
                            tool_calls=tuple(all_tool_calls),
                            status='stuck',
                            changed_paths=(
                                self.workspace_tracker.changed_paths
                                if self.workspace_tracker is not None
                                else ()
                            ),
                            verification=latest_verification,
                            completion_reasons=(reason,),
                        )
                    )
                    return
                # Protocol recovery owns this iteration. Do not let a stale
                # global stagnation count pre-empt the corrected retry.
                continue
            elif mutation_failures:
                # Edit Recovery exclusively owns progress limits while a
                # workspace-write failure remains unresolved. Reads and
                # searches may guide the corrected edit without also
                # consuming the global Stagnation budget.
                calls_without_progress = 0
            else:
                calls_without_progress += 1
            if calls_without_progress == self.stagnation_warning:
                force_synthesis = True
                request_messages.append(
                    build_stagnation_feedback(
                        calls_without_progress,
                        self.task_manager.system_suffix(),
                        self.working_state.system_suffix(),
                    )
                )
            elif calls_without_progress >= self.stagnation_limit:
                if (
                    not change_required
                    and not mutation_attempted
                    and not finalization_recovery
                ):
                    finalization_recovery = True
                    force_synthesis = True
                    calls_without_progress = 0
                    request_messages.append(
                        {
                            'role': 'user',
                            'content': (
                                'ForgeCode read-only synthesis checkpoint: '
                                'enough repository evidence has been collected. '
                                'Tools are now closed; answer the user directly '
                                'from the existing evidence without more lookup.'
                            ),
                        }
                    )
                    continue
                pending_required_change = self._pending_required_change(
                    change_required
                )
                if (
                    pending_required_change
                    and not mutation_attempted
                    and calls_without_progress
                    < max(
                        self.stagnation_limit,
                        self.change_exploration_limit,
                    )
                ):
                    if calls_without_progress == self.stagnation_limit:
                        request_messages.append(
                            build_stagnation_feedback(
                                calls_without_progress,
                                self.task_manager.system_suffix(),
                                self.working_state.system_suffix(),
                            )
                        )
                    continue
                if (
                    pending_required_change
                    and not mutation_attempted
                    and not change_convergence_required
                ):
                    change_convergence_required = True
                    change_convergence_read_used = False
                    change_convergence_extra_reads = 0
                    post_mutation_convergence = False
                    force_synthesis = True
                    calls_without_progress = 0
                    request_messages.append(
                        build_change_convergence_feedback(
                            self.task_manager.system_suffix(),
                            self.working_state.system_suffix(),
                        )
                    )
                    continue
                tracker = self.workspace_tracker
                verification_current = bool(
                    tracker is not None
                    and latest_verification is not None
                    and latest_verification.success
                    and latest_verification.workspace_revision
                    == tracker.revision
                    and (
                        not tests_required
                        or verification_command_runs_tests(
                            latest_verification.command
                        )
                    )
                    and (
                        not full_tests_required
                        or verification_command_runs_full_suite(
                            latest_verification.command
                        )
                    )
                )
                if (
                    verification_required
                    and tracker is not None
                    and tracker.changed_paths
                    and not verification_current
                    and not verification_recovery
                ):
                    verification_recovery = True
                    force_synthesis = False
                    calls_without_progress = 0
                    request_messages.append(
                        {
                            'role': 'user',
                            'content': (
                                'ForgeCode verification checkpoint: a real Diff '
                                'exists, but the user-requested verification is '
                                'missing or stale. Run the verify tool now on '
                                'the current workspace revision.'
                            ),
                        }
                    )
                    continue
                if await self._can_finalize_after_stagnation(
                    mutation_attempted=mutation_attempted,
                    verification=latest_verification,
                    mutation_failures=mutation_failures,
                    verification_required=verification_required,
                    tests_required=tests_required,
                    full_tests_required=full_tests_required,
                ):
                    finalization_recovery = True
                    force_synthesis = True
                    request_messages.append(
                        build_finalization_recovery_feedback(
                            self.task_manager.system_suffix(),
                            self.working_state.system_suffix(),
                            self.workspace_tracker.changed_paths,
                            latest_verification,
                        )
                    )
                    continue
                reason = (
                    'Stopped after '
                    f'{calls_without_progress} model calls without a workspace '
                    'change or task-state transition.'
                )
                self.task_manager.stuck((reason,))
                self.messages[:] = request_messages
                yield TurnCompleted(
                    result=TurnResult(
                        text=reason,
                        usage=completed_usage,
                        last_request_usage=request_usage,
                        model_calls=iteration,
                        tool_calls=tuple(all_tool_calls),
                        status='stuck',
                        changed_paths=(
                            self.workspace_tracker.changed_paths
                            if self.workspace_tracker is not None
                            else ()
                        ),
                        verification=latest_verification,
                        completion_reasons=(reason,),
                    )
                )
                return

        if self.max_iterations is not None:
            reason = (
                f'Stopped after reaching the per-turn limit of '
                f'{self.max_iterations} model calls.'
            )
            self.task_manager.stuck((reason,))
            self.messages[:] = request_messages
            yield TurnCompleted(
                result=TurnResult(
                    text=reason,
                    usage=completed_usage,
                    model_calls=self.max_iterations,
                    tool_calls=tuple(all_tool_calls),
                    status='stuck',
                    changed_paths=(
                        self.workspace_tracker.changed_paths
                        if self.workspace_tracker is not None
                        else ()
                    ),
                    verification=latest_verification,
                    completion_reasons=(reason,),
                )
            )
            return
        raise AssertionError('Unlimited Agent Loop stopped unexpectedly.')

    def _system_prompt_with_task(
        self,
        *,
        include_tool_availability: bool = True,
    ) -> str:
        task_context = self.task_manager.system_suffix()
        self._last_task_context = task_context
        parts = [self.system_prompt]
        if task_context:
            parts.append(task_context)
        working_context = self.working_state.system_suffix()
        if working_context:
            parts.append(working_context)
        if self._tool_definitions() and include_tool_availability:
            parts.append(
                '[Runtime Tool Availability]\n'
                'The tools included with this model request are currently '
                'available. Decide from the user goal whether to answer, '
                'inspect, modify, or verify. If earlier conversation text '
                'claimed tools were unavailable, that claim is stale for '
                'this request. Use tools directly whenever your chosen '
                'approach requires repository actions.'
            )
        return '\n\n'.join(parts)

    def _pending_required_change(self, change_required: bool) -> bool:
        tracker = self.workspace_tracker
        return bool(
            change_required
            and tracker is not None
            and getattr(tracker, 'available', True)
            and not tracker.changed_paths
        )

    def _verification_tools(
        self,
        *,
        require_tests: bool,
    ) -> list[dict[str, Any]] | None:
        definitions = self._tool_definitions()
        if definitions is None:
            return None
        selected = [
            dict(definition)
            for definition in definitions
            if str(definition.get('name', '')) == 'verify'
        ]
        if require_tests:
            for definition in selected:
                definition['description'] = (
                    'Run the repository test suite now. For this ForgeCode '
                    'Python repository, command MUST invoke pytest; use '
                    '`uv run pytest -q` for the full suite or an explicit '
                    '`uv run pytest -q <test paths>` focused command. Do not '
                    'use git status, git diff, sed, grep, or Python file-reading '
                    'scripts as verification.'
                )
        return selected

    def _edit_recovery_tools(
        self,
        *,
        read_available: bool,
    ) -> list[dict[str, Any]] | None:
        definitions = self._tool_definitions()
        if self.registry is None or definitions is None:
            return definitions
        definitions = list(definitions)
        exact_replace = self.registry.definition('replace_text')
        if (
            exact_replace is not None
            and not any(
                str(item.get('name', '')) == 'replace_text'
                for item in definitions
            )
        ):
            definitions.append(exact_replace)
        return [
            definition
            for definition in definitions
            if (
                read_available
                and str(definition.get('name', ''))
                in EDIT_RECOVERY_READ_TOOLS
            )
            or (
                str(definition.get('name', '')) != 'write_file'
                and self.registry.effect(
                    str(definition.get('name', ''))
                ) == 'workspace_write'
            )
        ]

    def _post_mutation_tools(
        self,
        *,
        read_available: bool,
    ) -> list[dict[str, Any]] | None:
        '''Expose only focused continuation edits plus deterministic verification.'''
        selected = self._edit_recovery_tools(
            read_available=read_available,
        )
        definitions = self._tool_definitions()
        if selected is None or definitions is None:
            return selected
        selected_names = {
            str(definition.get('name', '')) for definition in selected
        }
        for definition in definitions:
            name = str(definition.get('name', ''))
            if (
                name in {'verify', 'finish_task'}
                and name not in selected_names
            ):
                selected.append(dict(definition))
                selected_names.add(name)
        return selected

    async def _finish_rejection_reasons(
        self,
        result: ToolResult,
        *,
        mutation_attempted: bool,
        change_required: bool,
        verification: VerificationEvidence | None,
        verification_required: bool,
        tests_required: bool,
        full_tests_required: bool,
    ) -> tuple[str, ...]:
        metadata = result.metadata
        if metadata.get('status') == 'blocked':
            if self.working_state.has_external_blocker:
                return ()
            return (
                'blocked is reserved for an external condition that requires '
                'user action, permission, credentials, or an unavailable '
                'dependency. Repeated reads, malformed arguments, lack of '
                'progress, and ForgeCode recovery guidance are not blockers; '
                'continue with the available tools.',
            )
        task_kind = str(metadata.get('task_kind', ''))
        reasons: list[str] = []
        changed_paths = (
            self.workspace_tracker.changed_paths
            if self.workspace_tracker is not None
            else ()
        )
        if change_required and task_kind != 'change' and not changed_paths:
            reasons.append(
                'This turn requires a real task-local workspace change. '
                'Inspection or answer completion cannot satisfy it while '
                'the task-local Diff is empty.'
            )
        if task_kind == 'inspection' and not self.working_state.evidence_paths:
            reasons.append(
                'An inspection task requires repository evidence from '
                'read_file, list_directory, grep, or find_files.'
            )
        if task_kind != 'change' and changed_paths:
            reasons.append(
                'The workspace changed during this turn; declare '
                'task_kind=change and provide current verification evidence.'
            )
        if task_kind == 'change':
            if self.workspace_tracker is None or self.completion_gate is None:
                reasons.append(
                    'Workspace tracking is unavailable, so a change outcome '
                    'cannot be verified.'
                )
            else:
                decision = await self.completion_gate.evaluate(
                    self.workspace_tracker,
                    verification,
                    mutation_attempted=True,
                    require_verification=verification_required,
                    require_tests=tests_required,
                    require_full_tests=full_tests_required,
                )
                reasons.extend(decision.reasons)
        elif mutation_attempted and not changed_paths:
            reasons.append(
                'A workspace write was attempted but produced no final Diff; '
                'continue or declare the task blocked.'
            )
        return tuple(dict.fromkeys(reasons))

    def _request_system_prompt(
        self,
        *,
        force_synthesis: bool = False,
        mutation_recovery_context: str = '',
        finalization_recovery: bool = False,
        completion_ready_context: str = '',
        change_required: bool = False,
        mutation_attempted: bool = False,
        verification_required: bool = False,
        tests_required: bool = False,
        full_tests_required: bool = False,
        verification_recovery: bool = False,
    ) -> str:
        prompt = self._system_prompt_with_task(
            include_tool_availability=not finalization_recovery,
        )
        prompt += '\n\n' + self._permission_system_context()
        if self._last_repository_context:
            prompt += '\n\n' + self._last_repository_context
        if change_required:
            prompt += '\n\n' + render_change_contract_context(
                (
                    self.workspace_tracker.changed_paths
                    if self.workspace_tracker is not None
                    else ()
                ),
                mutation_attempted=mutation_attempted,
            )
        if verification_required:
            prompt += (
                '\n\n[ForgeCode Verification Contract]\n'
                'The user explicitly requested verification. Before declaring '
                'completion, run the verify tool with the focused checks they '
                'requested, then run the full test suite when requested. The '
                'latest verification must succeed on the final workspace '
                'revision; a summary without verification will be rejected.'
            )
        if tests_required:
            prompt += (
                '\nThe request explicitly requires tests. A VCS inspection '
                'such as git status or git diff --check does not satisfy this '
                'contract; invoke the repository test runner (for this Python '
                'project, use pytest).'
            )
        if full_tests_required:
            prompt += (
                '\nThe user explicitly requires the full test suite. Focused '
                'test paths, pytest -k/-m selections, and collect/version '
                'commands do not satisfy completion; the latest verification '
                'must be an unscoped full-suite command such as '
                '`uv run pytest -q`.'
            )
        if mutation_recovery_context:
            prompt += '\n\n' + mutation_recovery_context
        if completion_ready_context:
            prompt += '\n\n' + completion_ready_context
        if verification_recovery:
            prompt += (
                '\n\n[ForgeCode Verification Recovery]\n'
                'A real task-local Diff exists, but the user-requested checks '
                'have not succeeded on the current revision. This request '
                'exposes only the verify tool. Run the focused and full checks '
                'requested by the user now; do not return a summary or request '
                'another repository inspection.'
            )
        elif finalization_recovery:
            prompt += (
                '\n\n[ForgeCode Finalization Recovery]\n'
                'The current workspace revision already has a real Diff and '
                'current successful verification. This is a dedicated final '
                'synthesis request, so no tools are included. Return one '
                'concise final answer in the user\'s language based only on '
                'the collected evidence. State what changed and the exact '
                'verification performed. Be honest about anything that was '
                'not semantically or visually verified. Do not request or '
                'describe another tool call.'
            )
        elif force_synthesis:
            prompt += (
                '\n\n[ForgeCode Recovery Checkpoint]\n'
                'Recent actions did not produce new evidence or workspace '
                'changes. All listed tools remain available. Reassess the '
                'root goal and existing evidence, then choose a materially '
                'different action. Paths marked as fully covered already have '
                'model-visible or replayable evidence, so do not re-read them '
                'with different line ranges. If your judgment is that the '
                'user goal requires a code change and the Diff is still empty, '
                'use an editing tool once the relevant code is understood. '
                'If exact evidence is missing, perform one targeted search. '
                'If the goal is already satisfied, return a concise final '
                'answer or call finish_task. Do not claim that ForgeCode '
                'paused repository tools.'
            )
        return prompt

    def _permission_filtered_tools(
        self,
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        '''Hide effectful tools from model requests while in Plan mode.'''
        if (
            tools is None
            or self.permission_manager.mode != 'plan'
            or self.registry is None
        ):
            return tools
        return [
            definition
            for definition in tools
            if self.registry.effect(str(definition.get('name', '')))
            == 'read_only'
        ]

    def _permission_system_context(self) -> str:
        mode = self.permission_manager.mode
        if mode == 'plan':
            return (
                '[ForgeCode Permission Mode]\n'
                'Current mode: plan. You may inspect and analyze the '
                'repository, but you must not request file-writing, patching, '
                'command-execution, verification, deletion, installation, or '
                'other effectful tools. Those tools are intentionally absent. '
                'When the user asks for a change, provide the plan or analysis '
                'they requested and clearly tell them to switch with '
                '`/permission supervised` or `/permission auto` before asking '
                'you to implement it.'
            )
        return (
            '[ForgeCode Permission Mode]\n'
            f'Current mode: {mode}. Tool calls remain subject to the active '
            'permission rules and approval policy.'
        )

    def _permission_denial_message(self, result: ToolResult) -> str:
        details = result.error.details if result.error is not None else {}
        source = str(details.get('source', 'permission'))
        if source == 'plan' or self.permission_manager.mode == 'plan':
            return (
                '当前处于 Plan 模式，ForgeCode 已阻止写入或命令执行，并停止'
                '本轮任务。请使用 `/permission supervised`（逐次审批）或 '
                '`/permission auto`（自动执行低风险操作）切换权限后再继续。'
            )
        reason = (
            result.error.message
            if result.error is not None
            else 'Permission denied.'
        )
        return f'操作未获授权，本轮已停止：{reason}'

    async def _can_finalize_after_stagnation(
        self,
        *,
        mutation_attempted: bool,
        verification: VerificationEvidence | None,
        mutation_failures: list[dict[str, Any]],
        verification_required: bool,
        tests_required: bool,
        full_tests_required: bool,
    ) -> bool:
        '''Enter synthesis only for a mechanically complete current revision.'''
        tracker = self.workspace_tracker
        gate = self.completion_gate
        if (
            tracker is None
            or gate is None
            or not tracker.changed_paths
            or mutation_failures
        ):
            return False
        task = self.task_manager.active
        if task is not None and task.planned and any(
            step.status != 'completed' for step in task.steps
        ):
            return False
        decision = await gate.evaluate(
            tracker,
            verification,
            mutation_attempted=mutation_attempted,
            require_verification=verification_required,
            require_tests=tests_required,
            require_full_tests=full_tests_required,
        )
        return decision.allowed

    async def compact(self) -> CompactionReport:
        '''Manually summarize committed history for the /compact command.'''
        if not self.messages:
            return CompactionReport(
                success=True,
                automatic=False,
                before_characters=0,
                after_characters=0,
                transcript_path=None,
                reason='conversation history is empty',
            )
        before_compact = await self._emit_hook(
            HookEvent(
                name='BeforeCompact',
                session_id=self._session_id(),
                payload={
                    'automatic': False,
                    'message_count': len(self.messages),
                },
            )
        )
        self._queue_hook_context(before_compact)
        if not before_compact.allowed:
            stats = self.context.stats
            return CompactionReport(
                success=False,
                automatic=False,
                before_characters=stats.estimated_characters,
                after_characters=stats.estimated_characters,
                transcript_path=None,
                reason=before_compact.reason,
            )
        report = await self.context.compact_history(
            self.messages,
            self.client,
            force=True,
        )
        if report is None:
            raise AssertionError('Forced compaction did not return a report.')
        if report.success and self.session_journal is not None:
            self.session_journal.record_context_compacted(self.messages)
        return report

    async def session_start(self, *, source: str = 'new') -> None:
        '''Emit SessionStart at most once for the active session.'''
        if self._hooks_started:
            return
        self._hooks_started = True
        outcome = await self._emit_hook(
            HookEvent(
                name='SessionStart',
                session_id=self._session_id(),
                payload={'source': source},
            )
        )
        self._queue_hook_context(outcome)

    async def session_end(self, *, reason: str = 'exit') -> None:
        '''Emit SessionEnd once before the current session is closed.'''
        if not self._hooks_started:
            return
        await self._emit_hook(
            HookEvent(
                name='SessionEnd',
                session_id=self._session_id(),
                payload={'reason': reason},
            )
        )
        self._hooks_started = False

    async def session_resume_with_hooks(self, identifier: str) -> str:
        await self.session_end(reason='resume')
        notice = self.session_resume(identifier)
        if self.mcp_manager is not None:
            await self.mcp_manager.reset_session()
        await self.session_start(source='resume')
        return notice

    async def session_branch_with_hooks(
        self,
        name: str | None = None,
    ) -> str:
        await self.session_end(reason='branch')
        notice = self.session_branch(name)
        if self.mcp_manager is not None:
            await self.mcp_manager.reset_session()
        await self.session_start(source='branch')
        return notice

    async def session_clear_with_hooks(self) -> str:
        await self.session_end(reason='clear')
        notice = self.session_clear()
        if self.mcp_manager is not None:
            await self.mcp_manager.reset_session()
        await self.session_start(source='clear')
        return notice

    async def _emit_hook(self, event: HookEvent) -> HookOutcome:
        if self.hook_manager is None:
            return HookOutcome(arguments=event.arguments)
        outcome = await self.hook_manager.emit(event)
        journal = self.session_journal
        if journal is not None:
            for execution in outcome.executions:
                journal.record_hook_execution(
                    execution.as_dict(),
                    tool_name=event.tool_name,
                    tool_call_id=event.tool_call_id,
                    paths=event.paths,
                )
        return outcome

    def _queue_hook_context(self, outcome: HookOutcome) -> None:
        self._pending_hook_context.extend(outcome.additional_context)

    def _session_id(self) -> str | None:
        return (
            self.session_journal.session_id
            if self.session_journal is not None
            else None
        )

    def record_session_event(self, event: ConversationEvent) -> None:
        '''Persist runtime boundaries needed for safe session recovery.'''
        journal = self.session_journal
        if journal is None:
            return
        if isinstance(event, ToolExecutionStarted):
            call = event.tool_call
            journal.record_tool_started(
                call.id,
                call.name,
                call.arguments,
                provenance=(
                    self.registry.provenance(call.name)
                    if self.registry is not None
                    else None
                ),
            )
        elif isinstance(event, ToolExecutionCompleted):
            call = event.tool_call
            journal.record_tool_completed(
                call.id,
                call.name,
                event.result.success,
                provenance=(
                    {
                        key: event.result.metadata[key]
                        for key in ('source', 'server', 'remote_tool')
                        if key in event.result.metadata
                    }
                    or (
                        self.registry.provenance(call.name)
                        if self.registry is not None
                        else {}
                    )
                ),
            )
        elif isinstance(event, TurnCompleted):
            journal.record_turn_completed(
                self.messages,
                self.task_manager.active,
                event.result,
            )

    def record_session_error(self, error: Exception) -> None:
        if self.session_journal is not None:
            self.session_journal.record_error(error)

    def remember(self, name: str, content: str) -> str:
        record = self.context.remember(name, content)
        return f'Remembered {record.name} in {record.path.as_posix()}'

    def memory_list(self) -> str:
        records = self.context.repository.memory.list()
        if not records:
            return 'No repository memories.'
        return '\n'.join(
            f'- {record.name} [{record.memory_type}]: {record.description}'
            for record in records
        )

    def memory_show(self, name: str) -> str:
        record = self.context.repository.memory.get(name)
        if record is None:
            return f'Memory not found: {name}'
        return (
            f'{record.name} [{record.memory_type}]\n'
            f'{record.description}\n\n{record.content}'
        )

    def memory_forget(self, name: str) -> str:
        removed = self.context.repository.memory.forget(name)
        return f'Forgot {name}.' if removed else f'Memory not found: {name}'

    def memory_rebuild(self) -> str:
        path = self.context.repository.memory.rebuild_index()
        return f'Rebuilt memory index: {path.as_posix()}'

    def memory_consolidate(self) -> str:
        removed = self.context.repository.memory.consolidate()
        return f'Consolidated memory; removed {removed} duplicate(s).'

    def task_show(self) -> str:
        return self.task_manager.describe()

    def task_history(self) -> str:
        return self.task_manager.history()

    def task_resume(self, task_id: str) -> str:
        task = self.task_manager.resume(task_id)
        self._last_task_context = self.task_manager.system_suffix()
        return f'Resumed {task.id}: {task.goal}'

    def permission_status(self) -> str:
        return self.permission_manager.describe()

    def permission_set_mode(self, mode: str) -> str:
        resolved = self.permission_manager.set_mode(mode)
        return f'Permission mode set to {resolved}.'

    def mcp_status(self) -> str:
        if self.mcp_manager is None:
            return 'MCP Client Manager is unavailable.'
        return self.mcp_manager.status()

    async def runtime_close(self, *, reason: str = 'exit') -> None:
        await self.session_end(reason=reason)
        if self.mcp_manager is not None:
            await self.mcp_manager.close()

    def checkpoint_undo(self) -> str:
        if self.checkpoint_store is None:
            raise ValueError('File checkpoints are unavailable.')
        checkpoint_id = self.checkpoint_store.latest_restorable()
        if checkpoint_id is None:
            raise ValueError('No restorable file checkpoints.')
        return self.checkpoint_rewind(checkpoint_id, mode='code')

    def checkpoint_history(self) -> str:
        if self.checkpoint_store is None:
            return 'File checkpoints are unavailable.'
        checkpoints = self.checkpoint_store.list()
        if not checkpoints:
            return 'No file checkpoints.'
        return '\n'.join(f'- {item}' for item in checkpoints)

    def checkpoint_rewind(
        self,
        checkpoint_id: str | None = None,
        *,
        mode: str = 'both',
    ) -> str:
        if self.checkpoint_store is None:
            raise ValueError('File checkpoints are unavailable.')
        if mode not in {'code', 'conversation', 'both'}:
            raise ValueError(
                'Rewind mode must be code, conversation, or both.'
            )
        resolved_id = checkpoint_id
        if not resolved_id:
            checkpoints = self.checkpoint_store.list()
            if not checkpoints:
                raise ValueError('No file checkpoints.')
            resolved_id = checkpoints[0]
        restored: tuple[str, ...] = ()
        restored_messages: list[dict[str, Any]] | None = None
        restored_task: ActiveTask | None = None
        if mode in {'conversation', 'both'}:
            if self.session_store is None or self.session_journal is None:
                raise ValueError('Conversation checkpoints are unavailable.')
            restored_messages, restored_task = (
                self.session_store.checkpoint_state(
                    self.session_journal.session_id,
                    resolved_id,
                )
            )
        if mode in {'code', 'both'}:
            restored = self.checkpoint_store.restore(resolved_id)
            if self.workspace_tracker is not None:
                self.workspace_tracker.watch_paths(restored)
        if restored_messages is not None:
            self.messages[:] = restored_messages
            self.task_manager.restore(restored_task)
            self._last_task_context = self.task_manager.system_suffix()
            self.session_journal.append(
                'conversation_rewound',
                {
                    'checkpoint_id': resolved_id,
                    'messages': restored_messages,
                    'task': (
                        restored_task.as_dict()
                        if restored_task is not None
                        else None
                    ),
                },
            )
        return (
            f'Rewound {mode} to {resolved_id}; '
            f'restored {len(restored)} file(s).'
        )

    def session_status(self) -> str:
        if self.session_journal is None:
            return 'Session persistence is unavailable.'
        task = self.task_manager.active
        checkpoint_count = (
            len(self.checkpoint_store.list())
            if self.checkpoint_store is not None
            else 0
        )
        task_id = task.id if task is not None else 'none'
        task_status = task.status if task is not None else 'none'
        return (
            f'id: {self.session_journal.session_id}\n'
            f'messages: {len(self.messages)}\n'
            f'checkpoints: {checkpoint_count}\n'
            f'task: {task_id}\n'
            f'task status: {task_status}'
        )

    def session_history(self) -> str:
        if self.session_store is None or self.session_journal is None:
            return 'Session persistence is unavailable.'
        events = self.session_store.history(
            self.session_journal.session_id
        )
        return '\n'.join(
            '- {}: {}{}'.format(
                item['sequence'],
                item['type'],
                ' — {}'.format(item['summary'])
                if item['summary']
                else '',
            )
            for item in events
        )

    def session_candidates(self) -> str:
        if self.session_store is None:
            return 'Session persistence is unavailable.'
        sessions = self.session_store.list()
        if not sessions:
            return 'No saved ForgeCode sessions for this project.'
        return '\n'.join(
            '- {}{} [{}]'.format(
                item.session_id,
                ' ({})'.format(item.name) if item.name else '',
                item.status,
            )
            for item in sessions
        )

    def session_rename(self, name: str) -> str:
        if self.session_journal is None:
            raise ValueError('Session persistence is unavailable.')
        cleaned = self.session_journal.rename(name)
        return f'Renamed session to {cleaned}.'

    def session_resume(self, identifier: str) -> str:
        if self.session_store is None:
            raise ValueError('Session persistence is unavailable.')
        state, journal = self.session_store.open(identifier)
        if (
            self.session_journal is not None
            and state.info.session_id == self.session_journal.session_id
        ):
            return f'Session {state.info.session_id} is already active.'
        if self.session_journal is not None:
            self.session_journal.record_stopped()
        self.messages[:] = list(state.messages)
        self.task_manager.restore(state.active_task)
        self._last_task_context = self.task_manager.system_suffix()
        if state.info.model and hasattr(self.client, 'model'):
            self.client.model = state.info.model
        self.session_journal = journal
        self.permission_manager.bind_session(journal)
        if self.mcp_manager is not None:
            self.mcp_manager.bind(self.permission_manager, journal)
        self.checkpoint_store = CheckpointStore.for_session(
            self.task_manager.root,
            journal.path,
            journal.session_id,
        )
        journal.record_resumed()
        warning = (
            f' Warning: {len(state.indeterminate_tools)} indeterminate '
            'tool execution(s) were not replayed.'
            if state.indeterminate_tools
            else ''
        )
        return f'Resumed {state.info.session_id}.{warning}'

    def session_branch(self, name: str | None = None) -> str:
        if self.session_store is None or self.session_journal is None:
            raise ValueError('Session persistence is unavailable.')
        source = self.session_store.load(self.session_journal.session_id)
        model = str(getattr(self.client, 'model', source.info.model))
        journal = self.session_store.fork(
            source,
            messages=self.messages,
            task=self.task_manager.active,
            model=model,
            name=name,
        )
        original_id = self.session_journal.session_id
        self.session_journal = journal
        self.permission_manager.bind_session(journal)
        if self.mcp_manager is not None:
            self.mcp_manager.bind(self.permission_manager, journal)
        self.checkpoint_store = CheckpointStore.for_session(
            self.task_manager.root,
            journal.path,
            journal.session_id,
        )
        return (
            f'Branched session {original_id} -> {journal.session_id}.'
        )

    def session_clear(self) -> str:
        if self.session_store is None or self.session_journal is None:
            self.messages.clear()
            self.task_manager.restore(None)
            return 'Cleared conversation history.'
        previous_id = self.session_journal.session_id
        self.session_journal.record_stopped()
        model = str(getattr(self.client, 'model', ''))
        journal = self.session_store.create(model=model)
        self.session_journal = journal
        self.permission_manager.bind_session(journal)
        if self.mcp_manager is not None:
            self.mcp_manager.bind(self.permission_manager, journal)
        self.checkpoint_store = CheckpointStore.for_session(
            self.task_manager.root,
            journal.path,
            journal.session_id,
        )
        self.messages.clear()
        self.task_manager.restore(None)
        self._last_task_context = ''
        return (
            f'Cleared conversation. Previous session: {previous_id}; '
            f'new session: {journal.session_id}.'
        )


def build_assistant_message(
    text: str,
    tool_calls: list[ToolCall],
) -> dict[str, Any]:
    '''Build model-visible assistant history from a completed response.'''
    if not tool_calls:
        return {'role': 'assistant', 'content': text}

    content: list[dict[str, Any]] = []
    if text:
        content.append({'type': 'text', 'text': text})
    content.extend(
        {
            'type': 'tool_use',
            'id': call.id,
            'name': call.name,
            'input': call.arguments,
        }
        for call in sorted(tool_calls, key=lambda call: call.index)
    )
    return {'role': 'assistant', 'content': content}


def build_tool_result_message(
    tool_results: list[tuple[ToolCall, ToolResult]],
) -> dict[str, Any]:
    '''Build one user message containing ordered Anthropic tool results.'''
    content: list[dict[str, Any]] = []
    for tool_call, result in tool_results:
        content.append(
            {
                'type': 'tool_result',
                'tool_use_id': tool_call.id,
                'content': serialize_tool_result(result),
                'is_error': not result.success,
            }
        )
    return {'role': 'user', 'content': content}


def serialize_tool_result(result: ToolResult) -> str:
    '''Serialize the stable ToolResult contract for model consumption.'''
    error = None
    if result.error is not None:
        error = {
            'code': result.error.code,
            'message': result.error.message,
            'details': result.error.details,
        }
    return json.dumps(
        {
            'success': result.success,
            'summary': result.summary,
            'content': result.content,
            'error': error,
            'metadata': result.metadata,
        },
        ensure_ascii=False,
        default=str,
    )


def verification_from_result(
    result: ToolResult,
) -> VerificationEvidence | None:
    '''Build stable evidence from one verify ToolResult metadata payload.'''
    metadata = result.metadata
    if metadata.get('verification') is not True:
        return None
    try:
        return VerificationEvidence(
            command=str(metadata['command']),
            cwd=str(metadata['cwd']),
            exit_code=int(metadata['exit_code']),
            duration_seconds=float(metadata['duration_seconds']),
            timed_out=bool(metadata['timed_out']),
            workspace_revision=int(metadata['workspace_revision']),
        )
    except (KeyError, TypeError, ValueError):
        return None


def tool_call_signature(tool_call: ToolCall, revision: int) -> str:
    '''Identify an exact tool request within one workspace revision.'''
    arguments = json.dumps(
        tool_call.arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    )
    return f'{revision}:{tool_call.name}:{arguments}'


def hook_denied_result(event: str, reason: str) -> ToolResult:
    '''Expose a pre-operation hook denial to the model as a tool failure.'''
    return ToolResult.fail(
        'hook_denied',
        f'{event} hook denied this operation.',
        content=reason,
        details={'event': event, 'reason': reason},
    )


def repeated_tool_result(
    tool_call: ToolCall,
    previous_count: int,
    *,
    previous_success: bool,
) -> ToolResult:
    '''Return actionable feedback without executing a known repeat.'''
    cause = (
        'the previous identical call failed'
        if not previous_success
        else f'it already ran {previous_count} times'
    )
    return ToolResult.fail(
        'repeated_tool_call',
        (
            f'Skipped repeated {tool_call.name} call because {cause}. '
            'Use the existing result, change the arguments, or choose a '
            'different next action.'
        ),
        details={
            'tool': tool_call.name,
            'arguments': tool_call.arguments,
            'previous_count': previous_count,
            'previous_success': previous_success,
        },
    )


def required_change_block_reason() -> str:
    return (
        'This turn requires a real task-local workspace change, but no file '
        'differs from the workspace snapshot captured when the turn began.'
    )


def render_change_contract_context(
    changed_paths: tuple[str, ...],
    *,
    mutation_attempted: bool,
) -> str:
    paths = ', '.join(changed_paths) if changed_paths else 'none'
    attempted = 'yes' if mutation_attempted else 'no'
    return (
        '[ForgeCode Turn Change Contract]\n'
        'The user requested an implemented workspace change; an explanation '
        'or inspection alone cannot complete this turn.\n'
        f'- task-local changed paths: {paths}\n'
        f'- workspace write attempted: {attempted}\n'
        'Only a file revision after the turn baseline satisfies this '
        'contract. Git HEAD changes or untracked files that already existed '
        'when the turn began are background context, not work completed in '
        'this turn.'
    )


def build_stagnation_feedback(
    calls_without_progress: int,
    task_context: str,
    working_context: str,
) -> dict[str, Any]:
    '''Remind the model to change strategy while preserving the active goal.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n{working_context}\n\n'
            'ForgeCode progress check: '
            f'{calls_without_progress} model calls have passed without a '
            'workspace change or task-state transition. Reuse the evidence '
            'already collected and stop broad exploration. Perform at most '
            'one targeted lookup for a specific missing fact; otherwise edit, '
            'verify, or return an honest blocked result. Do not repeat an '
            'unchanged failing action.'
        ),
    }


def build_explore_handoff_feedback() -> dict[str, Any]:
    '''Tell the parent how to consume the isolated Explore report efficiently.'''
    return {
        'role': 'user',
        'content': (
            'ForgeCode Explore handoff: treat suggested_edit_points as the '
            'primary implementation map. When current_excerpt is present, '
            'reuse its exact whitespace as replace_text old_text or patch '
            'context. Spend focused reads only on unresolved questions or '
            'missing edit anchors; do not rescan files already supported by '
            'the report. Implement the requested behavior and tests, not '
            'placeholder or recovery-marker changes.'
        ),
    }


def build_change_convergence_feedback(
    task_context: str,
    working_context: str,
) -> dict[str, Any]:
    '''Move a researched change request from exploration to implementation.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n{working_context}\n\n'
            'ForgeCode implementation checkpoint: the requested change still '
            'has no task-local Diff after the bounded exploration phase. The '
            'next request exposes only focused read/grep and editing tools. '
            'Reuse the evidence already collected; if one exact fact is still '
            'missing, perform one targeted lookup, then make a task-relevant '
            'edit now. For an existing file, prefer replace_text with an exact '
            'unique fragment already visible in evidence; otherwise use a '
            'small apply_patch. Do not create placeholder, temporary, test, noop, or '
            'sentinel files merely to produce a Diff. If an edit fails, '
            'ForgeCode will provide a bounded recovery read. Do not continue '
            'repository exploration or return an implementation claim without '
            'a real edit.'
        ),
    }


def build_verification_failure_feedback(
    result: ToolResult,
) -> dict[str, Any]:
    '''Keep failed verification focused on the smallest shared root cause.'''
    return {
        'role': 'user',
        'content': (
            'ForgeCode verification repair checkpoint: the current Diff exists, '
            'but verification failed. Treat the immediately preceding verification '
            'output as the primary diagnostic. If many tests share one exception '
            'or traceback, fix that production-code root cause first, then rerun '
            'the smallest failing test set. Do not weaken, delete, or rewrite '
            'existing tests merely to make them pass unless the user explicitly '
            'requested a test-contract change. Use at most one focused read for '
            'an exact missing definition or edit anchor; do not restart broad '
            f'repository exploration. Verification status: {result.summary}'
        ),
    }


def build_post_mutation_convergence_feedback() -> dict[str, Any]:
    '''Keep a partial implementation moving toward verification, not discovery.'''
    return {
        'role': 'user',
        'content': (
            'ForgeCode post-edit checkpoint: a real Diff now exists. Continue '
            'only with task-relevant edits, up to four focused read/grep calls '
            'for exact missing source context, or the verify tool. Broad '
            'repository discovery tools are closed in this phase. Do not '
            'finalize until every requested behavior and test is implemented; '
            'when implementation is complete, run the requested verification '
            'on the current revision.'
        ),
    }


def completion_review_paths(
    tool_results: list[tuple[ToolCall, ToolResult]],
    changed_paths: tuple[str, ...],
) -> set[str]:
    '''Return changed paths covered by a successful, non-empty Git Diff.'''
    changed = {
        path.replace('\\', '/')
        for path in changed_paths
    }
    reviewed: set[str] = set()
    for tool_call, result in tool_results:
        if (
            tool_call.name != 'git_diff'
            or not result.success
            or not result.content.strip()
            or result.metadata.get('diff_complete') is False
        ):
            continue
        path = result.metadata.get('path')
        if path is None:
            reviewed.update(changed)
            continue
        normalized = str(path).replace('\\', '/')
        if normalized in changed:
            reviewed.add(normalized)
    return reviewed


def render_completion_ready_context(
    changed_paths: tuple[str, ...],
    verification: VerificationEvidence | None,
    decision_calls: int,
    decision_limit: int,
    reviewed_paths: set[str],
) -> str:
    '''Persist the mechanically complete revision and decision budget.'''
    changed = ', '.join(changed_paths)
    reviewed = ', '.join(sorted(reviewed_paths)) or 'none'
    verification_status = (
        f'{verification.command} @ revision {verification.workspace_revision}'
        if verification is not None
        else 'not required / not run'
    )
    remaining = max(decision_limit - decision_calls, 0)
    return (
        '[ForgeCode Completion Ready]\n'
        f'changed paths: {changed}\n'
        f'current verification: {verification_status}\n'
        f'reviewed Diff paths: {reviewed}\n'
        f'decision calls remaining: {remaining}\n'
        'Deterministic completion checks pass for the current revision. '
        'All tools listed in this request remain available, but open-ended '
        'discovery is no longer useful. Decide whether the user goal is '
        'satisfied. If it is, return the final answer or call finish_task '
        'alone. If it is not, make one concrete workspace edit based on the '
        'existing evidence, then verify the new revision. Use scoped git_diff '
        'only for a changed path not already reviewed. If it returns '
        'diff_complete=false, immediately continue with its next_offset and '
        'diff_sha256 until the final page.'
    )


def build_finalization_recovery_feedback(
    task_context: str,
    working_context: str,
    changed_paths: tuple[str, ...],
    verification: VerificationEvidence | None,
) -> dict[str, Any]:
    '''Request one bounded, tool-free synthesis after a ready-state loop.'''
    verification_status = (
        f'{verification.command} @ revision {verification.workspace_revision}'
        if verification is not None
        else 'not required / not run'
    )
    changed = ', '.join(changed_paths)
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n{working_context}\n\n'
            '[ForgeCode Finalization Recovery]\n'
            'The current revision passed every deterministic completion '
            'check, but the trajectory continued diagnostics without another '
            'workspace change. The next request is a dedicated final '
            'synthesis with no tools. Return a concise final answer in the '
            'user\'s language. Summarize the actual changed paths '
            f'({changed}) and verification '
            f'({verification_status}). State any semantic or visual '
            'limitation honestly. Do not request another tool call.'
        ),
    }


def mutation_failure_record(
    tool_call: ToolCall,
    result: ToolResult,
) -> dict[str, Any]:
    '''Keep bounded, actionable evidence for a write that changed nothing.'''
    error_code = (
        result.error.code
        if result.error is not None
        else 'no_workspace_change'
    )
    message = (
        result.error.message
        if result.error is not None
        else (
            'The tool reported success, but the task-local workspace '
            'revision did not change.'
        )
    )
    diagnostic = result.content.strip()
    if len(diagnostic) > 2_000:
        diagnostic = (
            diagnostic[:1_000]
            + '\n...[diagnostic shortened]...\n'
            + diagnostic[-1_000:]
        )
    return {
        'tool': tool_call.name,
        'code': error_code,
        'message': message,
        'targets': list(mutation_target_paths(tool_call)),
        'diagnostic': diagnostic,
    }


def mutation_target_paths(
    tool_call: ToolCall,
    *,
    maximum: int | None = 5,
) -> tuple[str, ...]:
    '''Extract only path evidence, never the potentially large write body.'''
    paths: list[str] = []
    direct_path = tool_call.arguments.get('path')
    if isinstance(direct_path, str) and direct_path.strip():
        paths.append(direct_path.strip().replace('\\', '/'))
    patch = tool_call.arguments.get('patch')
    if isinstance(patch, str):
        prefixes = (
            '*** Update File:',
            '*** Add File:',
            '*** Delete File:',
            '*** Move to:',
            '+++ b/',
            '--- a/',
        )
        for line in patch.splitlines():
            stripped = line.strip()
            prefix = next(
                (
                    candidate
                    for candidate in prefixes
                    if stripped.startswith(candidate)
                ),
                None,
            )
            if prefix is None:
                continue
            path = stripped[len(prefix):].strip().replace('\\', '/')
            if path and path != '/dev/null':
                paths.append(path)
    unique = tuple(dict.fromkeys(paths))
    return unique if maximum is None else unique[:maximum]


def render_mutation_recovery_context(
    failures: list[dict[str, Any]],
    failure_count: int,
) -> str:
    '''Render durable failure state outside compactable chat history.'''
    lines = [
        '[Failed Mutation Recovery]',
        f'failed workspace writes: {failure_count}',
    ]
    for failure in failures:
        targets = ', '.join(failure['targets']) or 'unknown target'
        tool = failure['tool']
        code = failure['code']
        message = failure['message']
        lines.append(f'- {tool} [{code}] on {targets}: {message}')
        diagnostic = str(failure.get('diagnostic', '')).strip()
        if diagnostic:
            lines.append(f'  diagnostic: {diagnostic}')
    lines.append(
        'Edit Recovery permits one targeted read or grep, followed by one '
        'corrected workspace edit. Do not restart broad discovery or repeat '
        'the rejected payload.'
    )
    lines.append(
        'For an existing file, prefer replace_text with one exact unique '
        'fragment copied from the current evidence. Otherwise use a smaller '
        'apply_patch based on exact current lines. Repeated failures on the '
        'same target, or too many failures across targets, end this recovery.'
    )
    return '\n'.join(lines)


def build_mutation_text_retry_feedback(
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    '''Reject one premature prose response while a correctable edit remains.'''
    latest = failures[-1] if failures else {}
    tool = str(latest.get('tool', 'workspace edit'))
    code = str(latest.get('code', 'no_workspace_change'))
    return {
        'role': 'user',
        'content': (
            'ForgeCode Edit Recovery rejected the prose response because a '
            f'correctable {tool} [{code}] failure remains. Editing tools are '
            'available in this request. Use the exact current fragment or '
            'diagnostic already collected to make one materially corrected '
            'workspace edit now. Verification becomes available again after '
            'the edit changes the workspace. Do not claim that tools are '
            'unavailable and do not return another summary.'
        ),
    }


def build_mutation_recovery_feedback(
    failures: list[dict[str, Any]],
    failure_count: int,
    task_context: str,
) -> dict[str, Any]:
    '''Put the recovery checkpoint after a failed write result.'''
    context = render_mutation_recovery_context(
        failures,
        failure_count,
    )
    return {
        'role': 'user',
        'content': f'{task_context}\n\n{context}',
    }


def mutation_recovery_stuck_reason(
    failures: list[dict[str, Any]],
    failure_count: int,
) -> str:
    latest = failures[-1] if failures else {}
    tool = str(latest.get('tool', 'workspace tool'))
    code = str(latest.get('code', 'no_workspace_change'))
    return (
        f'Stopped after {failure_count} workspace-write attempt(s) failed '
        'to change the task workspace; the Edit Recovery failure limit was '
        f'reached. Latest failure: {tool} [{code}].'
    )


def is_tool_protocol_failure(result: ToolResult) -> bool:
    '''Return whether every failure came from the tool-call protocol.'''
    return (
        not result.success
        and result.error is not None
        and result.error.code in {
            'invalid_arguments',
            'unknown_tool',
            'finish_must_be_alone',
            'unsupported_shell_syntax',
            'invalid_pattern',
            'patch_contains_read_line_numbers',
            'patch_empty_hunk',
            'patch_missing_hunk',
            'text_no_change',
            'git_diff_path_is_directory',
            'tool_not_available_in_phase',
            'recovery_read_already_used',
        }
    )


def build_tool_protocol_feedback(
    failures: int,
    task_context: str,
    tool_results: list[tuple[ToolCall, ToolResult]] | None = None,
) -> dict[str, Any]:
    diagnostics: list[str] = []
    for tool_call, result in tool_results or ():
        if result.error is None:
            continue
        message = result.error.message
        if len(message) > 1_500:
            message = f'{message[:1_497]}...'
        diagnostics.append(f'- {tool_call.name}: {message}')
    rendered_diagnostics = (
        '\nExact rejection(s):\n' + '\n'.join(diagnostics) + '\n'
        if diagnostics
        else ''
    )
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            'The previous tool request was rejected at the argument/schema '
            'boundary. This does not mean the repository task is blocked. '
            f'{rendered_diagnostics}'
            'Follow the exact recovery instruction above, change the '
            'arguments materially, and retry with valid JSON or choose '
            'another tool. Do not repeat the rejected payload. '
            f'Protocol recovery count: {failures}.'
        ),
    }


def build_synthesis_retry_feedback(
    task_context: str,
    working_context: str,
) -> dict[str, Any]:
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n{working_context}\n\n'
            'ForgeCode rejected the previous synthesis because it did not '
            'reference collected repository evidence. All tools remain '
            'available. Answer the current goal using the working evidence, '
            'or gather genuinely missing evidence before answering.'
        ),
    }


def self_declared_incomplete_reasons(
    text: str,
    *,
    require_tests: bool,
) -> tuple[str, ...]:
    '''Detect explicit admissions that a change task is still unfinished.'''
    normalized = ' '.join(text.split())
    reasons: list[str] = []
    implementation_patterns = (
        (
            r'\b(?:does not|did not|has not|have not|not yet|haven\'t|'
            r'isn\'t|is not)\b.{0,100}\b'
            r'(?:implement|complete|finish|address|satisfy|support)\w*'
        ),
        (
            r'(?:尚未|还未|仍未|没有|并未|未)(?:真正)?'
            r'(?:实现|完成|修复|满足|支持)'
        ),
    )
    if any(
        re.search(pattern, normalized, flags=re.IGNORECASE)
        for pattern in implementation_patterns
    ):
        reasons.append(
            'The response explicitly says the requested implementation is '
            'not complete.'
        )

    if require_tests:
        test_patterns = (
            (
                r'\b(?:did not|have not|has not|not yet|haven\'t)\b'
                r'.{0,80}\b(?:run|execute|complete)\w*\b'
                r'.{0,40}\b(?:full|complete|entire)?\s*'
                r'(?:test|tests|test suite)\b'
            ),
            (
                r'(?:尚未|还未|没有|未)(?:运行|执行|完成)'
                r'.{0,30}(?:完整|全量|全部|全面)?测试'
            ),
            (
                r'\b(?:cannot|can.t|unable\s+to)\b.{0,80}'
                r'\b(?:complete|run|execute)\w*\b.{0,80}'
                r'\b(?:verification|tests?|test\s+suite|checks?)\b'
            ),
            (
                r'\bmissing\s+(?:required\s+)?'
                r'(?:verification|tests?|test\s+suite)\b'
            ),
            (
                r'\bfull\s+(?:test\s+)?suite\b.{0,60}'
                r'\b(?:still\s+needs?|needs?\s+to\s+be|not)\b.{0,20}'
                r'\b(?:run|executed?)\b'
            ),
        )
        if any(
            re.search(pattern, normalized, flags=re.IGNORECASE)
            for pattern in test_patterns
        ):
            reasons.append(
                'The response explicitly says the requested tests were not run.'
            )
    return tuple(reasons)


def build_incomplete_declaration_feedback(
    reasons: tuple[str, ...],
) -> dict[str, Any]:
    '''Resume bounded editing when the model admits the task is unfinished.'''
    rendered = '\n'.join(f'- {reason}' for reason in reasons)
    return {
        'role': 'user',
        'content': (
            'ForgeCode rejected completion because the response explicitly '
            'declared required work incomplete:\n'
            f'{rendered}\n'
            'Continue from the existing Diff and repository evidence. Use the '
            'single targeted read/search allowance only if needed, make the '
            'missing task-relevant edit, and run the requested tests. Do not '
            'return another incomplete summary.'
        ),
    }


def build_output_continuation_feedback(
    *,
    attempt: int,
    maximum: int,
) -> dict[str, str]:
    '''Ask the model to continue preserved text without repeating it.'''
    return {
        'role': 'user',
        'content': (
            'The previous response reached the output token limit. The text '
            'already generated has been preserved. Continue directly from '
            'where it stopped without repeating earlier content, and finish '
            'concisely. If work remains, use the available tools instead of '
            'printing large code blocks. '
            f'Continuation attempt {attempt} of {maximum}.'
        ),
    }


def build_protocol_recovery_feedback(
    error: ModelProtocolError,
    *,
    attempt: int,
    maximum: int,
    available_tools: tuple[str, ...],
) -> list[dict[str, Any]]:
    '''Represent a rejected response and request one smaller valid retry.'''
    tool = f' for tool {error.tool_name!r}' if error.tool_name else ''
    if error.reason == 'output_truncated':
        problem = 'The previous response reached the max_tokens limit.'
    elif error.reason == 'unavailable_tool':
        problem = f'The previous response requested unavailable tool{tool}.'
    else:
        problem = f'The previous tool call{tool} had invalid arguments.'
    available = (
        ', '.join(available_tools) if available_tools else 'none'
    )
    retry_limit = 4_000 if attempt == 1 else 2_000
    retry_strategy = (
        'Modify only one function or one file section.'
        if attempt == 1
        else (
            'Create only a minimal skeleton. Keep HTML, CSS, and JavaScript '
            'in separate tool calls.'
        )
    )
    return [
        {
            'role': 'assistant',
            'content': '[ForgeCode rejected an invalid model response.]',
        },
        {
            'role': 'user',
            'content': (
                f'{problem}\nError: {error}\n'
                'No tool was executed and no file was changed by that '
                f'response. Available tools: {available}. For a small complete '
                f'new file, use write_file with at most {retry_limit} characters. '
                'For every change to an existing file, use a focused '
                f'apply_patch with at most {retry_limit} characters. '
                f'{retry_strategy} Split large '
                'HTML, CSS, or JavaScript across multiple calls and do not '
                'repeat the same invalid arguments.\n'
                f'Recovery attempt {attempt} of {maximum}.'
            ),
        },
    ]


def add_token_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    '''Add exact usage from separate model requests in one user turn.'''
    return TokenUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cache_creation_input_tokens=(
            left.cache_creation_input_tokens
            + right.cache_creation_input_tokens
        ),
        cache_read_input_tokens=(
            left.cache_read_input_tokens + right.cache_read_input_tokens
        ),
    )
