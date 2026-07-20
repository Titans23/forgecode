'''Multi-step model and tool execution for the M1 Agent Loop.'''

from collections.abc import AsyncIterator
from functools import cache
from itertools import count
import json
from pathlib import Path
from typing import Any

from forge.context.compactor import CompactionConfig
from forge.context.manager import (
    CompactionReport,
    ContextManager,
    ContextStats,
)
from forge.context.working import WorkingState
from forge.runtime.intent import infer_change_required
from forge.runtime.model_client import (
    AnthropicModelClient,
    ModelCallError,
    ModelClient,
    ModelOutputTruncatedError,
    ModelProtocolError,
)
from forge.runtime.completion import CompletionGate, TaskPolicy
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
from forge.tasks.manager import TaskManager
from forge.tools.base import ToolRegistry, ToolResult
from forge.tools.task import create_task_tools


ACTION_RECOVERY_READ_TOOLS = frozenset(
    {'read_file', 'grep'}
)


class ModelResponseError(RuntimeError):
    '''Raised when a model response cannot continue the Agent Loop.'''


class AgentLoopLimitError(RuntimeError):
    '''Raised when one user turn exceeds its model-call safety limit.'''


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
        max_iterations: int | None = None,
        task_policy: TaskPolicy | None = None,
        max_completion_blocks: int = 3,
        context_config: CompactionConfig | None = None,
        context_root: Path | None = None,
        max_protocol_recoveries: int = 2,
        max_tool_protocol_recoveries: int = 3,
        max_output_continuations: int = 2,
        repeated_tool_limit: int = 2,
        stagnation_warning: int = 4,
        stagnation_limit: int = 8,
        completion_decision_limit: int = 8,
        mutation_recovery_warning: int = 3,
        mutation_recovery_limit: int = 8,
        pre_mutation_limit: int = 8,
        action_recovery_limit: int = 3,
    ) -> None:
        if tools is not None and registry is not None:
            raise ValueError('Pass tools or registry, not both.')
        if max_iterations is not None and max_iterations < 1:
            raise ValueError('max_iterations must be positive')
        if max_completion_blocks < 1:
            raise ValueError('max_completion_blocks must be positive')
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
        if completion_decision_limit < 1:
            raise ValueError('completion_decision_limit must be positive')
        if mutation_recovery_warning < 1:
            raise ValueError(
                'mutation_recovery_warning must be positive'
            )
        if mutation_recovery_limit <= mutation_recovery_warning:
            raise ValueError(
                'mutation_recovery_limit must be greater than '
                'mutation_recovery_warning'
            )
        if pre_mutation_limit < 1:
            raise ValueError('pre_mutation_limit must be positive')
        if action_recovery_limit < 1:
            raise ValueError('action_recovery_limit must be positive')
        self.client = (
            client if client is not None else AnthropicModelClient.from_config()
        )
        self.system_prompt = (
            system_prompt
            if system_prompt is not None
            else load_system_prompt()
        )
        self.messages: list[dict[str, Any]] = []
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
        self.working_state = WorkingState()
        if registry is not None:
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
        self.max_completion_blocks = max_completion_blocks
        self.max_protocol_recoveries = max_protocol_recoveries
        self.max_tool_protocol_recoveries = max_tool_protocol_recoveries
        self.max_output_continuations = max_output_continuations
        self.repeated_tool_limit = repeated_tool_limit
        self.stagnation_warning = stagnation_warning
        self.stagnation_limit = stagnation_limit
        self.completion_decision_limit = completion_decision_limit
        self.mutation_recovery_warning = mutation_recovery_warning
        self.mutation_recovery_limit = mutation_recovery_limit
        self.pre_mutation_limit = pre_mutation_limit
        self.action_recovery_limit = action_recovery_limit
        self._last_repository_context = self.context.repository.system_suffix('')
        self._last_task_context = ''

    @property
    def context_stats(self) -> ContextStats:
        '''Return current committed conversation context statistics.'''
        return self.context.stats_for_request(
            system_prompt=self._system_prompt_with_task(),
            repository_context=self._last_repository_context,
            tools=self.tools,
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

        self.task_manager.begin_turn(prompt)
        self.working_state = WorkingState()
        self._last_task_context = self.task_manager.system_suffix()
        user_message = {'role': 'user', 'content': prompt}
        request_messages = [*self.messages, user_message]
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
        tool_attempts: dict[str, tuple[int, bool]] = {}
        calls_without_progress = 0
        pre_mutation_calls = 0
        action_recovery = False
        action_recovery_calls = 0
        action_read_used = False
        action_block_events = 0
        mutation_recovery_calls = 0
        mutation_failure_count = 0
        mutation_failures: list[dict[str, Any]] = []
        mutation_recovery_context = ''
        force_synthesis = False
        tool_protocol_failures = 0
        synthesis_retries = 0
        completion_blocks = 0
        last_completion_reasons: tuple[str, ...] = ()
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

            if finalization_recovery:
                request_tools = None
            elif action_recovery:
                request_tools = self._action_recovery_tools(
                    read_available=not action_read_used
                )
            else:
                request_tools = self.tools
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
                action_recovery=action_recovery,
                action_recovery_calls=action_recovery_calls,
                action_read_used=action_read_used,
            )
            await self.context.compact_history(
                request_messages,
                self.client,
                system_prompt=request_system_prompt,
                repository_context=self._last_repository_context,
                tools=request_tools,
                context_window_tokens=getattr(
                    self.client,
                    'context_window',
                    None,
                ),
                reserved_output_tokens=getattr(
                    self.client,
                    'max_tokens',
                    0,
                ),
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
                    report = await self.context.compact_history(
                        request_messages,
                        self.client,
                        force=True,
                    )
                    if report is not None and report.success:
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
                    if action_recovery:
                        action_recovery_calls += 1
                    else:
                        action_recovery = True
                        action_recovery_calls = 0
                        action_read_used = False
                    action_block_events += 1
                    change_reason = required_change_block_reason()
                    yield CompletionBlocked(
                        attempt=action_block_events,
                        reasons=(change_reason,),
                    )
                    if (
                        action_recovery_calls
                        >= self.action_recovery_limit
                    ):
                        reason = action_recovery_stuck_reason(
                            action_recovery_calls
                        )
                        self.task_manager.stuck((reason, change_reason))
                        self.messages[:] = request_messages
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
                                completion_reasons=(
                                    reason,
                                    change_reason,
                                ),
                            )
                        )
                        return
                    request_messages.append(
                        build_action_recovery_feedback(
                            self.task_manager.system_suffix(),
                            action_recovery_calls,
                            self.action_recovery_limit,
                            read_used=action_read_used,
                        )
                    )
                    continue
                if (
                    (force_synthesis or completion_blocks > 0)
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
                    reasons = (reason, *last_completion_reasons)
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
                    mutation_recovery_calls += 1
                    mutation_recovery_context = (
                        render_mutation_recovery_context(
                            mutation_failures,
                            mutation_recovery_calls,
                            mutation_failure_count,
                        )
                    )
                    if (
                        mutation_recovery_calls
                        >= self.mutation_recovery_limit
                    ):
                        reason = mutation_recovery_stuck_reason(
                            mutation_failures,
                            mutation_recovery_calls,
                            mutation_failure_count,
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
                    request_messages.append(
                        build_mutation_recovery_feedback(
                            mutation_failures,
                            mutation_recovery_calls,
                            mutation_failure_count,
                            self.task_manager.system_suffix(),
                        )
                    )
                    continue
                if self._pending_required_change(change_required):
                    if action_recovery:
                        action_recovery_calls += 1
                    else:
                        action_recovery = True
                        action_recovery_calls = 0
                        action_read_used = False
                    action_block_events += 1
                    change_reason = required_change_block_reason()
                    yield CompletionBlocked(
                        attempt=action_block_events,
                        reasons=(change_reason,),
                    )
                    if (
                        action_recovery_calls
                        >= self.action_recovery_limit
                    ):
                        reason = action_recovery_stuck_reason(
                            action_recovery_calls
                        )
                        self.task_manager.stuck((reason, change_reason))
                        self.messages[:] = request_messages
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
                                completion_reasons=(
                                    reason,
                                    change_reason,
                                ),
                            )
                        )
                        return
                    request_messages.append(
                        build_action_recovery_feedback(
                            self.task_manager.system_suffix(),
                            action_recovery_calls,
                            self.action_recovery_limit,
                            read_used=action_read_used,
                        )
                    )
                    continue
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
                    )
                    if not decision.allowed:
                        last_completion_reasons = decision.reasons
                        completion_blocks += 1
                        yield CompletionBlocked(
                            attempt=completion_blocks,
                            reasons=decision.reasons,
                        )
                        if force_synthesis:
                            reasons = (
                                'The agent stopped making progress before '
                                'the task satisfied its completion checks.',
                                *decision.reasons,
                            )
                            self.task_manager.stuck(reasons)
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
                                    completion_reasons=reasons,
                                )
                            )
                            return
                        if completion_blocks < self.max_completion_blocks:
                            request_messages.append(
                                build_completion_feedback(
                                    decision.reasons,
                                    task_context=(
                                        self.task_manager.system_suffix()
                                    ),
                                )
                            )
                            continue
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

            all_tool_calls.extend(tool_calls)
            tool_results: list[tuple[ToolCall, ToolResult]] = []
            last_workspace_change_position = -1
            task_progressed = False
            evidence_progressed = False
            required_change_rejected = False
            workspace_write_results: list[
                tuple[int, ToolCall, ToolResult, bool]
            ] = []
            accepted_finish: ToolResult | None = None
            terminal_finish_reasons: tuple[str, ...] = ()
            for tool_position, tool_call in enumerate(tool_calls):
                finish_rejection: tuple[str, ...] = ()
                tool_effect = self.registry.effect(tool_call.name)
                if tool_effect == 'workspace_write':
                    mutation_attempted = True
                    change_required = True
                if (
                    tool_call.name == 'finish_task'
                    and tool_call.arguments.get('task_kind') == 'change'
                ):
                    change_required = True
                action_read_call = (
                    action_recovery
                    and tool_call.name in ACTION_RECOVERY_READ_TOOLS
                )
                action_read_exhausted = (
                    action_read_call and action_read_used
                )
                if action_read_call and not action_read_used:
                    action_read_used = True
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
                if finish_mixed:
                    result = ToolResult.fail(
                        'finish_must_be_alone',
                        'finish_task must be the only tool call in its model '
                        'response. Complete other actions first, then declare '
                        'the outcome in a separate response.',
                    )
                elif (
                    action_recovery
                    and tool_call.name not in request_tool_names
                ):
                    result = ToolResult.fail(
                        'tool_not_available_in_phase',
                        f'{tool_call.name} is not available during Action '
                        'Recovery. Use one of the tools included with this '
                        'request.',
                        details={
                            'available_tools': sorted(request_tool_names),
                        },
                    )
                elif action_read_exhausted:
                    result = ToolResult.fail(
                        'action_read_limit_reached',
                        'Action Recovery permits only one targeted repository '
                        'read or search. Use the existing evidence and make '
                        'the workspace edit now.',
                    )
                elif semantic_repeat is not None:
                    result = semantic_repeat
                elif should_block_repeat:
                    result = repeated_tool_result(
                        tool_call,
                        previous_count,
                        previous_success=previous_success,
                    )
                else:
                    result = await self.registry.execute(
                        tool_call.name,
                        tool_call.arguments,
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
                        last_completion_reasons = finish_reasons
                        pending_required_change = (
                            self._pending_required_change(change_required)
                        )
                        if pending_required_change:
                            required_change_rejected = True
                            action_block_events += 1
                        else:
                            completion_blocks += 1
                        force_synthesis = False
                        calls_without_progress = 0
                        result = ToolResult.fail(
                            'finish_rejected',
                            'The finish_task declaration did not match the '
                            'available execution evidence.',
                            details={'reasons': list(finish_reasons)},
                        )
                        if (
                            not pending_required_change
                            and completion_blocks
                            >= self.max_completion_blocks
                        ):
                            terminal_finish_reasons = finish_reasons
                    else:
                        accepted_finish = result
                evidence_progressed = (
                    self.working_state.observe(
                        tool_call,
                        result,
                        revision,
                        signature,
                    )
                    or evidence_progressed
                )
                tool_results.append((tool_call, result))
                yield ToolExecutionCompleted(
                    tool_call=tool_call,
                    result=result,
                )
                if finish_rejection:
                    yield CompletionBlocked(
                        attempt=(
                            action_block_events
                            if required_change_rejected
                            else completion_blocks
                        ),
                        reasons=finish_rejection,
                    )
                tool_changed_workspace = False
                if self.workspace_tracker is not None:
                    change = await self.workspace_tracker.refresh()
                    if change is not None:
                        tool_changed_workspace = True
                        last_workspace_change_position = tool_position
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
                    if latest_verification is not None:
                        yield VerificationCompleted(
                            evidence=latest_verification
                        )
                if tool_call.name == 'task_update' and result.success:
                    task_progressed = True
            request_messages.append(build_tool_result_message(tool_results))

            if terminal_finish_reasons:
                self.task_manager.stuck(terminal_finish_reasons)
                self.messages[:] = request_messages
                yield TurnCompleted(
                    result=TurnResult(
                        text=(
                            'ForgeCode rejected the model completion '
                            'declaration after repeated evidence failures.'
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
            batch_reverted_to_baseline = (
                workspace_progressed
                and self.workspace_tracker is not None
                and not self.workspace_tracker.changed_paths
            )
            if batch_reverted_to_baseline:
                workspace_progressed = False
            if workspace_progressed:
                mutation_recovery_calls = 0
                mutation_failure_count = 0
                mutation_failures.clear()
                mutation_recovery_context = ''
                pre_mutation_calls = 0
                action_recovery = False
                action_recovery_calls = 0
                action_read_used = False
                force_synthesis = False
                synthesis_retries = 0
                completion_ready_revision = None
                completion_decision_calls = 0
                completion_ready_context = ''
                completion_reviewed_paths.clear()
            pending_write_results = [
                (call, result)
                for position, call, result, changed
                in workspace_write_results
                if position > last_workspace_change_position and not changed
            ]
            if batch_reverted_to_baseline and workspace_write_results:
                _, last_call, last_result, _ = workspace_write_results[-1]
                pending_write_results = [(last_call, last_result)]
            if pending_write_results:
                mutation_failure_count += len(pending_write_results)
                for failed_call, failed_result in pending_write_results:
                    mutation_failures.append(
                        mutation_failure_record(
                            failed_call,
                            failed_result,
                        )
                    )
                mutation_failures = mutation_failures[-3:]
            if mutation_failures:
                action_recovery = False
                action_recovery_calls = 0
                action_read_used = False
                mutation_recovery_calls += 1
                mutation_recovery_context = (
                    render_mutation_recovery_context(
                        mutation_failures,
                        mutation_recovery_calls,
                        mutation_failure_count,
                    )
                )
                if workspace_write_results:
                    request_messages.append(
                        build_mutation_recovery_feedback(
                            mutation_failures,
                            mutation_recovery_calls,
                            mutation_failure_count,
                            self.task_manager.system_suffix(),
                        )
                    )
                elif (
                    mutation_recovery_calls
                    == self.mutation_recovery_warning
                ):
                    request_messages.append(
                        build_mutation_recovery_feedback(
                            mutation_failures,
                            mutation_recovery_calls,
                            mutation_failure_count,
                            self.task_manager.system_suffix(),
                        )
                    )
                if (
                    mutation_recovery_calls
                    >= self.mutation_recovery_limit
                ):
                    reason = mutation_recovery_stuck_reason(
                        mutation_failures,
                        mutation_recovery_calls,
                        mutation_failure_count,
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
            pending_required_change = self._pending_required_change(
                change_required
            )
            if (
                pending_required_change
                and not mutation_failures
                and not protocol_failure
            ):
                entered_action_recovery = False
                if action_recovery:
                    action_recovery_calls += 1
                elif required_change_rejected:
                    action_recovery = True
                    action_recovery_calls = 0
                    action_read_used = False
                    entered_action_recovery = True
                else:
                    pre_mutation_calls += 1
                    if pre_mutation_calls >= self.pre_mutation_limit:
                        action_recovery = True
                        action_recovery_calls = 0
                        action_read_used = False
                        entered_action_recovery = True
                if action_recovery:
                    force_synthesis = False
                    synthesis_retries = 0
                    calls_without_progress = 0
                    if entered_action_recovery:
                        action_block_events += 1
                        yield CompletionBlocked(
                            attempt=action_block_events,
                            reasons=(required_change_block_reason(),),
                        )
                    if (
                        action_recovery_calls
                        >= self.action_recovery_limit
                    ):
                        reason = action_recovery_stuck_reason(
                            action_recovery_calls
                        )
                        change_reason = required_change_block_reason()
                        self.task_manager.stuck((reason, change_reason))
                        self.messages[:] = request_messages
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
                                completion_reasons=(
                                    reason,
                                    change_reason,
                                ),
                            )
                        )
                        return
                    request_messages.append(
                        build_action_recovery_feedback(
                            self.task_manager.system_suffix(),
                            action_recovery_calls,
                            self.action_recovery_limit,
                            read_used=action_read_used,
                        )
                    )
                    continue
            completion_ready = (
                not protocol_failure
                and await self._can_finalize_after_stagnation(
                    mutation_attempted=mutation_attempted,
                    verification=latest_verification,
                    mutation_failures=mutation_failures,
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
            if workspace_progressed or task_progressed or evidence_progressed:
                calls_without_progress = 0
                force_synthesis = False
                synthesis_retries = 0
            elif protocol_failure:
                # Malformed tool arguments are a protocol-recovery problem,
                # not evidence that the task itself is stuck.
                request_messages.append(
                    build_tool_protocol_feedback(
                        tool_protocol_failures,
                        self.task_manager.system_suffix(),
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
                    not mutation_failures
                    and self._pending_required_change(change_required)
                ):
                    action_recovery = True
                    action_recovery_calls = 0
                    action_read_used = False
                    force_synthesis = False
                    synthesis_retries = 0
                    calls_without_progress = 0
                    action_block_events += 1
                    yield CompletionBlocked(
                        attempt=action_block_events,
                        reasons=(required_change_block_reason(),),
                    )
                    request_messages.append(
                        build_action_recovery_feedback(
                            self.task_manager.system_suffix(),
                            action_recovery_calls,
                            self.action_recovery_limit,
                            read_used=action_read_used,
                        )
                    )
                    continue
                if await self._can_finalize_after_stagnation(
                    mutation_attempted=mutation_attempted,
                    verification=latest_verification,
                    mutation_failures=mutation_failures,
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
                    f'{calls_without_progress} model calls without new '
                    'workspace, plan, or repository evidence.'
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
            raise AgentLoopLimitError(
                f'Agent Loop exceeded {self.max_iterations} model calls.'
            )
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
        if self.tools and include_tool_availability:
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

    def _action_recovery_tools(
        self,
        *,
        read_available: bool,
    ) -> list[dict[str, Any]] | None:
        if self.registry is None or self.tools is None:
            return self.tools
        selected: list[dict[str, Any]] = []
        for definition in self.tools:
            name = str(definition.get('name', ''))
            if (
                (read_available and name in ACTION_RECOVERY_READ_TOOLS)
                or name == 'finish_task'
                or self.registry.effect(name) == 'workspace_write'
            ):
                selected.append(definition)
        return selected

    async def _finish_rejection_reasons(
        self,
        result: ToolResult,
        *,
        mutation_attempted: bool,
        change_required: bool,
        verification: VerificationEvidence | None,
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
        action_recovery: bool = False,
        action_recovery_calls: int = 0,
        action_read_used: bool = False,
    ) -> str:
        prompt = self._system_prompt_with_task(
            include_tool_availability=not finalization_recovery,
        )
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
        if mutation_recovery_context:
            prompt += '\n\n' + mutation_recovery_context
        if completion_ready_context:
            prompt += '\n\n' + completion_ready_context
        if finalization_recovery:
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
        elif action_recovery:
            prompt += '\n\n' + render_action_recovery_context(
                action_recovery_calls,
                self.action_recovery_limit,
                read_used=action_read_used,
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

    async def _can_finalize_after_stagnation(
        self,
        *,
        mutation_attempted: bool,
        verification: VerificationEvidence | None,
        mutation_failures: list[dict[str, Any]],
    ) -> bool:
        '''Enter synthesis only for a mechanically complete current revision.'''
        tracker = self.workspace_tracker
        gate = self.completion_gate
        if (
            tracker is None
            or gate is None
            or not tracker.changed_paths
            or mutation_failures
            or verification is None
            or not verification.success
            or verification.workspace_revision != tracker.revision
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
        report = await self.context.compact_history(
            self.messages,
            self.client,
            force=True,
        )
        if report is None:
            raise AssertionError('Forced compaction did not return a report.')
        return report

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


def build_completion_feedback(
    reasons: tuple[str, ...],
    *,
    task_context: str = '',
) -> dict[str, Any]:
    '''Tell the model exactly why its final answer was not accepted.'''
    details = '\n'.join(f'- {reason}' for reason in reasons)
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            'ForgeCode completion check rejected the previous final answer.\n'
            f'{details}\n'
            'The tools are still available. Continue using them, then provide '
            'a new final answer after every condition is satisfied. If '
            'verification is missing, call verify with the relevant test or '
            'build command; use git diff --check only when the project has no '
            'more specific validation command.'
        ),
    }


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


def render_action_recovery_context(
    recovery_calls: int,
    maximum: int,
    *,
    read_used: bool,
) -> str:
    next_action = (
        'The one targeted repository read/search has already been used. '
        'Use the existing evidence and call a workspace editing tool now.'
        if read_used
        else (
            'If one exact code location is still missing, you may use one '
            'targeted read_file or grep call. Otherwise edit immediately.'
        )
    )
    return (
        '[ForgeCode Action Recovery]\n'
        'Investigation has consumed its bounded budget while the task-local '
        'Diff is still empty. This is a focused action phase. Use an editing '
        'tool now if the relevant code is understood. '
        f'{next_action} Broad diagnostics, process commands, Git inspection, '
        'verification, and task planning '
        'are intentionally unavailable until a real workspace revision is '
        'created. A preexisting Git Diff does not satisfy this turn. '
        'finish_task is valid only for a genuine external blocker.\n'
        f'Focused calls used: {recovery_calls}/{maximum}.'
    )


def build_action_recovery_feedback(
    task_context: str,
    recovery_calls: int,
    maximum: int,
    *,
    read_used: bool,
) -> dict[str, Any]:
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            f'{render_action_recovery_context(
                recovery_calls,
                maximum,
                read_used=read_used,
            )}'
        ),
    }


def action_recovery_stuck_reason(recovery_calls: int) -> str:
    return (
        f'Action Recovery stopped after {recovery_calls} focused model calls '
        'without a task-local workspace revision, although this turn '
        'requires a change.'
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
            f'{calls_without_progress} model calls have passed since the '
            'last new workspace, task-plan, or repository evidence. All '
            'tools remain available. Reassess the root goal, use existing '
            'evidence, and choose a materially different next action. Do not '
            're-read paths already marked as fully covered. If the task needs '
            'a code change and the Diff is empty, edit the relevant code after '
            'you understand it; otherwise perform one targeted search for the '
            'specific missing fact. Do not repeat an unchanged failing action '
            'or claim tools are paused.'
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
    command = verification.command if verification is not None else 'unknown'
    revision = (
        verification.workspace_revision
        if verification is not None
        else 'unknown'
    )
    remaining = max(decision_limit - decision_calls, 0)
    return (
        '[ForgeCode Completion Ready]\n'
        f'changed paths: {changed}\n'
        f'current verification: {command} @ revision {revision}\n'
        f'reviewed Diff paths: {reviewed}\n'
        f'decision calls remaining: {remaining}\n'
        'Deterministic completion checks pass for the current revision. '
        'All tools listed in this request remain available, but open-ended '
        'discovery is no longer useful. Decide whether the user goal is '
        'satisfied. If it is, return the final answer or call finish_task '
        'alone. If it is not, make one concrete workspace edit based on the '
        'existing evidence, then verify the new revision. You may call one '
        'scoped git_diff only for a changed path not already reviewed.'
    )


def build_finalization_recovery_feedback(
    task_context: str,
    working_context: str,
    changed_paths: tuple[str, ...],
    verification: VerificationEvidence | None,
) -> dict[str, Any]:
    '''Request one bounded, tool-free synthesis after a ready-state loop.'''
    command = verification.command if verification is not None else 'unknown'
    revision = (
        verification.workspace_revision
        if verification is not None
        else 'unknown'
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
            f'({command} @ revision {revision}). State any semantic or visual '
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


def mutation_target_paths(tool_call: ToolCall) -> tuple[str, ...]:
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
    return tuple(dict.fromkeys(paths))[:5]


def render_mutation_recovery_context(
    failures: list[dict[str, Any]],
    recovery_calls: int,
    failure_count: int,
) -> str:
    '''Render durable failure state outside compactable chat history.'''
    lines = [
        '[Failed Mutation Recovery]',
        f'failed writes: {failure_count}; recovery calls: {recovery_calls}',
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
        'The goal and all tools remain active. Do not restart broad '
        'discovery or re-read covered files. Inspect only missing target '
        'lines, then retry one smaller corrected edit.'
    )
    lines.append(
        'apply_patch accepts unified diff and Begin Patch; use replace_text '
        'for an exact change. Only a real workspace revision clears this.'
    )
    return '\n'.join(lines)


def build_mutation_recovery_feedback(
    failures: list[dict[str, Any]],
    recovery_calls: int,
    failure_count: int,
    task_context: str,
) -> dict[str, Any]:
    '''Put the recovery checkpoint after a failed write result.'''
    context = render_mutation_recovery_context(
        failures,
        recovery_calls,
        failure_count,
    )
    return {
        'role': 'user',
        'content': f'{task_context}\n\n{context}',
    }


def mutation_recovery_stuck_reason(
    failures: list[dict[str, Any]],
    recovery_calls: int,
    failure_count: int,
) -> str:
    latest = failures[-1] if failures else {}
    tool = str(latest.get('tool', 'workspace tool'))
    code = str(latest.get('code', 'no_workspace_change'))
    return (
        f'Stopped after {failure_count} workspace-write attempt(s) failed '
        f'to change the task workspace and {recovery_calls} model calls '
        f'passed in edit recovery. Latest failure: {tool} [{code}].'
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
            'git_diff_path_is_directory',
            'tool_not_available_in_phase',
            'action_read_limit_reached',
        }
    )


def build_tool_protocol_feedback(
    failures: int,
    task_context: str,
) -> dict[str, Any]:
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            'The previous tool request was rejected at the argument/schema '
            'boundary. This does not mean the repository task is blocked. '
            'Read the structured error, use only the allowed fields, and '
            'retry with valid JSON or choose another tool. '
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
                f'file, use write_file with at most {retry_limit} characters. '
                'For a '
                'focused exact change, use replace_text. For structured edits, '
                f'use apply_patch with at most {retry_limit} characters. '
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
