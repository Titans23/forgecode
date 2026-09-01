'''Multi-step model and tool execution for the M1 Agent Loop.'''

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import cache
from itertools import count
import hashlib
import json
import os
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
from forge.permissions.policy import PermissionManager
from forge.permissions.risk import classify_tool_call
from forge.runtime.router import IntentRouter, TurnDecision
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
from forge.sessions.checkpoint import CheckpointError, CheckpointStore
from forge.skills import SkillManager
from forge.tasks.manager import TaskManager, task_path_matches
from forge.tools.base import ToolRegistry, ToolResult
from forge.tools.task import create_task_tools

if TYPE_CHECKING:
    from forge.mcp.manager import MCPClientManager
    from forge.sessions.store import SessionJournal, SessionStore
    from forge.tasks.state import ActiveTask


EDIT_RECOVERY_READ_TOOLS = frozenset({'read_file', 'grep'})
MUTATION_REFRESH_REQUIRED_CODES = frozenset(
    {
        'patch_context_not_found',
        'patch_rejected',
        'patch_apply_failed',
        'text_not_found',
        'text_not_unique',
    }
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
        change_exploration_limit: int = 8,
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
        skill_manager: SkillManager | None = None,
        intent_router: IntentRouter | None = None,
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
        skill_tool = (
            registry.implementation('load_skill')
            if registry is not None
            else None
        )
        self.skill_manager = skill_manager or getattr(
            skill_tool,
            'manager',
            None,
        )
        self.intent_router = intent_router
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
        self._last_repository_context = self._repository_context('')
        self._last_task_context = ''

    def _repository_context(self, query: str) -> str:
        parts = [self.context.repository.system_suffix(query)]
        if self.skill_manager is not None:
            self.skill_manager.refresh()
            parts.append(self.skill_manager.system_suffix(query))
        return '\n\n'.join(part for part in parts if part)

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
        routing_usage = TokenUsage(input_tokens=0, output_tokens=0)
        routing_model_calls = 0
        turn_decision: TurnDecision | None = None
        if self.intent_router is not None:
            route_result = await self.intent_router.route(
                prompt,
                self.task_manager.active,
                self.messages,
            )
            turn_decision = route_result.decision
            routing_usage = route_result.usage
            routing_model_calls = 1
            if self.session_journal is not None:
                self.session_journal.append(
                    'turn_routed',
                    {
                        'decision': turn_decision.model_dump(),
                        'raw_response': route_result.raw_response[:4_000],
                        'degraded_reason': route_result.degraded_reason,
                    },
                )
            yield ModelUsageUpdate(
                usage=routing_usage,
                request_usage=routing_usage,
                model_calls=1,
            )

        if self.mcp_manager is not None:
            await self.mcp_manager.ensure_connected()

        previous_task = self.task_manager.active
        preserve_active_task = False
        if turn_decision is not None:
            prompt_change_required = (
                turn_decision.requires_workspace_change
            )
            turn_change_required = bool(
                prompt_change_required
                or (
                    turn_decision.intent == 'continue_task'
                    and previous_task is not None
                    and previous_task.status != 'completed'
                    and previous_task.requires_change
                )
            )
            if turn_decision.intent in {
                'conversation',
                'task_query',
                'read_only',
                'ambiguous',
            }:
                preserve_active_task = True
            elif (
                turn_decision.intent == 'continue_task'
                and previous_task is not None
            ):
                self.task_manager.continue_active(
                    prompt,
                    requires_change=turn_change_required,
                )
            elif (
                turn_decision.intent == 'change_task'
                and turn_decision.task_relation == 'active'
                and previous_task is not None
            ):
                self.task_manager.continue_active(
                    prompt,
                    requires_change=True,
                )
                turn_change_required = True
            elif turn_decision.intent in {
                'new_task',
                'change_task',
                'continue_task',
            }:
                self.task_manager.start(
                    prompt,
                    requires_change=turn_change_required,
                )
            else:
                preserve_active_task = True
        else:
            # Embedded callers without a semantic router remain model-driven.
            # A task is created lazily on the first workspace mutation instead
            # of classifying natural language with keyword rules.
            prompt_change_required = False
            turn_change_required = False
            preserve_active_task = previous_task is not None
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
        completed_usage = routing_usage
        all_tool_calls: list[ToolCall] = []
        latest_verification: VerificationEvidence | None = None
        mutation_attempted = False
        active_task = self.task_manager.active
        if (
            active_task is not None
            and active_task.planned
            and (
                len(active_task.steps) >= 4
                or sum(len(step.title) for step in active_task.steps) >= 400
                or len(active_task.goal) >= 500
            )
        ):
            # Complex plans need more room than small edits, but must not
            # silently spend hundreds of model calls in a single turn.
            if self.max_iterations == 80:
                self.max_iterations = 160
            if self.max_tool_calls == 120:
                self.max_tool_calls = 320
            if self.max_turn_input_tokens is None:
                self.max_turn_input_tokens = 2_000_000
            if self.max_tool_protocol_recoveries == 6:
                self.max_tool_protocol_recoveries = 24
            if self.mutation_recovery_limit == 4:
                self.mutation_recovery_limit = 12
        change_required = bool(
            self.permission_manager.mode != 'plan'
            and (
                (
                    self.completion_gate is not None
                    and self.completion_gate.policy.require_changes
                )
                or (
                    self.workspace_tracker is not None
                    and turn_change_required
                )
            )
        )
        verification_required = bool(
            self.completion_gate is not None
            and (
                self.completion_gate.policy.require_verification
                or (
                    turn_decision is not None
                    and turn_decision.requires_verification
                )
            )
        )
        # The model chooses the appropriate verification command from the user
        # request and repository evidence. The harness validates only its exit
        # status and workspace revision.
        tool_attempts: dict[str, tuple[int, bool]] = {}
        successful_plan_calls = 0
        completed_workspace_calls: set[str] = set()
        completed_destructive_calls: set[str] = set()
        turn_created_paths: set[str] = set()
        calls_without_progress = 0
        change_exploration_calls = 0
        mutation_failure_count = 0
        mutation_failure_total = 0
        mutation_failure_targets: tuple[str, ...] = ()
        mutation_failures: list[dict[str, Any]] = []
        mutation_recovery_cycles = 0
        chunk_fallback_required = False
        oversized_write_checkpoint = False
        stale_task_step_checkpoint = False
        current_step_action_checkpoint = False
        mutation_recovery_context = ''
        mutation_text_recoveries = 0
        required_change_text_recoveries = 0
        finish_declaration_recoveries = 0
        false_blocker_recoveries = 0
        stagnation_plan_recoveries = 0
        stagnation_action_recoveries = 0
        planning_checkpoint_recovery = False
        planned_progress_checkpoint = False
        idempotent_resolution_checkpoint = False
        mutation_read_checkpoint = False
        mutation_action_checkpoint = False
        action_checkpoint_recovery = False
        force_synthesis = False
        verification_recovery = False
        verification_fix_checkpoint = False
        verification_action_checkpoint = False
        dependency_recovery_checkpoint = False
        dependency_verification_checkpoint = False
        verification_recheck_checkpoint = False
        unresolved_verifications: dict[str, VerificationEvidence] = {}
        tool_protocol_failures = 0
        finalization_recovery = False
        task_state_synthesis = False
        completion_ready_revision: int | None = None
        completion_decision_calls = 0
        completion_ready_context = ''
        resumed_existing_change = False
        if self.workspace_tracker is not None:
            await self.workspace_tracker.begin_turn()
            active_task = self.task_manager.active
            if (
                active_task is not None
                and active_task.workspace_paths
                and previous_task is not None
                and previous_task.status != 'completed'
                and turn_decision is not None
                and turn_decision.intent == 'continue_task'
            ):
                self.workspace_tracker.carry_existing_changes(
                    active_task.workspace_paths
                )
                resumed_existing_change = bool(
                    self.workspace_tracker.changed_paths
                )

        self._last_repository_context = self._repository_context(prompt)
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
                        model_calls=iteration - 1 + routing_model_calls,
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

            if task_state_synthesis:
                request_tools = None
            elif finalization_recovery:
                # Finalization is a synthesis phase, not another action phase.
                # Closing tools prevents a second finish_task (or an unrelated
                # call) after deterministic completion evidence is sufficient.
                request_tools = None
            elif (
                completion_ready_context
                and change_required
                and self.finish_protocol
                and finish_declaration_recoveries > 0
            ):
                request_tools = self._finish_task_tools()
            elif (
                turn_decision is not None
                and turn_decision.intent in {'conversation', 'ambiguous'}
            ):
                # A conversational-looking question may still require repository
                # evidence (for example, "can you see the play directory?").
                # Keep safe inspection tools visible and let the main model decide
                # whether it needs them instead of treating router labels as a
                # capability boundary.
                request_tools = self._read_only_tools()
            elif (
                turn_decision is not None
                and turn_decision.intent == 'task_query'
            ):
                request_tools = self._task_query_tools()
            elif (
                turn_decision is not None
                and turn_decision.intent == 'read_only'
            ):
                request_tools = self._read_only_tools()
            elif planning_checkpoint_recovery:
                request_tools = self._planning_checkpoint_tools()
            elif idempotent_resolution_checkpoint:
                request_tools = self._idempotent_resolution_tools()
            elif oversized_write_checkpoint:
                request_tools = self._oversized_write_recovery_tools()
            elif stale_task_step_checkpoint:
                request_tools = self._stale_task_step_tools()
            elif current_step_action_checkpoint:
                request_tools = self._stale_task_step_tools()
            elif planned_progress_checkpoint:
                request_tools = self._planned_progress_tools()
            elif mutation_read_checkpoint:
                request_tools = self._mutation_read_tools()
            elif mutation_action_checkpoint:
                request_tools = self._mutation_action_tools()
            elif dependency_verification_checkpoint:
                request_tools = self._dependency_verification_tools()
            elif dependency_recovery_checkpoint:
                request_tools = self._dependency_recovery_tools()
            elif verification_recheck_checkpoint:
                request_tools = self._dependency_verification_tools()
            elif verification_fix_checkpoint:
                request_tools = self._verification_fix_tools()
            elif verification_action_checkpoint:
                request_tools = self._verification_fix_tools()
            elif action_checkpoint_recovery:
                request_tools = self._action_checkpoint_tools()
            elif preserve_active_task:
                request_tools = self._read_only_tools()
            else:
                # Keep the model's normal toolset available after recoverable
                # failures. Structured errors and cached evidence guide the next
                # decision; the harness does not guess which semantic action is
                # now correct by hiding unrelated tools.
                request_tools = self._tool_definitions()
                if (
                    chunk_fallback_required
                    and self.registry is not None
                    and request_tools is not None
                ):
                    chunk_tool = self.registry.definition('write_file_chunk')
                    if (
                        chunk_tool is not None
                        and not any(
                            str(item.get('name', '')) == 'write_file_chunk'
                            for item in request_tools
                        )
                    ):
                        request_tools = [*request_tools, chunk_tool]
            request_tools = self._permission_filtered_tools(request_tools)
            request_tool_names = {
                str(definition.get('name', ''))
                for definition in request_tools or ()
            }
            enforce_declared_tool_phase = bool(
                planning_checkpoint_recovery
                or idempotent_resolution_checkpoint
                or oversized_write_checkpoint
                or stale_task_step_checkpoint
                or current_step_action_checkpoint
                or planned_progress_checkpoint
                or mutation_read_checkpoint
                or mutation_action_checkpoint
                or dependency_recovery_checkpoint
                or dependency_verification_checkpoint
                or verification_recheck_checkpoint
                or verification_fix_checkpoint
                or verification_action_checkpoint
                or action_checkpoint_recovery
                or task_state_synthesis
                or finalization_recovery
                or bool(
                    completion_ready_context
                    and change_required
                    and self.finish_protocol
                    and finish_declaration_recoveries > 0
                )
            )
            request_system_prompt = self._request_system_prompt(
                force_synthesis=force_synthesis,
                mutation_recovery_context=mutation_recovery_context,
                finalization_recovery=finalization_recovery,
                task_state_synthesis=task_state_synthesis,
                completion_ready_context=completion_ready_context,
                change_required=change_required,
                mutation_attempted=mutation_attempted,
                verification_required=verification_required,
                verification_recovery=verification_recovery,
                resumed_existing_change=resumed_existing_change,
            )
            context_window_tokens = getattr(
                self.client, 'context_window', None
            )
            reserved_output_tokens = getattr(
                self.client, 'max_tokens', 0
            )
            active_task = self.task_manager.active
            active_scope_hints = (
                active_task.scope_hints if active_task is not None else ()
            )
            active_task_goal = active_task.goal if active_task is not None else ''
            compaction_required = self.context.compaction_required(
                request_messages,
                system_prompt=request_system_prompt,
                repository_context=self._last_repository_context,
                tools=request_tools,
                context_window_tokens=context_window_tokens,
                reserved_output_tokens=reserved_output_tokens,
                scope_hints=active_scope_hints,
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
                        model_calls=iteration - 1 + routing_model_calls,
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
                    scope_hints=active_scope_hints,
                    task_goal=active_task_goal,
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
                    messages=self.context.prepare(
                        request_messages,
                        scope_hints=active_scope_hints,
                    ),
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
                            model_calls=iteration + routing_model_calls,
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
                            scope_hints=active_scope_hints,
                            task_goal=active_task_goal,
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
                            model_calls=iteration + routing_model_calls,
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
                            model_calls=iteration + routing_model_calls,
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
                            model_calls=iteration + routing_model_calls,
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
            if tool_calls:
                # Protocol recovery limits consecutive malformed provider
                # responses. A valid structured response restores the budget
                # for long-running tasks.
                protocol_recoveries = 0

            if (
                finalization_recovery
                and tool_calls
                and not (
                    change_required
                    and len(tool_calls) == 1
                    and tool_calls[0].name == 'finish_task'
                )
            ):
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
                        model_calls=iteration + routing_model_calls,
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
                serialized_tool = serialized_tool_arguments(
                    text,
                    request_tools,
                )
                if (
                    serialized_tool is not None
                    and protocol_recoveries < self.max_protocol_recoveries
                ):
                    protocol_recoveries += 1
                    request_messages[-1] = {
                        'role': 'assistant',
                        'content': (
                            '[Malformed serialized tool arguments omitted; '
                            f'intended tool: {serialized_tool}.]'
                        ),
                    }
                    request_messages.append(
                        {
                            'role': 'user',
                            'content': (
                                'ForgeCode protocol recovery: your previous '
                                'response printed arguments for '
                                f'{serialized_tool!r} as ordinary text, so '
                                'nothing executed. Call that tool through the '
                                'structured tool interface now. Keep the retry '
                                'focused; do not repeat the JSON as prose.'
                            ),
                        }
                    )
                    calls_without_progress = 0
                    force_synthesis = False
                    continue
                protocol_recoveries = 0
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
                            model_calls=iteration + routing_model_calls,
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
                if self._pending_required_change(change_required):
                    if required_change_text_recoveries < 1:
                        required_change_text_recoveries += 1
                        force_synthesis = True
                        calls_without_progress = 0
                        request_messages.append(
                            build_required_change_text_recovery_feedback()
                        )
                        continue
                    reason = required_change_block_reason()
                    self.task_manager.stuck((reason,))
                    self.messages[:] = request_messages
                    yield CompletionBlocked(attempt=1, reasons=(reason,))
                    yield TurnCompleted(
                        result=TurnResult(
                            text=reason,
                            usage=completed_usage,
                            last_request_usage=request_usage,
                            model_calls=iteration + routing_model_calls,
                            tool_calls=tuple(all_tool_calls),
                            status='stuck',
                            changed_paths=(),
                            verification=latest_verification,
                            completion_reasons=(reason,),
                        )
                    )
                    return
                if (
                    self.finish_protocol
                    and turn_decision is not None
                    and change_required
                    and not finalization_recovery
                ):
                    if finish_declaration_recoveries < 1:
                        finish_declaration_recoveries += 1
                        calls_without_progress = 0
                        request_messages.append(
                            {
                                'role': 'user',
                                'content': (
                                    'ForgeCode completion protocol: this is a '
                                    'workspace-change task. Do not complete it '
                                    'with prose alone. If the original goal is '
                                    'satisfied, call finish_task alone with an '
                                    'honest structured status and summary. If it '
                                    'is not satisfied, use the normal tools for '
                                    'the smallest concrete next action.'
                                ),
                            }
                        )
                        continue
                    reason = (
                        'The workspace-change task did not receive a structured '
                        'finish_task declaration after one protocol retry.'
                    )
                    self.task_manager.stuck((reason,))
                    self.messages[:] = request_messages
                    yield TurnCompleted(
                        result=TurnResult(
                            text=reason,
                            usage=completed_usage,
                            last_request_usage=request_usage,
                            model_calls=iteration + routing_model_calls,
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
                                model_calls=iteration + routing_model_calls,
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
                if not preserve_active_task:
                    self.task_manager.complete()
                self.messages[:] = request_messages
                self.context.capture_explicit_memory(prompt)
                yield TurnCompleted(
                    result=TurnResult(
                        text=complete_text,
                        usage=completed_usage,
                        last_request_usage=request_usage,
                        model_calls=iteration + routing_model_calls,
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
                        model_calls=iteration + routing_model_calls,
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
            delete_only_allowed = bool(
                turn_decision is None
                or turn_decision.allows_delete_only is not False
            )
            destructive_batch_ids: set[str] = set()
            agent_created_destructive_ids: set[str] = set()
            batch_created_targets: set[str] = set()
            constructive_batch_write = False
            for candidate in tool_calls:
                candidate_effect = self.registry.effect(candidate.name)
                candidate_request = (
                    self.registry.permission_request(
                        candidate.name,
                        candidate.arguments,
                    )
                    or classify_tool_call(candidate, candidate_effect)
                )
                candidate_targets = mutation_target_paths(
                    candidate,
                    maximum=None,
                )
                if candidate_request.capability == 'file.delete':
                    destructive_batch_ids.add(candidate.id)
                    if candidate_targets and all(
                        target in batch_created_targets
                        or (
                            self.workspace_tracker is not None
                            and self.workspace_tracker.baseline.files.get(target)
                            == 'missing'
                        )
                        for target in candidate_targets
                    ):
                        agent_created_destructive_ids.add(candidate.id)
                elif candidate_effect == 'workspace_write':
                    for target in candidate_targets:
                        if not (self.task_manager.root / target).exists():
                            batch_created_targets.add(target)
                if candidate_effect == 'workspace_write' and (
                    candidate_request.capability != 'file.delete'
                    or patch_has_constructive_operation(candidate)
                ):
                    constructive_batch_write = True
            reject_delete_only_batch = bool(
                destructive_batch_ids
                and not constructive_batch_write
                and not delete_only_allowed
            )
            offered_tool_names = frozenset(
                str(definition.get('name', ''))
                for definition in (request_tools or ())
                if definition.get('name')
            )
            tool_results: list[tuple[ToolCall, ToolResult]] = []
            workspace_write_batch_failure: ToolResult | None = None
            workspace_write_batch_boundary = False
            last_workspace_change_position = -1
            last_workspace_write_change_position = -1
            task_progressed = False
            evidence_progressed = False
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
                if tool_call.name == 'read_file':
                    revision_now = (
                        self.workspace_tracker.revision
                        if self.workspace_tracker is not None
                        else 0
                    )
                    if self.working_state.covers_read(tool_call, revision_now):
                        arguments = dict(tool_call.arguments)
                        try:
                            start_line = int(arguments.get('start_line', 1))
                            raw_end = arguments.get('end_line')
                            end_line = (
                                start_line + WorkingState.MAX_REPLAY_LINES - 1
                                if raw_end is None
                                else int(raw_end)
                            )
                        except (TypeError, ValueError):
                            pass
                        else:
                            maximum_end = (
                                start_line + WorkingState.MAX_REPLAY_LINES - 1
                            )
                            if end_line > maximum_end:
                                arguments['start_line'] = start_line
                                arguments['end_line'] = maximum_end
                                tool_call = ToolCall(
                                    index=tool_call.index,
                                    id=tool_call.id,
                                    name=tool_call.name,
                                    arguments=arguments,
                                )
                tool_effect = self.registry.effect(tool_call.name)
                mutation_refresh_sibling_write = bool(
                    mutation_read_checkpoint
                    and tool_call.name
                    in {
                        'apply_patch',
                        'replace_text',
                        'write_file',
                        'write_file_chunk',
                    }
                    and mutation_target_paths(tool_call, maximum=None)
                    and set(
                        mutation_target_paths(tool_call, maximum=None)
                    ).isdisjoint(mutation_failure_targets)
                )
                phase_rejection = (
                    None
                    if (
                        not enforce_declared_tool_phase
                        or tool_call.name in offered_tool_names
                        or tool_call.name not in self.registry.names
                        or mutation_refresh_sibling_write
                    )
                    else ToolResult.fail(
                        'tool_not_available_in_phase',
                        f'{tool_call.name} is not available in the current '
                        'execution phase. Choose from the tools declared in '
                        'this model request.',
                        details={
                            'tool': tool_call.name,
                            'offered': sorted(offered_tool_names),
                        },
                    )
                )
                if (
                    phase_rejection is None
                    and verification_fix_checkpoint
                    and tool_call.name == 'verify'
                ):
                    phase_rejection = ToolResult.fail(
                        'verification_requires_correction',
                        'Verification was not executed because the failing '
                        'revision has not been corrected yet. Make a focused '
                        'edit first, then run the exact verification command.',
                        metadata={
                            'verification_retry_blocked': True,
                        },
                    )
                if (
                    phase_rejection is None
                    and verification_action_checkpoint
                    and tool_call.name in {'read_file', 'grep', 'verify'}
                ):
                    phase_rejection = ToolResult.ok(
                        'The verification failure already received its targeted '
                        'evidence batch. Preserved that evidence and redirected '
                        'the next request to the concrete correction.',
                        metadata={
                            'status': 'already_completed',
                            'verification_read_closed': True,
                        },
                    )
                if (
                    phase_rejection is None
                    and obvious_probe_write(tool_call, prompt)
                ):
                    phase_rejection = ToolResult.fail(
                        'placeholder_write_denied',
                        'Workspace write denied because it only creates an '
                        'obvious placeholder, probe, noop, or temporary value. '
                        'Make the actual task-relevant implementation edit.',
                    )
                if (
                    phase_rejection is None
                    and tool_call.name == 'task_plan'
                    and successful_plan_calls >= 1
                ):
                    phase_rejection = ToolResult.ok(
                        'A task plan was already created during this turn; '
                        'preserved the existing plan and current step.',
                        metadata={
                            'status': 'already_completed',
                            'recommended_tool': 'task_update',
                        },
                    )
                if (
                    phase_rejection is None
                    and not delete_only_allowed
                    and tool_call.id in destructive_batch_ids
                    and protected_task_input_delete(
                        tool_call,
                        (
                            self.task_manager.active.scope_hints
                            if self.task_manager.active is not None
                            else ()
                        ),
                    )
                ):
                    phase_rejection = ToolResult.fail(
                        'protected_task_input_delete',
                        'This implementation task cannot delete its task '
                        'specification or explicit scope root. Preserve task.md, '
                        'AGENTS.md, and the scoped project directory; make '
                        'focused edits inside that directory instead. Deleting '
                        'them is allowed only for an explicit user-requested '
                        'cleanup or removal task.',
                        details={
                            'targets': list(
                                mutation_target_paths(tool_call, maximum=None)
                            ),
                        },
                    )
                if (
                    phase_rejection is None
                    and reject_delete_only_batch
                    and tool_call.id in destructive_batch_ids
                    and not (
                        tool_call.id in agent_created_destructive_ids
                        and agent_created_document_cleanup(tool_call)
                    )
                ):
                    phase_rejection = ToolResult.fail(
                        'delete_only_batch_requires_replacement',
                        'This task is an implementation or refactor, but this '
                        'model response only deletes files. Create replacements '
                        'first or combine deletions with constructive edits in '
                        'the same response. Delete-only completion is reserved '
                        'for user-requested cleanup or removal tasks.',
                        details={
                            'targets': list(
                                mutation_target_paths(tool_call, maximum=None)
                            ),
                        },
                    )
                if (
                    tool_effect == 'workspace_write'
                    and self.task_manager.active is None
                ):
                    self.task_manager.start(
                        prompt,
                        requires_change=True,
                    )
                    self._last_task_context = self.task_manager.system_suffix()
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
                write_correction: tuple[str, str, str] | None = None
                if tool_call.name == 'write_file':
                    write_targets = mutation_target_paths(
                        tool_call,
                        maximum=None,
                    )
                    safe_correction_paths = set(turn_created_paths)
                    if self.workspace_tracker is not None:
                        safe_correction_paths.update(
                            self.workspace_tracker.changed_paths
                        )
                    active_task = self.task_manager.active
                    if (
                        active_task is not None
                        and active_task.requires_change
                        and not self.task_manager.outside_scope(
                            tuple(write_targets)
                        )
                    ):
                        safe_correction_paths.update(write_targets)
                    content = tool_call.arguments.get('content')
                    if (
                        len(write_targets) == 1
                        and write_targets[0] in safe_correction_paths
                        and isinstance(content, str)
                    ):
                        current_path = (
                            self.task_manager.root / write_targets[0]
                        )
                        try:
                            with current_path.open(
                                'r',
                                encoding='utf-8',
                                newline='',
                            ) as current_source:
                                current_content = current_source.read()
                        except (OSError, UnicodeDecodeError):
                            pass
                        else:
                            destructive_shrink = bool(
                                len(current_content) >= 512
                                and len(content) * 2 < len(current_content)
                            )
                            if destructive_shrink:
                                phase_rejection = ToolResult.fail(
                                    'whole_file_correction_too_small',
                                    'The proposed whole-file correction would '
                                    'discard more than half of the existing '
                                    'substantive file. Use apply_patch or '
                                    'replace_text for a focused repair, or use '
                                    'write_file_chunk for an intentional complete '
                                    'replacement.',
                                    metadata={
                                        'path': write_targets[0],
                                        'current_characters': len(current_content),
                                        'proposed_characters': len(content),
                                    },
                                )
                            else:
                                write_correction = (
                                    write_targets[0],
                                    content,
                                    hashlib.sha256(
                                        current_content.encode('utf-8')
                                    ).hexdigest(),
                                )
                permission_rejection: ToolResult | None = None
                completed_write_replay: ToolResult | None = None
                destructive_replay: ToolResult | None = None
                permission_request = None
                workspace_write_identity = (
                    tool_call_identity(tool_call)
                    if tool_effect == 'workspace_write'
                    else ''
                )
                destructive_identity = ''
                if (
                    workspace_write_identity
                    and workspace_write_identity in completed_workspace_calls
                ):
                    completed_write_replay = ToolResult.ok(
                        'Skipped an identical workspace write that already '
                        'succeeded during this turn.',
                        metadata={'status': 'already_completed'},
                    )
                elif (
                    hook_rejection is None
                    and phase_rejection is None
                ):
                    permission_request = (
                        self.registry.permission_request(
                            tool_call.name,
                            tool_call.arguments,
                        )
                        if self.registry is not None
                        else None
                    ) or classify_tool_call(tool_call, tool_effect)
                    if permission_request.capability == 'file.delete':
                        destructive_identity = tool_call_identity(tool_call)
                    if destructive_identity in completed_destructive_calls:
                        destructive_replay = ToolResult.ok(
                            'Skipped an identical delete that already succeeded '
                            'during this turn.',
                            metadata={'status': 'already_completed'},
                        )
                    elif tool_call.id in agent_created_destructive_ids:
                        # Reverting a path created after the immutable turn
                        # baseline cannot delete user-owned pre-turn content.
                        pass
                    else:
                        permission_decision = (
                            await self.permission_manager.authorize(
                                permission_request
                            )
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
                scope_rejection: ToolResult | None = None
                if (
                    tool_effect == 'workspace_write'
                    and hook_rejection is None
                    and phase_rejection is None
                    and permission_rejection is None
                ):
                    targets = mutation_target_paths(tool_call, maximum=None)
                    outside_scope = tuple(
                        path
                        for path in self.task_manager.outside_scope(targets)
                        if not (
                            self.completion_gate is not None
                            and any(
                                task_path_matches(path, allowed)
                                for allowed in self.completion_gate.policy.allowed_paths
                            )
                            and not any(
                                task_path_matches(path, forbidden)
                                for forbidden in self.completion_gate.policy.forbidden_paths
                            )
                        )
                    )
                    if outside_scope:
                        scope_rejection = ToolResult.fail(
                            'outside_task_scope',
                            'Workspace write denied because its target is outside '
                            'the active task scope.',
                            details={
                                'targets': list(outside_scope),
                                'allowed': list(
                                    self.task_manager.active.scope_hints
                                    if self.task_manager.active is not None
                                    else ()
                                ),
                            },
                        )
                # Content relevance and deletion intent are semantic
                # decisions for the model. Deterministic path and permission
                # checks above remain the enforcement boundary.
                if (
                    tool_effect == 'workspace_write'
                    and phase_rejection is None
                ):
                    mutation_attempted = True
                    change_required = True
                if (
                    tool_call.name == 'finish_task'
                    and tool_call.arguments.get('task_kind') == 'change'
                ):
                    change_required = True
                if (
                    tool_effect == 'workspace_write'
                    and phase_rejection is None
                    and self.workspace_tracker is not None
                ):
                    self.workspace_tracker.watch_paths(
                        mutation_target_paths(tool_call)
                    )
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
                    and not (
                        tool_call.name == 'create_directory'
                        and previous_success
                    )
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
                if phase_rejection is not None:
                    result = phase_rejection
                elif hook_rejection is not None:
                    result = hook_rejection
                elif completed_write_replay is not None:
                    result = completed_write_replay
                elif destructive_replay is not None:
                    result = destructive_replay
                elif permission_rejection is not None:
                    result = permission_rejection
                elif scope_rejection is not None:
                    result = scope_rejection
                elif finish_mixed:
                    result = ToolResult.fail(
                        'finish_must_be_alone',
                        'finish_task must be the only tool call in its model '
                        'response. Complete other actions first, then declare '
                        'the outcome in a separate response.',
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
                        checkpoint_mutation_paths(
                            self.task_manager.root,
                            tool_call,
                        )
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
                        if write_correction is not None:
                            implementation = self.registry.implementation(
                                'write_file'
                            )
                            correct_existing = getattr(
                                implementation,
                                'correct_existing',
                                None,
                            )
                            if not callable(correct_existing):
                                result = ToolResult.fail(
                                    'internal_tool_unavailable',
                                    'ForgeCode could not access the internal '
                                    'write_file correction path.',
                                )
                            else:
                                correction_path, correction_content, digest = (
                                    write_correction
                                )
                                result = await correct_existing(
                                    path=correction_path,
                                    content=correction_content,
                                    expected_sha256=digest,
                                )
                        else:
                            result = await self.registry.execute(
                                tool_call.name,
                                tool_call.arguments,
                            )
                        if (
                            tool_call.name == 'create_directory'
                            and not result.success
                            and result.error is not None
                            and result.error.code == 'directory_already_exists'
                        ):
                            # "Ensure this directory exists" is idempotent at
                            # the orchestration boundary. Keep the direct tool's
                            # diagnostic while avoiding a failed agent action.
                            result = ToolResult.ok(
                                result.error.message,
                                metadata={
                                    **result.error.details,
                                    'status': 'already_completed',
                                },
                            )
                        if (
                            tool_effect == 'workspace_write'
                            and result.success
                            and result.metadata.get('status')
                            != 'already_completed'
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
                if (
                    tool_call.name in {'write_file', 'write_file_chunk'}
                    and result.success
                    and result.metadata.get('created') is True
                    and isinstance(result.metadata.get('path'), str)
                ):
                    turn_created_paths.add(str(result.metadata['path']))
                if (
                    workspace_write_identity
                    and completed_write_replay is None
                    and result.success
                    and result.metadata.get('status') != 'already_completed'
                ):
                    completed_workspace_calls.add(workspace_write_identity)
                if (
                    destructive_identity
                    and destructive_replay is None
                    and result.success
                ):
                    completed_destructive_calls.add(destructive_identity)
                if tool_call.name == 'finish_task' and result.success:
                    finish_reasons = await self._finish_rejection_reasons(
                        result,
                        mutation_attempted=mutation_attempted,
                        change_required=change_required,
                        verification=latest_verification,
                        verification_required=verification_required,
                    )
                    if unresolved_verifications:
                        unresolved_commands = ', '.join(
                            f'{kind}: {evidence.command!r} in '
                            f'{evidence.cwd or "."!r}'
                            for kind, evidence in unresolved_verifications.items()
                        )
                        finish_reasons = (
                            'Earlier verification failures remain unresolved by '
                            'the same commands on the current revision: '
                            f'{unresolved_commands}.',
                            *finish_reasons,
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
                if oversized_write_file_result(tool_call, result):
                    chunk_fallback_required = True
                    oversized_write_checkpoint = True
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
                if (
                    result.error is not None
                    and result.error.code == 'permission_denied'
                ):
                    terminal_permission_denial = result
                    break
                if tool_effect == 'workspace_write' and not result.success:
                    workspace_write_batch_failure = result
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
                            self.task_manager.observe_mutation_paths(change.paths)
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
                elif (
                    tool_effect == 'workspace_write'
                    and result.success
                    and result.metadata.get('status') != 'already_completed'
                ):
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
                if (
                    tool_effect == 'workspace_write'
                    and result.metadata.get('status') != 'already_completed'
                ):
                    workspace_write_batch_boundary = True
                    if workspace_write_batch_failure is not None:
                        break
                    next_call = (
                        tool_calls[tool_position + 1]
                        if tool_position + 1 < len(tool_calls)
                        else None
                    )
                    if (
                        next_call is None
                        or self.registry.effect(next_call.name)
                        != 'workspace_write'
                    ):
                        break
                if tool_call.name == 'verify':
                    observed_verification = verification_from_result(
                        result,
                        workspace_revision=(
                            self.workspace_tracker.revision
                            if self.workspace_tracker is not None
                            else None
                        ),
                    )
                    verification_recovery = False
                    if observed_verification is not None:
                        latest_verification = observed_verification
                        verification_key = verification_obligation(
                            latest_verification.command
                        )
                        if verification_key and latest_verification.success:
                            unresolved_verifications.pop(verification_key, None)
                        elif verification_key:
                            unresolved_verifications[verification_key] = (
                                latest_verification
                            )
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
                if (
                    tool_call.name == 'explore_repository'
                    and result.success
                ):
                    task_progressed = True
                if (
                    tool_call.name == 'task_plan'
                    and result.success
                    and result.metadata.get('status') != 'already_completed'
                ):
                    successful_plan_calls += 1
                    task_progressed = True
                    step_count = result.metadata.get('step_count')
                    step_refs = result.metadata.get('steps', ())
                    title_characters = sum(
                        len(str(step.get('title', '')))
                        for step in step_refs
                        if isinstance(step, dict)
                    ) if isinstance(step_refs, (list, tuple)) else 0
                    if (
                        isinstance(step_count, int)
                        and (
                            step_count >= 4
                            or title_characters >= 400
                            or (
                                self.task_manager.active is not None
                                and len(self.task_manager.active.goal) >= 500
                            )
                        )
                    ):
                        if self.max_iterations == 80:
                            self.max_iterations = 160
                        if self.max_tool_calls == 120:
                            self.max_tool_calls = 320
                        if self.max_turn_input_tokens is None:
                            self.max_turn_input_tokens = 2_000_000
                        if self.max_tool_protocol_recoveries == 6:
                            self.max_tool_protocol_recoveries = 24
                        if self.mutation_recovery_limit == 4:
                            self.mutation_recovery_limit = 12
                if (
                    tool_call.name == 'task_update'
                    and result.success
                    and result.metadata.get('status') != 'already_completed'
                ):
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
            elif workspace_write_batch_failure is not None:
                for skipped_call in tool_calls[len(tool_results):]:
                    tool_results.append(
                        (
                            skipped_call,
                            ToolResult.fail(
                                'not_executed_after_workspace_write_failure',
                                'Not executed because an earlier workspace '
                                'write failed. Correct that edit first.',
                            ),
                        )
                    )
            elif workspace_write_batch_boundary:
                for skipped_call in tool_calls[len(tool_results):]:
                    tool_results.append(
                        (
                            skipped_call,
                            ToolResult.ok(
                                'Deferred because an earlier tool changed the '
                                'workspace. Reconsider this call against the '
                                'new workspace revision.',
                                metadata={'status': 'deferred_after_write'},
                            ),
                        )
                    )
            tool_result_message = build_tool_result_message(tool_results)
            self.context.persist_tool_result_message(tool_result_message)
            request_messages.append(tool_result_message)
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
            if any(
                oversized_write_file_result(call, result)
                for call, result in tool_results
            ):
                request_messages.append(
                    build_oversized_write_recovery_feedback(
                        self.task_manager.system_suffix()
                    )
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
                        model_calls=iteration + routing_model_calls,
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
                finish_call = next(
                    (
                        call
                        for call, _ in reversed(tool_results)
                        if call.name == 'finish_task'
                    ),
                    None,
                )
                active_finish_task = self.task_manager.active
                premature_planned_finish = bool(
                    finish_call is not None
                    and finish_call.arguments.get('task_kind') != 'change'
                    and change_required
                    and active_finish_task is not None
                    and active_finish_task.planned
                    and any(
                        step.status != 'completed'
                        for step in active_finish_task.steps
                    )
                    and (
                        self.workspace_tracker is None
                        or not self.workspace_tracker.changed_paths
                    )
                )
                if (
                    premature_planned_finish
                    and finish_declaration_recoveries < 1
                ):
                    finish_declaration_recoveries += 1
                    calls_without_progress = 0
                    planning_checkpoint_recovery = False
                    action_checkpoint_recovery = False
                    mutation_read_checkpoint = False
                    mutation_action_checkpoint = False
                    planned_progress_checkpoint = True
                    request_messages.append(
                        {
                            'role': 'user',
                            'content': (
                                'ForgeCode completion correction: inspection '
                                'cannot complete this requested workspace change. '
                                'Continue the existing task plan now with one '
                                'task-relevant edit or task_update transition. '
                                'Do not re-plan, re-explore, or return another '
                                'completion declaration before implementation.'
                            ),
                        }
                    )
                    terminal_finish_reasons = ()
                    continue
                correctable_kind_mismatch = bool(
                    finish_call is not None
                    and finish_call.arguments.get('task_kind') != 'change'
                    and self.workspace_tracker is not None
                    and self.workspace_tracker.changed_paths
                    and latest_verification is not None
                    and latest_verification.success
                    and latest_verification.workspace_revision
                    == self.workspace_tracker.revision
                )
                if (
                    correctable_kind_mismatch
                    and finish_declaration_recoveries < 1
                ):
                    finish_declaration_recoveries += 1
                    calls_without_progress = 0
                    request_messages.append(
                        {
                            'role': 'user',
                            'content': (
                                'ForgeCode completion correction: the previous '
                                '`finish_task` declaration was rejected. Reuse '
                                'the exact rejection reasons above and call '
                                '`finish_task` once more with a declaration that '
                                'matches the current workspace changes and '
                                'verification evidence. Do not perform more '
                                'exploration or create probe files.'
                            ),
                        }
                    )
                    terminal_finish_reasons = ()
                    continue
                correctable_read_only_kind = bool(
                    finish_call is not None
                    and finish_call.arguments.get('task_kind') == 'change'
                    and not mutation_attempted
                    and not turn_change_required
                    and not (
                        self.completion_gate is not None
                        and self.completion_gate.policy.require_changes
                    )
                    and self.workspace_tracker is not None
                    and not self.workspace_tracker.changed_paths
                    and self.working_state.evidence_paths
                )
                if (
                    correctable_read_only_kind
                    and finish_declaration_recoveries < 1
                ):
                    finish_declaration_recoveries += 1
                    calls_without_progress = 0
                    change_required = False
                    request_messages.append(
                        {
                            'role': 'user',
                            'content': (
                                'ForgeCode completion correction: this turn '
                                'inspected existing code and produced no workspace '
                                'change. Reuse the repository and verification '
                                'evidence already collected, then call '
                                'finish_task once more with '
                                'task_kind=inspection. Do not edit files, '
                                're-read evidence, or run another verification.'
                            ),
                        }
                    )
                    terminal_finish_reasons = ()
                    continue
                correctable_workspace_finish = bool(
                    finish_call is not None
                    and finish_call.arguments.get('task_kind') == 'change'
                    and self.workspace_tracker is not None
                    and self.workspace_tracker.changed_paths
                    and any(
                        'deterministic Patch error' in reason
                        for reason in terminal_finish_reasons
                    )
                )
                if (
                    correctable_workspace_finish
                    and finish_declaration_recoveries < 1
                ):
                    finish_declaration_recoveries += 1
                    calls_without_progress = 0
                    planning_checkpoint_recovery = False
                    planned_progress_checkpoint = False
                    action_checkpoint_recovery = False
                    mutation_action_checkpoint = True
                    rendered_reasons = '\n'.join(terminal_finish_reasons)
                    request_messages.append(
                        {
                            'role': 'user',
                            'content': (
                                '[ForgeCode Final Correction]\n'
                                'The completion gate found a correctable problem '
                                'in the current workspace change:\n'
                                f'{rendered_reasons}\n'
                                'Use the reported path and line evidence to make '
                                'one focused correction now. Do not re-plan, '
                                'broaden exploration, or finish before correcting '
                                'and re-verifying the final revision.'
                            ),
                        }
                    )
                    terminal_finish_reasons = ()
                    continue
                correctable_false_blocker = bool(
                    finish_call is not None
                    and finish_call.arguments.get('task_kind') == 'change'
                    and finish_call.arguments.get('status') == 'blocked'
                    and change_required
                    and not mutation_attempted
                    and false_blocker_recoveries < 2
                )
                if correctable_false_blocker:
                    false_blocker_recoveries += 1
                    calls_without_progress = 0
                    planning_checkpoint_recovery = False
                    planned_progress_checkpoint = False
                    mutation_read_checkpoint = False
                    mutation_action_checkpoint = False
                    action_checkpoint_recovery = True
                    request_messages.append(
                        {
                            'role': 'user',
                            'content': (
                                '[ForgeCode Action Recovery]\n'
                                'The blocked declaration was rejected because no '
                                'external blocker exists. Editing tools are '
                                'available in this request. Use the repository '
                                'evidence already collected to make one concrete '
                                'task-relevant edit now, then verify it. Do not '
                                're-read unchanged files or declare the task '
                                'blocked again unless a new external condition is '
                                'reported by a tool.'
                            ),
                        }
                    )
                    terminal_finish_reasons = ()
                    continue
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
                        model_calls=iteration + routing_model_calls,
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
                if not preserve_active_task:
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
                        model_calls=iteration + routing_model_calls,
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
            substantive_write_progressed = any(
                changed and result.success
                for _, _, result, changed in workspace_write_results
            )
            if (
                workspace_write_progressed
                and not substantive_write_progressed
                and last_workspace_change_position
                == last_workspace_write_change_position
            ):
                workspace_progressed = False
                workspace_write_progressed = False
            batch_reverted_to_baseline = (
                workspace_progressed
                and self.workspace_tracker is not None
                and not self.workspace_tracker.changed_paths
            )
            if batch_reverted_to_baseline:
                workspace_progressed = False
                workspace_write_progressed = False
            write_resolves_active_failure = (
                not mutation_failures or workspace_progressed
            )
            verification_edit_progressed = bool(
                workspace_progressed
                and (
                    verification_fix_checkpoint
                    or verification_action_checkpoint
                )
            )
            if workspace_progressed:
                chunk_fallback_required = False
                oversized_write_checkpoint = False
                stale_task_step_checkpoint = False
                current_step_action_checkpoint = False
                verification_fix_checkpoint = False
                verification_action_checkpoint = False
                dependency_recovery_checkpoint = False
                dependency_verification_checkpoint = False
                verification_recheck_checkpoint = verification_edit_progressed
                if write_resolves_active_failure:
                    mutation_failure_count = 0
                    mutation_failure_total = 0
                    mutation_failure_targets = ()
                    mutation_failures.clear()
                    mutation_recovery_cycles = 0
                mutation_recovery_context = ''
                mutation_text_recoveries = 0
                force_synthesis = False
                completion_ready_revision = None
                completion_decision_calls = 0
                completion_ready_context = ''
                mutation_read_checkpoint = False
                mutation_action_checkpoint = False
                idempotent_resolution_checkpoint = False
                # A real new revision begins a fresh implementation phase. Do
                # not let exploration consumed before that revision exhaust the
                # recovery opportunity needed to inspect or correct it.
                stagnation_plan_recoveries = 0
                stagnation_action_recoveries = 0
                # A successful write can be only one part of a larger change.
                # Keep implementation tools available; the stagnation and
                # completion paths enforce fresh verification before finish.
                verification_recovery = False
            idempotent_write_targets = {
                target
                for _, call, result, changed in workspace_write_results
                if (
                    result.success
                    and not changed
                    and result.metadata.get('status') == 'already_completed'
                    and result.metadata.get('resolution_checkpoint') is True
                )
                for target in mutation_target_paths(call, maximum=None)
            }
            known_changed_targets = set(
                self.workspace_tracker.changed_paths
                if self.workspace_tracker is not None
                else ()
            )
            idempotent_resolves_active_failure = bool(
                idempotent_write_targets
                and (
                    idempotent_write_targets & set(mutation_failure_targets)
                    or idempotent_write_targets & known_changed_targets
                )
            )
            if idempotent_resolves_active_failure:
                resolved_targets = tuple(sorted(idempotent_write_targets))
                mutation_failure_count = 0
                mutation_failure_total = 0
                mutation_failure_targets = ()
                mutation_failures.clear()
                mutation_recovery_cycles = 0
                mutation_recovery_context = ''
                mutation_text_recoveries = 0
                mutation_read_checkpoint = False
                mutation_action_checkpoint = False
                planned_progress_checkpoint = False
                idempotent_resolution_checkpoint = True
                calls_without_progress = 0
                request_messages.append(
                    build_idempotent_resolution_feedback(
                        self.task_manager.system_suffix(),
                        resolved_targets,
                    )
                )
            protocol_failure = bool(tool_results) and all(
                is_tool_protocol_failure(result)
                for _, result in tool_results
            )
            pending_write_results = [
                (call, result)
                for position, call, result, changed
                in workspace_write_results
                if (
                    position > last_workspace_write_change_position
                    and not changed
                    and not is_tool_protocol_failure(result)
                    and result.metadata.get('status') != 'already_completed'
                    and (
                        result.error is None
                        or result.error.code
                        not in {
                                'permission_denied',
                                'repeated_tool_call',
                                'outside_task_scope',
                                'directory_already_exists',
                                'not_executed_after_workspace_write_failure',
                            }
                    )
                )
            ]
            if batch_reverted_to_baseline and workspace_write_results:
                _, last_call, last_result, _ = workspace_write_results[-1]
                pending_write_results = [(last_call, last_result)]
            if pending_write_results:
                planned_progress_checkpoint = False
                mutation_read_checkpoint = False
                mutation_action_checkpoint = False
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
                    mutation_recovery_cycles = 0
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
            recovery_file_targets = tuple(
                target
                for target in mutation_failure_targets
                if not target.startswith('@tool:')
            )
            refresh_required_failure_count = sum(
                str(failure.get('code', ''))
                in MUTATION_REFRESH_REQUIRED_CODES
                for failure in mutation_failures
            )
            mutation_requires_refreshed_evidence = bool(
                recovery_file_targets
                and refresh_required_failure_count >= 2
            )
            if pending_write_results and mutation_requires_refreshed_evidence:
                mutation_read_checkpoint = True
            recovery_read_succeeded = any(
                call.name in EDIT_RECOVERY_READ_TOOLS
                and result.success
                and (
                    not recovery_file_targets
                    or bool(
                        set(mutation_target_paths(call, maximum=None))
                        & set(recovery_file_targets)
                    )
                )
                for call, result in tool_results
            )
            if mutation_failures:
                if (
                    pending_write_results
                    and not protocol_failure
                    and not recovery_read_succeeded
                ):
                    mutation_recovery_cycles += 1
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
                    not protocol_failure
                    and not recovery_read_succeeded
                    and (
                        mutation_recovery_cycles
                        >= self.mutation_recovery_limit
                        or mutation_failure_count
                        >= self.mutation_recovery_limit
                        or mutation_failure_total
                        >= self.mutation_recovery_limit * 2
                    )
                ):
                    reason = (
                        'Stopped after '
                        f'{mutation_recovery_cycles} unresolved edit-recovery '
                        'cycle(s) since the latest successful workspace '
                        'revision while correcting '
                        f'{", ".join(mutation_failure_targets)}. Earlier '
                        'successful workspace changes remain preserved.'
                        if mutation_recovery_cycles
                        >= self.mutation_recovery_limit
                        else mutation_recovery_stuck_reason(
                            mutation_failures,
                            mutation_failure_total,
                        )
                    )
                    self.task_manager.stuck((reason,))
                    self.messages[:] = request_messages
                    yield TurnCompleted(
                        result=TurnResult(
                            text=reason,
                            usage=completed_usage,
                            last_request_usage=request_usage,
                            model_calls=iteration + routing_model_calls,
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
            if protocol_failure:
                tool_protocol_failures += 1
            elif any(result.success for _, result in tool_results):
                tool_protocol_failures = 0
            if mutation_attempted:
                change_exploration_calls = 0
            elif change_required and tool_results:
                change_exploration_calls += 1

            dependency_recovery_succeeded = bool(
                dependency_recovery_checkpoint
                and any(
                    call.name == 'run_command' and result.success
                    for call, result in tool_results
                )
            )
            if dependency_recovery_succeeded:
                dependency_recovery_checkpoint = False
                dependency_verification_checkpoint = True
                verification_fix_checkpoint = False
                verification_action_checkpoint = False
                calls_without_progress = 0
                force_synthesis = False
                request_messages.append(
                    build_dependency_recovery_completed_feedback(
                        self.task_manager.system_suffix()
                    )
                )
                continue

            dependency_verification_succeeded = bool(
                dependency_verification_checkpoint
                and any(
                    call.name == 'verify' and result.success
                    for call, result in tool_results
                )
            )
            if dependency_verification_succeeded:
                dependency_verification_checkpoint = False
                calls_without_progress = 0
                force_synthesis = False
                request_messages.append(
                    build_dependency_verification_completed_feedback(
                        self.task_manager.system_suffix()
                    )
                )
                continue

            verification_recheck_succeeded = bool(
                verification_recheck_checkpoint
                and any(
                    call.name == 'verify' and result.success
                    for call, result in tool_results
                )
            )
            if verification_recheck_succeeded:
                verification_recheck_checkpoint = bool(unresolved_verifications)
                calls_without_progress = 0
                force_synthesis = False
                unresolved_summary = '; '.join(
                    f'{kind}: {evidence.command} '
                    f'(cwd={evidence.cwd or "."})'
                    for kind, evidence in unresolved_verifications.items()
                )
                request_messages.append(
                    {
                        'role': 'user',
                        'content': (
                            f'{self.task_manager.system_suffix()}\n\n'
                            + (
                                'The current verification command passes, but '
                                'earlier verification failures remain unresolved '
                                'on this revision. Run each exact command now: '
                                f'{unresolved_summary}. A weaker or different '
                                'command cannot replace this evidence.'
                                if unresolved_summary
                                else
                                'The corrected revision now passes its verification. '
                                'Continue with the next concrete requirement, or use '
                                'finish_task if the complete original goal is satisfied.'
                            )
                        ),
                    }
                )
                continue

            verification_retry_blocked = (
                not any(
                    call.name in EDIT_RECOVERY_READ_TOOLS
                    and result.success
                    for call, result in tool_results
                )
                and any(
                    not result.success
                    and result.error is not None
                    and result.error.code == 'verification_requires_correction'
                    and result.metadata.get('verification_retry_blocked') is True
                    for _, result in tool_results
                )
            )
            if verification_retry_blocked:
                verification_fix_checkpoint = False
                verification_action_checkpoint = False
                mutation_action_checkpoint = True
                calls_without_progress = 0
                force_synthesis = False
                request_messages.append(
                    build_redirected_read_action_feedback(
                        self.task_manager.system_suffix()
                    )
                )
                continue

            verification_read_redirected = any(
                result.success
                and result.metadata.get('verification_read_closed') is True
                for _, result in tool_results
            )
            if verification_read_redirected:
                verification_action_checkpoint = False
                mutation_action_checkpoint = True
                calls_without_progress = 0
                force_synthesis = False
                request_messages.append(
                    build_redirected_read_action_feedback(
                        self.task_manager.system_suffix()
                    )
                )
                continue

            requires_verification_fix = bool(
                failed_verification is not None
                and failed_verification.error is not None
                and failed_verification.error.code
                in {'verification_failed', 'verification_timeout'}
            )
            if requires_verification_fix:
                dependency_verification_checkpoint = False
                verification_recheck_checkpoint = False
                missing_dependency = verification_missing_dependency(
                    failed_verification
                )
                dependency_recovery_checkpoint = missing_dependency
                verification_fix_checkpoint = not missing_dependency
                calls_without_progress = 0
                force_synthesis = False
                request_messages.append(
                    (
                        build_dependency_recovery_feedback(
                            self.task_manager.system_suffix(),
                            failed_verification,
                        )
                        if missing_dependency
                        else build_verification_fix_feedback(
                            self.task_manager.system_suffix(),
                            failed_verification,
                        )
                    )
                )
                continue

            redirected_read_succeeded = bool(
                (
                    stale_task_step_checkpoint
                    or current_step_action_checkpoint
                    or verification_fix_checkpoint
                    or oversized_write_checkpoint
                )
                and any(
                    result.success
                    and self.registry is not None
                    and self.registry.effect(call.name) == 'read_only'
                    for call, result in tool_results
                )
            )
            if redirected_read_succeeded:
                verification_read_completed = verification_fix_checkpoint
                oversized_read_completed = oversized_write_checkpoint
                stale_task_step_checkpoint = False
                current_step_action_checkpoint = False
                verification_fix_checkpoint = False
                if oversized_read_completed:
                    oversized_write_checkpoint = False
                    chunk_fallback_required = True
                verification_action_checkpoint = verification_read_completed
                mutation_action_checkpoint = not verification_read_completed
                calls_without_progress = 0
                force_synthesis = False
                request_messages.append(
                    build_redirected_read_action_feedback(
                        self.task_manager.system_suffix()
                    )
                )
                continue

            task_state_read = bool(
                turn_decision is not None
                and turn_decision.intent
                in {'task_query', 'conversation', 'ambiguous'}
                and any(
                    call.name == 'task_get' and result.success
                    for call, result in tool_results
                )
            )
            if task_state_read:
                task_state_synthesis = True
                calls_without_progress = 0
                force_synthesis = True
                request_messages.append(
                    build_task_state_synthesis_feedback(
                        self.task_manager.system_suffix()
                    )
                )
                continue

            current_step_action_result = next(
                (
                    result
                    for call, result in tool_results
                    if call.name == 'task_update'
                    and result.success
                    and result.metadata.get(
                        'current_step_action_required'
                    ) is True
                ),
                None,
            )
            if current_step_action_result is not None:
                current_step_action_checkpoint = True
                planned_progress_checkpoint = False
                calls_without_progress = 0
                force_synthesis = False
                request_messages.append(
                    build_current_step_action_feedback(
                        self.task_manager.system_suffix(),
                        str(
                            current_step_action_result.metadata.get(
                                'current_step_id',
                                '',
                            )
                        ),
                    )
                )
                continue

            stale_step_result = next(
                (
                    result
                    for call, result in tool_results
                    if call.name == 'task_update'
                    and result.success
                    and result.metadata.get('stale_step_redirect') is True
                ),
                None,
            )
            if stale_step_result is not None:
                stale_task_step_checkpoint = True
                planned_progress_checkpoint = False
                calls_without_progress = 0
                force_synthesis = False
                request_messages.append(
                    build_stale_task_step_feedback(
                        self.task_manager.system_suffix(),
                        str(stale_step_result.metadata.get('step_id', '')),
                        str(
                            stale_step_result.metadata.get(
                                'current_step_id',
                                '',
                            )
                        ),
                    )
                )
                continue

            active_task = self.task_manager.active
            planned_work_remaining = bool(
                active_task is not None
                and active_task.planned
                and any(
                    step.status != 'completed'
                    for step in active_task.steps
                )
            )
            successful_plan_created = any(
                call.name == 'task_plan'
                and result.success
                and result.metadata.get('status') != 'already_completed'
                for call, result in tool_results
            )
            if successful_plan_created:
                # Planning consumes the exploration that produced it. The next
                # request must execute that plan, not inherit a pre-plan
                # exploration checkpoint and reopen task_plan.
                change_exploration_calls = 0
            successful_plan_transition = any(
                call.name == 'task_update'
                and result.success
                and result.metadata.get('status') != 'already_completed'
                for call, result in tool_results
            )
            if successful_plan_transition:
                idempotent_resolution_checkpoint = False
                stale_task_step_checkpoint = False
                current_step_action_checkpoint = False
            successful_current_verification = bool(
                any(
                    call.name == 'verify' and result.success
                    for call, result in tool_results
                )
                and self.workspace_tracker is not None
                and latest_verification is not None
                and latest_verification.success
                and latest_verification.workspace_revision
                == self.workspace_tracker.revision
            )
            if successful_current_verification:
                idempotent_resolution_checkpoint = False
            needs_planned_progress_checkpoint = bool(
                planned_work_remaining
                and (
                    successful_plan_created
                    or successful_plan_transition
                    or workspace_progressed
                    or successful_current_verification
                )
            )
            if (
                change_exploration_calls >= self.change_exploration_limit
                and change_required
                and not mutation_attempted
            ):
                if (
                    active_task is not None
                    and not active_task.planned
                    and stagnation_plan_recoveries < 1
                ):
                    stagnation_plan_recoveries += 1
                    planning_checkpoint_recovery = True
                    action_checkpoint_recovery = False
                    request_messages.append(
                        build_planning_checkpoint_feedback(
                            self.task_manager.system_suffix(),
                            self.working_state.system_suffix(),
                        )
                    )
                    continue
                if stagnation_action_recoveries < 1:
                    stagnation_action_recoveries += 1
                    action_checkpoint_recovery = True
                    request_messages.append(
                        build_action_checkpoint_feedback(
                            self.task_manager.system_suffix(),
                            self.working_state.system_suffix(),
                        )
                    )
                    continue

            completion_ready = (
                not protocol_failure
                and await self._can_finalize_after_stagnation(
                    mutation_attempted=mutation_attempted,
                    verification=latest_verification,
                    mutation_failures=mutation_failures,
                    verification_required=verification_required,
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
                    force_synthesis = False
                if not new_ready_revision:
                    completion_decision_calls += 1
                completion_ready_context = render_completion_ready_context(
                    self.workspace_tracker.changed_paths,
                    latest_verification,
                    completion_decision_calls,
                    self.completion_decision_limit,
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
            if (
                workspace_progressed
                or task_progressed
                or needs_planned_progress_checkpoint
                or (
                    change_required
                    and not mutation_attempted
                    and evidence_progressed
                )
            ):
                calls_without_progress = 0
                planning_checkpoint_recovery = False
                planned_progress_checkpoint = (
                    needs_planned_progress_checkpoint
                )
                action_checkpoint_recovery = False
                force_synthesis = False
                if needs_planned_progress_checkpoint:
                    request_messages.append(
                        build_planned_progress_feedback(
                            self.task_manager.system_suffix()
                        )
                    )
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
                            model_calls=iteration + routing_model_calls,
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
                # A failed write gets one evidence-gathering turn followed by
                # an edit-only checkpoint. Evidence collected before the failed
                # write is equally valid; do not force a cached reread first.
                recovery_target_evidence_available = (
                    not mutation_requires_refreshed_evidence
                    and bool(recovery_file_targets)
                    and all(
                        target in self.working_state.evidence_paths
                        for target in recovery_file_targets
                    )
                )
                if (
                    recovery_read_succeeded
                    or recovery_target_evidence_available
                ):
                    calls_without_progress = 0
                    mutation_read_checkpoint = False
                    mutation_action_checkpoint = True
                    request_messages.append(
                        build_mutation_action_feedback(
                            self.task_manager.system_suffix(),
                            mutation_failure_targets,
                        )
                    )
                elif pending_write_results:
                    # Actual failed edits are bounded by the dedicated
                    # mutation attempt counters above. Idempotent directory
                    # no-ops are not edit attempts and must not reset stagnation.
                    calls_without_progress = 0
                else:
                    calls_without_progress += 1
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
                active_task = self.task_manager.active
                if (
                    active_task is not None
                    and active_task.planned
                    and active_task.current_step is not None
                    and stagnation_action_recoveries < 1
                ):
                    stagnation_action_recoveries += 1
                    planned_progress_checkpoint = True
                    action_checkpoint_recovery = False
                    force_synthesis = True
                    calls_without_progress = self.stagnation_limit - 1
                    request_messages.append(
                        build_planned_progress_feedback(
                            self.task_manager.system_suffix(),
                        )
                    )
                    continue
                if (
                    pending_required_change
                    and not mutation_attempted
                    and active_task is not None
                    and not active_task.planned
                    and stagnation_plan_recoveries < 1
                ):
                    stagnation_plan_recoveries += 1
                    planning_checkpoint_recovery = True
                    action_checkpoint_recovery = False
                    force_synthesis = True
                    calls_without_progress = self.stagnation_limit - 1
                    request_messages.append(
                        build_planning_checkpoint_feedback(
                            self.task_manager.system_suffix(),
                            self.working_state.system_suffix(),
                        )
                    )
                    continue
                if (
                    pending_required_change
                    and not mutation_attempted
                    and stagnation_action_recoveries < 1
                ):
                    stagnation_action_recoveries += 1
                    action_checkpoint_recovery = True
                    force_synthesis = True
                    # Preserve one final decision turn. Novel evidence or a
                    # workspace mutation resets this counter normally.
                    calls_without_progress = self.stagnation_limit - 1
                    request_messages.append(
                        build_action_checkpoint_feedback(
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
                        model_calls=iteration + routing_model_calls,
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
                    model_calls=self.max_iterations + routing_model_calls,
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
        git_available = (
            self.workspace_tracker is None
            or getattr(self.workspace_tracker, 'git_available', True)
        )
        verification_hint = (
            'a native build, test, lint, type, syntax, or git diff --check '
            'command'
            if git_available
            else 'a build, test, lint, type, syntax, or task-specific check '
            '(Git is unavailable in this environment)'
        )
        if os.name == 'nt':
            parts.append(
                '[Runtime Environment]\n'
                '- platform: Windows\n'
                '- process shell for command strings: cmd.exe\n'
                '- repository inspection: use read_file, list_directory, grep, '
                'or find_files; do not use ls, POSIX find, cat, or heredocs\n'
                f'- verification: use {verification_hint}'
            )
        else:
            parts.append(
                '[Runtime Environment]\n'
                '- platform: POSIX\n'
                '- process shell: platform default shell\n'
                '- repository inspection: prefer dedicated repository tools\n'
                f'- verification: use {verification_hint}'
            )
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

    async def _finish_rejection_reasons(
        self,
        result: ToolResult,
        *,
        mutation_attempted: bool,
        change_required: bool,
        verification: VerificationEvidence | None,
        verification_required: bool,
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
        task_state_synthesis: bool = False,
        completion_ready_context: str = '',
        change_required: bool = False,
        mutation_attempted: bool = False,
        verification_required: bool = False,
        verification_recovery: bool = False,
        resumed_existing_change: bool = False,
    ) -> str:
        prompt = self._system_prompt_with_task(
            include_tool_availability=not (
                finalization_recovery or task_state_synthesis
            ),
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
                resumed_existing_change=resumed_existing_change,
            )
        if verification_required:
            prompt += (
                '\n\n[ForgeCode Verification Contract]\n'
                'The semantic router determined that executed verification is '
                'part of this task. Choose the appropriate test, build, lint, '
                'type-check, or other command from the user request and repository '
                'evidence. The latest verify result must succeed on the final '
                'workspace revision.'
            )
        if mutation_recovery_context:
            prompt += '\n\n' + mutation_recovery_context
        if completion_ready_context:
            prompt += '\n\n' + completion_ready_context
        if task_state_synthesis:
            prompt += (
                '\n\n[ForgeCode Task-State Synthesis]\n'
                'The current task state was returned successfully. Tools are '
                'closed for this one response because another task_get cannot '
                'add evidence. Answer the user directly from the injected task '
                'context and the latest tool result. State what is current, what '
                'failed or remains, and what the next concrete action would be. '
                'Do not claim that tools are unavailable for future turns, and '
                'do not propose another lookup.'
            )
        elif verification_recovery:
            prompt += (
                '\n\n[ForgeCode Verification Recovery]\n'
                'A real task-local Diff exists, but required verification has '
                'not succeeded on the current revision. Normal tools remain '
                'available: fix a reported failure when necessary, otherwise run '
                'the appropriate verify command before completing.'
            )
        elif finalization_recovery:
            prompt += (
                '\n\n[ForgeCode Finalization Recovery]\n'
                'The current workspace revision satisfies the objective '
                'completion checks. This is a dedicated final synthesis '
                'request, so no tools are included. Return one concise final '
                'answer in the user\'s language based only on the collected '
                'evidence. State what changed and the exact verification '
                'performed. Be honest about anything not verified. Do not print '
                'tool arguments or attempt another finish_task declaration.'
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

    def _finish_task_tools(self) -> list[dict[str, Any]] | None:
        '''Expose only the structured terminal declaration when work is ready.'''
        tools = [
            definition
            for definition in self._tool_definitions() or ()
            if str(definition.get('name', '')) == 'finish_task'
        ]
        return tools or None

    def _task_query_tools(self) -> list[dict[str, Any]] | None:
        '''Expose only structured task-state inspection to task queries.'''
        tools = [
            definition
            for definition in self._tool_definitions() or ()
            if str(definition.get('name', '')) == 'task_get'
        ]
        return tools or None

    def _planning_checkpoint_tools(self) -> list[dict[str, Any]] | None:
        '''Expose only task planning or an honest terminal declaration.'''
        names = {'task_plan', 'finish_task'}
        tools = [
            definition
            for definition in self._tool_definitions() or ()
            if str(definition.get('name', '')) in names
        ]
        return tools or None

    def _mutation_read_tools(self) -> list[dict[str, Any]] | None:
        '''Expose only fresh targeted evidence gathering after stale edit context.'''
        if self.registry is None:
            return self._read_only_tools()
        names = EDIT_RECOVERY_READ_TOOLS
        tools = [
            definition
            for definition in self._tool_definitions() or ()
            if str(definition.get('name', '')) in names
        ]
        return tools or None

    def _mutation_action_tools(self) -> list[dict[str, Any]] | None:
        '''Expose only a corrected workspace edit or honest termination.'''
        tools = self._action_checkpoint_tools()
        if tools is None or self.registry is None:
            return tools
        return [
            definition
            for definition in tools
            if (
                (
                    self.registry.effect(str(definition.get('name', '')))
                    == 'workspace_write'
                    and str(definition.get('name', ''))
                    not in {'create_directory', 'remove_directory'}
                )
                or str(definition.get('name', '')) == 'finish_task'
            )
        ]

    def _dependency_verification_tools(
        self,
    ) -> list[dict[str, Any]] | None:
        '''Expose only verification immediately after dependency installation.'''
        names = {'verify'}
        tools = [
            definition
            for definition in self._tool_definitions() or ()
            if str(definition.get('name', '')) in names
        ]
        return tools or None

    def _dependency_recovery_tools(self) -> list[dict[str, Any]] | None:
        '''Expose only the process action needed for a missing declared tool.'''
        names = {'run_command'}
        tools = [
            definition
            for definition in self._tool_definitions() or ()
            if str(definition.get('name', '')) in names
        ]
        return tools or None

    def _verification_fix_tools(self) -> list[dict[str, Any]] | None:
        '''Expose only targeted evidence and edits after verification failure.'''
        names = {
            'read_file',
            'grep',
            'write_file',
            'write_file_chunk',
            'replace_text',
            'apply_patch',
            'remove_file',
            'run_command',
            'verify',
            'finish_task',
        }
        tools = [
            definition
            for definition in self._tool_definitions() or ()
            if str(definition.get('name', '')) in names
        ]
        if self.registry is not None:
            exposed = {
                str(definition.get('name', ''))
                for definition in tools
            }
            for name in ('write_file_chunk', 'replace_text'):
                definition = self.registry.definition(name)
                if definition is not None and name not in exposed:
                    tools.append(definition)
        return tools or None

    def _idempotent_resolution_tools(self) -> list[dict[str, Any]] | None:
        '''Expose orchestration only after an edit is already satisfied.'''
        names = {'task_get', 'task_update', 'verify', 'finish_task'}
        tools = [
            definition
            for definition in self._tool_definitions() or ()
            if str(definition.get('name', '')) in names
        ]
        return tools or None

    def _oversized_write_recovery_tools(
        self,
    ) -> list[dict[str, Any]] | None:
        '''Expose only concrete ways to recover an oversized whole-file write.'''
        names = {
            'read_file',
            'write_file',
            'write_file_chunk',
            'apply_patch',
            'replace_text',
            'finish_task',
        }
        tools = [
            definition
            for definition in self._tool_definitions() or ()
            if str(definition.get('name', '')) in names
        ]
        if (
            self.registry is not None
            and not any(
                str(definition.get('name', '')) == 'write_file_chunk'
                for definition in tools
            )
        ):
            chunk_definition = self.registry.definition('write_file_chunk')
            if chunk_definition is not None:
                tools.append(chunk_definition)
        return tools or None

    def _stale_task_step_tools(self) -> list[dict[str, Any]] | None:
        '''Exclude plan updates after the model selected an already-finished step.'''
        if self.registry is None:
            return self._tool_definitions()
        excluded = {
            'task_plan',
            'task_update',
            'create_directory',
            'remove_directory',
        }
        tools = [
            definition
            for definition in self._tool_definitions() or ()
            if str(definition.get('name', '')) not in excluded
            and (
                self.registry.effect(str(definition.get('name', '')))
                in {'read_only', 'workspace_write'}
                or str(definition.get('name', '')) in {'verify', 'finish_task'}
            )
        ]
        exposed = {
            str(definition.get('name', ''))
            for definition in tools
        }
        for name in ('write_file_chunk', 'replace_text'):
            definition = self.registry.definition(name)
            if definition is not None and name not in exposed:
                tools.append(definition)
        return tools or None

    def _planned_progress_tools(self) -> list[dict[str, Any]] | None:
        '''Expose evidence, execution, and transition tools after planning.'''
        combined = [
            *(self._read_only_tools() or ()),
            *(self._action_checkpoint_tools() or ()),
        ]
        tools_by_name = {
            str(definition.get('name', '')): definition
            for definition in combined
            if str(definition.get('name', '')) != 'task_plan'
        }
        return list(tools_by_name.values()) or None

    def _action_checkpoint_tools(self) -> list[dict[str, Any]] | None:
        '''Expose planning, mutation, verification, and completion actions.'''
        if self.registry is None:
            return self._tool_definitions()
        orchestration = {
            'task_plan',
            'task_update',
            'verify',
            'finish_task',
        }
        tools = [
            definition
            for definition in self._tool_definitions() or ()
            if (
                self.registry.effect(str(definition.get('name', '')))
                == 'workspace_write'
                or str(definition.get('name', '')) in orchestration
            )
        ]
        exposed_names = {
            str(definition.get('name', ''))
            for definition in tools
        }
        for name in ('write_file_chunk', 'replace_text'):
            definition = self.registry.definition(name)
            if definition is not None and name not in exposed_names:
                tools.append(definition)
        return tools or None

    def _read_only_tools(self) -> list[dict[str, Any]] | None:
        '''Expose only tools that cannot mutate the workspace or run processes.'''
        if self.registry is None:
            return None
        task_state_writes = {'finish_task', 'task_plan', 'task_update'}
        return [
            definition
            for definition in self._tool_definitions() or ()
            if (
                self.registry.effect(str(definition.get('name', '')))
                == 'read_only'
                and str(definition.get('name', ''))
                not in task_state_writes
            )
        ]

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
            and str(definition.get('name', '')) != 'finish_task'
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
    ) -> bool:
        '''Enter synthesis only for a mechanically complete current revision.'''
        tracker = self.workspace_tracker
        gate = self.completion_gate
        if (
            tracker is None
            or gate is None
            or not tracker.changed_paths
        ):
            return False
        decision = await gate.evaluate(
            tracker,
            verification,
            mutation_attempted=mutation_attempted,
            require_verification=verification_required,
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
            scope_hints=(
                self.task_manager.active.scope_hints
                if self.task_manager.active is not None
                else ()
            ),
            task_goal=(
                self.task_manager.active.goal
                if self.task_manager.active is not None
                else ''
            ),
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
                (
                    self.registry.audit_arguments(call.name, call.arguments)
                    if self.registry is not None
                    else call.arguments
                ),
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

    def skill_list(self) -> str:
        if self.skill_manager is None:
            return 'ForgeCode skill discovery is unavailable.'
        self.skill_manager.refresh()
        return self.skill_manager.describe()

    def skill_show(self, name: str) -> str:
        if self.skill_manager is None:
            raise ValueError('ForgeCode skill discovery is unavailable.')
        self.skill_manager.refresh()
        return self.skill_manager.show(name)

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
        if not state.messages and state.active_task is None:
            raise ValueError(
                f'Session {state.info.session_id} has no resumable '
                'conversation history.'
            )
        if self.session_journal is not None:
            self.session_journal.record_stopped()
        self.messages[:] = list(state.messages)
        self.task_manager.restore(state.active_task)
        self._last_task_context = self.task_manager.system_suffix()
        if state.info.model and hasattr(self.client, 'model'):
            self.client.model = state.info.model
            router_client = getattr(self.intent_router, 'client', None)
            if router_client is not None and hasattr(router_client, 'model'):
                router_client.model = state.info.model
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
    *,
    workspace_revision: int | None = None,
) -> VerificationEvidence | None:
    '''Build evidence bound to the workspace state after verification ends.'''
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
            workspace_revision=(
                workspace_revision
                if workspace_revision is not None
                else int(metadata['workspace_revision'])
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def tool_call_signature(tool_call: ToolCall, revision: int) -> str:
    '''Identify a semantic tool request within one workspace revision.'''
    normalized = dict(tool_call.arguments)
    if tool_call.name == 'git_diff':
        # Omitted Pydantic defaults and explicitly supplied defaults are the
        # same request and must share one working-evidence cache entry.
        normalized.setdefault('staged', False)
        normalized.setdefault('offset', 0)
        normalized.setdefault('expected_sha256', None)
    arguments = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    )
    return f'{revision}:{tool_call.name}:{arguments}'


def tool_call_identity(tool_call: ToolCall) -> str:
    '''Identify an exact call independent of workspace revision.'''
    arguments = json.dumps(
        tool_call.arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    )
    return f'{tool_call.name}:{arguments}'


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


def build_required_change_text_recovery_feedback() -> dict[str, Any]:
    '''Give one bounded retry when a requested edit received only prose.'''
    return {
        'role': 'user',
        'content': (
            'ForgeCode rejected the progress report because the user requested '
            'an actual workspace implementation and this turn still has no '
            'Diff. Do not ask for confirmation or return another plan. Use the '
            'available workspace-write tools now, then verify the result.'
        ),
    }


def render_change_contract_context(
    changed_paths: tuple[str, ...],
    *,
    mutation_attempted: bool,
    resumed_existing_change: bool = False,
) -> str:
    paths = summarize_changed_paths(changed_paths)
    attempted = 'yes' if mutation_attempted else 'no'
    if resumed_existing_change:
        return (
            '[ForgeCode Resumed Change Evidence]\n'
            'This router decision continues the persisted task. The paths below '
            'were changed by earlier turns of the same task and count as its '
            'workspace evidence.\n'
            f'- persisted task changed paths: {paths}\n'
            'Use the original goal, current task state, and available tools to '
            'decide the next action. Do not create an unrelated edit merely to '
            'produce a new per-turn Diff.'
        )
    return (
        '[ForgeCode Turn Change Contract]\n'
        'The user requested an implemented workspace change; an explanation '
        'or inspection alone cannot complete this turn.\n'
        f'- task-local changed paths: {paths}\n'
        f'- workspace write attempted: {attempted}\n'
        'Only a file revision after the turn baseline satisfies this '
        'contract. Git HEAD changes or untracked files that already existed '
        'when the turn began are background context, not work completed in '
        'this turn. When the original goal is satisfied, call finish_task alone '
        'with an honest structured outcome; do not end a change task with prose '
        'alone.'
    )


def build_task_state_synthesis_feedback(
    task_context: str,
) -> dict[str, Any]:
    '''Close tools after one successful current-task lookup.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            '[ForgeCode Task-State Synthesis Checkpoint]\n'
            'task_get succeeded and returned the complete current task state. '
            'Do not call task_get again or invent a task_id argument. Tools are '
            'closed only for this response. Answer the user now from the '
            'available task state, without saying that future work is disabled.'
        ),
    }


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
            'already collected and choose a materially different next action. '
            'Avoid broad rescans and do not repeat an unchanged failing call.'
        ),
    }


def build_planning_checkpoint_feedback(
    task_context: str,
    working_context: str,
) -> dict[str, Any]:
    '''Require an unplanned complex task to choose a concrete strategy.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n{working_context}\n\n'
            '[ForgeCode Planning Checkpoint]\n'
            'The requested workspace change remains broad after repository '
            'exploration. Exploration and editing tools are temporarily closed. '
            'Use task_plan to choose concrete implementation and verification '
            'steps from the evidence already gathered. If no honest plan can '
            'satisfy the user request, call finish_task with a blocked outcome. '
            'Do not create a probe, marker, placeholder, or no-op edit.'
        ),
    }


def build_idempotent_resolution_feedback(
    task_context: str,
    targets: tuple[str, ...],
) -> dict[str, Any]:
    '''Move an already-satisfied edit to plan progress or verification.'''
    rendered = ', '.join(targets) or 'the current edit target'
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            '[ForgeCode Already-Satisfied Edit Checkpoint]\n'
            f'The requested edit for {rendered} is already present on the '
            'current workspace revision. This resolves the pending edit '
            'failure. Editing tools are closed for this request: do not '
            'reapply the same patch, rewrite the file, or start a chunked '
            'replacement. Record completed plan evidence with task_update, '
            'run the required verify command when the plan is ready, inspect '
            'task state if necessary, or finish honestly.'
        ),
    }


def build_oversized_write_recovery_feedback(
    task_context: str,
) -> dict[str, Any]:
    '''Require an actual file write after write_file exceeds its schema limit.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            '[ForgeCode Oversized Write Recovery]\n'
            'The previous write_file request exceeded the 30,000-character '
            'argument limit and made no workspace change. Do not mark a plan '
            'step complete or update task state. Create the actual target now: '
            'use write_file_chunk starting at offset=0 with truncate=true, or '
            'write a smaller real skeleton and extend it with focused edits. '
            'Reading the target is allowed when needed, but preparation and '
            'future-intention notes are not completion evidence.'
        ),
    }


def build_redirected_read_action_feedback(
    task_context: str,
) -> dict[str, Any]:
    '''Turn one redirected evidence batch into a mandatory concrete edit.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            '[ForgeCode Redirected Read Complete]\n'
            'The one targeted evidence batch allowed by the current-step '
            'redirect has completed. Reading, planning, task updates, directory '
            'creation, and verification are closed for this request. Make one '
            'concrete task-relevant file edit from that evidence now, or call '
            'finish_task with an honest blocked result. Do not inspect another '
            'path and do not create a temporary or probe artifact.'
        ),
    }


def build_current_step_action_feedback(
    task_context: str,
    current_step_id: str,
) -> dict[str, Any]:
    '''Force concrete work after a repeated in-progress status update.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            '[ForgeCode Current-Step Action Checkpoint]\n'
            f'{current_step_id or "The current step"} is already in progress. '
            'task_plan and task_update are closed for this request because '
            'another preparation note cannot advance the task. Use existing '
            'evidence to read one missing target if necessary, make a concrete '
            'task-relevant file edit, run a relevant verification, or finish '
            'honestly if blocked. Do not create temporary, placeholder, probe, '
            'marker, or empty-directory progress.'
        ),
    }


def build_stale_task_step_feedback(
    task_context: str,
    stale_step_id: str,
    current_step_id: str,
) -> dict[str, Any]:
    '''Redirect repeated updates of a completed step to concrete current work.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            '[ForgeCode Current-Step Redirect]\n'
            f'{stale_step_id or "The requested step"} is already completed; '
            f'the current step is {current_step_id or "shown above"}. '
            'Task planning and task updates are closed for this request. '
            'Do not update the completed step again. Read only the files needed '
            'for the current step, make its concrete workspace edit, verify the '
            'result when appropriate, or finish honestly if blocked.'
        ),
    }


def build_planned_progress_feedback(
    task_context: str,
) -> dict[str, Any]:
    '''Move a planned task forward after planning or current verification.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            '[ForgeCode Planned Progress Checkpoint]\n'
            'A plan was just created or the current revision was just verified, '
            'but planned work remains. Do not reopen broad repository exploration '
            'or repeat verification. If the current step needs exact content not '
            'yet seen, perform one targeted read; otherwise use existing evidence '
            'to update completed plan steps, make the next task-relevant workspace '
            'edit, or finish honestly if the remaining '
            'plan is no longer required. Probe and placeholder edits are invalid.'
        ),
    }


def build_action_checkpoint_feedback(
    task_context: str,
    working_context: str,
) -> dict[str, Any]:
    '''Give a change task one model turn to consume gathered evidence.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n{working_context}\n\n'
            '[ForgeCode Action Checkpoint]\n'
            'Exploration has reached its useful limit. Consume the evidence '
            'returned by the latest tool batch before the runtime can stop. '
            'Exploration tools are temporarily closed; planning, workspace '
            'edits, verification, and finish_task remain available. Choose the '
            'next semantic action yourself. If the request is broad and the '
            'implementation path is not concrete, create or update the task '
            'plan before editing. Otherwise make a focused task-relevant edit, '
            'run a necessary verification, or call finish_task with an honest '
            'blocked explanation. For a deliberate whole-file replacement, use '
            'write_file_chunk with a non-empty real prefix, offset=0, '
            'truncate=true, and final=false, then append a non-empty final '
            'chunk at next_offset; never send an empty starter chunk. Do not '
            'delete and recreate the file merely to bypass focused edit '
            'constraints. Do not '
            'create probe, marker, sentinel, placeholder, or no-op files.'
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


def build_verification_failure_feedback(
    result: ToolResult,
) -> dict[str, Any]:
    '''Keep failed verification focused on the smallest shared root cause.'''
    return {
        'role': 'user',
        'content': (
            'ForgeCode verification checkpoint: the current Diff exists, '
            'but verification failed. Treat the immediately preceding command '
            'output as primary evidence. Choose the smallest useful inspection, '
            'edit, or corrected verification command; do not repeat the same '
            'failing command unchanged and do not weaken existing tests merely '
            f'to obtain a passing exit code. Verification status: {result.summary}'
        ),
    }


def build_dependency_recovery_feedback(
    task_context: str,
    result: ToolResult,
) -> dict[str, Any]:
    '''Force package installation when verification cannot find a declared tool.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            '[ForgeCode Dependency Recovery]\n'
            'Verification cannot find a declared project compiler or command. '
            f'The failure was: {result.summary} '
            'Repository reads, edits, planning, task updates, and repeated '
            'verification are closed for this request. Use run_command now to '
            'run the project package-manager install command in the exact cwd '
            'used by the failed verification (for example npm install). Do not '
            'change scripts or compiler settings to hide the missing dependency.'
        ),
    }


def build_dependency_recovery_completed_feedback(
    task_context: str,
) -> dict[str, Any]:
    '''Require verification immediately after dependency installation.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            '[ForgeCode Dependency Recovery Complete]\n'
            'The dependency installation command succeeded. Re-run the exact '
            'failed typecheck or build verification now. Do not edit configuration '
            'again unless the new verification reports a concrete source error.'
        ),
    }


def build_dependency_verification_completed_feedback(
    task_context: str,
) -> dict[str, Any]:
    '''Return to implementation after the post-install verification passes.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            '[ForgeCode Post-Install Verification Passed]\n'
            'The previously missing project command is installed and the repeated '
            'verification passed. Continue the current plan from the next concrete '
            'implementation step; do not reinstall or rewrite the toolchain.'
        ),
    }


def build_verification_fix_feedback(
    task_context: str,
    result: ToolResult,
) -> dict[str, Any]:
    '''Restrict the next request to diagnosing or fixing the failed command.'''
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            '[ForgeCode Verification Fix Phase]\n'
            'ForgeCode verification checkpoint: '
            'Choose the smallest useful inspection or correction; do not '
            'repeat the same failing command unchanged and do not weaken existing '
            'tests merely to obtain a passing exit code. '
            f'The command failed: {result.summary} '
            'The exact stdout/stderr in the preceding tool result is primary '
            'evidence. Planning, task updates, directory creation, unrelated '
            'inspection, version queries, and immediate re-verification are '
            'closed for this request. Read only a file named by the failure if '
            'needed, or make the smallest concrete source/config correction now. '
            'If stderr says a declared compiler, package, or command is missing '
            '(for example tsc is not recognized), use run_command to execute the '
            'project package-manager install command in the exact task cwd before '
            're-running verification. Do not create check, keep, readme, '
            'temporary, probe, or marker files.'
        ),
    }


def render_completion_ready_context(
    changed_paths: tuple[str, ...],
    verification: VerificationEvidence | None,
    decision_calls: int,
    decision_limit: int,
) -> str:
    '''Expose only objective completion evidence to the model.'''
    changed = summarize_changed_paths(changed_paths)
    verification_status = (
        f'{verification.command} @ revision {verification.workspace_revision}'
        if verification is not None
        else 'not required / not run'
    )
    remaining = max(decision_limit - decision_calls, 0)
    return (
        '[ForgeCode Completion Evidence]\n'
        f'changed paths: {changed}\n'
        f'current verification: {verification_status}\n'
        f'decision calls remaining: {remaining}\n'
        'The harness checks only objective execution evidence. Decide from the '
        'original request and collected repository evidence whether the goal is '
        'satisfied. If it is, call finish_task alone. If it is not, perform the '
        'smallest concrete next action. Do not repeat already-covered reads or '
        'unchanged failing tool calls.'
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
    changed = summarize_changed_paths(changed_paths)
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


def summarize_changed_paths(
    changed_paths: tuple[str, ...],
    *,
    maximum_paths: int = 30,
    maximum_characters: int = 4_000,
) -> str:
    '''Render bounded workspace evidence for model-visible prompts.'''
    if not changed_paths:
        return 'none'
    visible: list[str] = []
    used = 0
    for path in changed_paths[:maximum_paths]:
        separator = 2 if visible else 0
        if used + separator + len(path) > maximum_characters:
            break
        visible.append(path)
        used += separator + len(path)
    omitted = len(changed_paths) - len(visible)
    rendered = ', '.join(visible) if visible else '(paths omitted)'
    if omitted:
        rendered += f' ... (+{omitted} more; {len(changed_paths)} total)'
    return rendered


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
        direct_path = direct_path.strip().replace('\\', '/')
        paths.append(direct_path)
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


def checkpoint_mutation_paths(
    root: Path,
    tool_call: ToolCall,
) -> tuple[str, ...]:
    '''Expand recursive directory deletion into checkpoint-restorable entries.'''
    targets = mutation_target_paths(tool_call, maximum=None)
    if tool_call.name != 'remove_directory' or not targets:
        return targets
    raw_path = targets[0]
    candidate = (root.resolve() / raw_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return targets
    if not candidate.is_dir():
        return targets
    descendants = sorted(
        (
            path.relative_to(root.resolve()).as_posix()
            for path in candidate.rglob('*')
            if not path.is_symlink()
        ),
        key=lambda path: (path.count('/'), path),
    )
    return tuple(dict.fromkeys((raw_path, *descendants)))


def protected_task_input_delete(
    tool_call: ToolCall,
    scope_hints: tuple[str, ...],
) -> bool:
    '''Protect task inputs and explicit scope roots during implementation.'''
    targets = mutation_target_paths(tool_call, maximum=None)
    if any(
        Path(target).name.casefold() in {'task.md', 'agents.md'}
        for target in targets
    ):
        return True
    if tool_call.name != 'remove_directory' or not targets:
        return False
    raw_target = targets[0].strip().replace('\\', '/').strip('/')
    scope_roots = {
        hint.strip().replace('\\', '/').removesuffix('/**').strip('/')
        for hint in scope_hints
        if hint.strip().replace('\\', '/').endswith('/**')
    }
    return bool(raw_target and raw_target in scope_roots)


def verification_obligation(command: str) -> str | None:
    '''Classify verification commands whose evidence must not be weakened.'''
    normalized = command.casefold()
    if re.search(r'\b(?:typecheck|tsc)\b', normalized):
        return 'typecheck'
    if re.search(r'\bbuild\b', normalized):
        return 'build'
    return None


def verification_missing_dependency(result: ToolResult) -> bool:
    '''Detect verification failures caused by an unavailable dependency.'''
    rendered = ' '.join(
        part
        for part in (
            result.summary,
            str(result.content or ''),
            result.error.message if result.error is not None else '',
        )
        if part
    ).casefold()
    if 'is not recognized as an internal or external command' in rendered:
        return True
    if re.search(
        r'(?:command\s+not\s+found|not\s+found|no\s+such\s+file\s+or\s+directory|'
        r'executable\s+file\s+not\s+found)\b',
        rendered,
    ):
        return True
    if re.search(
        r'\b(?:modulenotfounderror|importerror):?\s*(?:no\s+module\s+named|'
        r'cannot\s+import)\b',
        rendered,
    ):
        return True
    if re.search(
        r'\b(?:cannot\s+open\s+shared\s+object\s+file|'
        r'could\s+not\s+find\s+\S+\s*\(missing:|'
        r'no\s+such\s+package)',
        rendered,
    ):
        return True
    return False


def oversized_write_file_result(
    tool_call: ToolCall,
    result: ToolResult,
) -> bool:
    '''Identify a write_file request rejected only because its body is too large.'''
    return bool(
        tool_call.name == 'write_file'
        and result.error is not None
        and (
            'maximum is' in result.error.message
            or 'maximum is' in result.summary
        )
    )


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
        'Normal repository tools remain available. Use the structured error '
        'and current evidence to choose the next action. Covered reads are '
        'cached, and an identical failed call will be rejected, so retry only '
        'with materially corrected arguments or a better-suited tool.'
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


def build_mutation_action_feedback(
    task_context: str,
    targets: tuple[str, ...],
) -> dict[str, Any]:
    '''Turn freshly gathered recovery evidence into one corrected edit.'''
    rendered = ', '.join(targets) or 'the failed edit target'
    return {
        'role': 'user',
        'content': (
            f'{task_context}\n\n'
            '[ForgeCode Edit Action Checkpoint]\n'
            f'Targeted evidence is available for {rendered}. '
            'Repository exploration and directory-only operations are closed '
            'for this request. Make one '
            'materially corrected workspace edit using that evidence, or '
            'finish with an honest blocked outcome. Do not reread cached '
            'ranges, verify an unchanged revision, or create a probe.'
        ),
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
            'tool_not_available_in_phase',
            'plan_already_created_this_turn',
            'delete_only_batch_requires_replacement',
            'protected_task_input_delete',
            'not_executed_after_workspace_write_failure',
            'finish_must_be_alone',
            'unsupported_shell_syntax',
            'file_already_exists',
            'placeholder_write_denied',
            'whole_file_correction_too_small',
            'directory_intent_mismatch',
            'invalid_json_content',
            'parent_is_file',
            'invalid_pattern',
            'patch_contains_read_line_numbers',
            'patch_empty_hunk',
            'patch_missing_hunk',
            'patch_no_changes',
            'verification_read_budget_exhausted',
            'verification_requires_correction',
            'text_no_change',
            'git_diff_path_is_directory',
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


def agent_created_document_cleanup(tool_call: ToolCall) -> bool:
    '''Allow cleanup of disposable docs without reopening source deletion.'''
    targets = mutation_target_paths(tool_call, maximum=None)
    if not targets:
        return False
    disposable_names = {'readme', 'readme.md', 'readme.txt'}
    return all(
        Path(target).name.casefold() in disposable_names
        or Path(target).suffix.casefold() in {'.md', '.txt'}
        for target in targets
    )


def obvious_probe_write(tool_call: ToolCall, prompt: str) -> bool:
    '''Reject unmistakable synthetic progress writes and directories.'''
    if tool_call.name not in {
        'write_file',
        'write_file_chunk',
        'create_directory',
    }:
        return False
    del prompt
    raw_path = tool_call.arguments.get('path')
    if isinstance(raw_path, str):
        name = Path(raw_path).name.casefold().lstrip('.')
        path_tokens = {
            token
            for token in re.split(r'[^a-z0-9]+', name)
            if token
        }
        if path_tokens & {'placeholder', 'tmp', 'temp', 'probe', 'noop', 'newdir'}:
            return True
        if name in {'keep', 'gitkeep', 'readme_check.md', 'readme_check.ts'}:
            return True
        if 'check' in path_tokens and (
            'readme' in path_tokens or 'forge' in path_tokens
        ):
            return True
    content = tool_call.arguments.get('content')
    if not isinstance(content, str):
        return False
    normalized = content.strip().casefold()
    normalized_words = re.sub(r'[^a-z0-9]+', ' ', normalized).strip()
    probes = {
        'noop',
        'tmp',
        'temp',
        'temporary',
        'test',
        'placeholder',
        'placeholder2',
        'dummy',
        'delete',
    }
    return normalized in probes or normalized_words in probes


def patch_has_constructive_operation(tool_call: ToolCall) -> bool:
    '''Return whether a destructive patch also creates or updates content.'''
    if tool_call.name != 'apply_patch':
        return False
    patch = tool_call.arguments.get('patch')
    if not isinstance(patch, str):
        return False
    return any(
        marker in patch
        for marker in ('*** Add File:', '*** Update File:')
    )


def serialized_tool_arguments(
    text: str,
    tool_definitions: list[dict[str, Any]] | None,
) -> str | None:
    '''Identify tool arguments emitted as prose instead of a tool call.'''
    candidate = text.lstrip('\ufeff \t\r\n')
    if not candidate.startswith('{'):
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not payload:
        return None
    keys = set(payload)
    for definition in tool_definitions or ():
        schema = definition.get('input_schema')
        if not isinstance(schema, dict):
            continue
        properties = schema.get('properties')
        required = schema.get('required', ())
        if (
            isinstance(properties, dict)
            and isinstance(required, list)
            and set(required).issubset(keys)
            and keys.issubset(properties)
        ):
            name = definition.get('name')
            if isinstance(name, str) and name:
                return name
    return None


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
