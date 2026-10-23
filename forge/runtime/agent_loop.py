'''Conversation compatibility entry point and session operations.'''

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing, nullcontext
from functools import cache
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
from forge.messages import UserInput
from forge.hooks import HookEvent, HookManager, HookOutcome
from forge.permissions.policy import PermissionManager
from forge.runtime.router import IntentRouter
from forge.runtime.model_client import (
    AnthropicModelClient,
    ModelClient,
)
from forge.runtime.completion import (
    CompletionGate,
    TaskPolicy,
)
from forge.runtime.state import (
    ConversationEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
    ToolCall,
    VerificationEvidence,
)
from forge.runtime.turn_state import (
    TurnState,
    execution_status_for_result,
)
from forge.runtime.executor import ToolExecutor
from forge.runtime.workspace import WorkspaceTracker
from forge.sessions.checkpoint import CheckpointStore
from forge.skills import SkillManager
from forge.tasks.manager import TaskManager
from forge.tools.base import ToolRegistry, ToolResult
from forge.tools.task import create_task_tools
from forge.tools.context import ReadContextArtifactTool

if TYPE_CHECKING:
    from forge.mcp.manager import MCPClientManager
    from forge.sessions.store import SessionJournal, SessionStore
    from forge.tasks.state import ActiveTask


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
        max_turn_seconds: float | None = None,
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
        task_relation: str | None = None,
        tool_backend: Any | None = None,
        owns_tool_backend: bool = True,
        event_recorder: Any | None = None,
        turn_baseline_handler: Any | None = None,
    ) -> None:
        if tools is not None and registry is not None:
            raise ValueError('Pass tools or registry, not both.')
        if task_relation not in {None, 'new', 'active'}:
            raise ValueError('Explicit task_relation must be new or active.')
        self.task_relation = task_relation
        import warnings
        for name, value, previous_default in (
            ('repeated_tool_limit', repeated_tool_limit, 2),
            ('stagnation_warning', stagnation_warning, 4),
            ('stagnation_limit', stagnation_limit, 8),
            ('change_exploration_limit', change_exploration_limit, 8),
            ('completion_decision_limit', completion_decision_limit, 3),
            ('mutation_recovery_limit', mutation_recovery_limit, 4),
        ):
            if value != previous_default:
                warnings.warn(f'{name} is deprecated and has no effect; use explicit turn budgets.',
                              DeprecationWarning, stacklevel=2)
        self.event_recorder = event_recorder
        self.owns_tool_backend = owns_tool_backend
        self._close_task = None
        self.turn_baseline_handler = turn_baseline_handler
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
        if client is None:
            from forge.runtime.providers import create_model_client
            client = create_model_client()
        self.client = client
        self.system_prompt = (
            system_prompt
            if system_prompt is not None
            else load_system_prompt()
        )
        from forge.sessions.controller import SessionController
        self.session = SessionController(messages=initial_messages or (), journal=session_journal,
            store=session_store, checkpoints=checkpoint_store)
        self._running = False
        self.hook_manager = hook_manager
        self._hooks_started = False
        self._pending_hook_context: list[str] = []
        self._persisted_event_keys: set[tuple[str, str]] = set()
        self._persisted_turn_events: set[str] = set()
        self._kernel_owns_events = False
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
        if (
            registry is not None
            and 'finish_task' in registry.names
            and 'read_context_artifact' not in registry.names
        ):
            registry.register(ReadContextArtifactTool(resolved_context_root, self.context))
        self.turn_state: TurnState | None = None
        # Task evidence outlives an individual TurnRunner. Recovery populates
        # this collection from durable observations, never from model summaries.
        self.task_manager.evidence_provider = lambda: tuple(
            item for item in self.verification_history
            if item.task_id == (self.task_manager.active.id if self.task_manager.active else ''))
        self.tool_executor = (
            ToolExecutor(
                registry,
                self.permission_manager,
                workspace_tracker=self.workspace_tracker,
                hook_runner=self._emit_hook,
                session_journal=session_journal,
                checkpoint_store=checkpoint_store,
                path_resolver=(
                    lambda call: checkpoint_mutation_paths(
                        resolved_context_root,
                        call,
                    )
                ) if not getattr(tool_backend, 'observer', None) else lambda call: mutation_target_paths(call, maximum=None),
                hook_context_sink=self._queue_hook_context,
                backend=tool_backend,
            )
            if registry is not None
            else None
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
        if max_turn_seconds is not None and max_turn_seconds <= 0:
            raise ValueError('max_turn_seconds must be positive')
        self.max_turn_seconds = max_turn_seconds
        self._last_repository_context = self._repository_context('')
        self._last_task_context = ''

    # Compatibility properties retain the released Conversation API while the
    # controller owns session state and the runner owns only current-turn state.
    @property
    def messages(self):
        return self.session.messages

    @messages.setter
    def messages(self, value):
        self.session.commit_messages(value)

    @property
    def session_journal(self):
        return self.session.journal

    @session_journal.setter
    def session_journal(self, value):
        self.session.journal = value

    @property
    def session_store(self):
        return self.session.store

    @session_store.setter
    def session_store(self, value):
        self.session.store = value

    @property
    def checkpoint_store(self):
        return self.session.checkpoints

    @checkpoint_store.setter
    def checkpoint_store(self, value):
        self.session.checkpoints = value

    @property
    def verification_history(self):
        return self.session.verification

    @verification_history.setter
    def verification_history(self, value):
        self.session.verification[:] = value

    @property
    def inbox(self):
        return self.session.inbox

    def _require_idle(self):
        if self._running:
            raise RuntimeError('Cancel the active turn before changing session history')

    def _repository_context(self, query: str) -> str:
        parts = [self.context.repository.system_suffix(query)]
        if self.skill_manager is not None:
            self.skill_manager.refresh()
            parts.append(self.skill_manager.system_suffix(query))
        return '\n\n'.join(part for part in parts if part)

    def _tool_definitions(self) -> list[dict[str, Any]] | None:
        definitions = self.registry.definitions if self.registry is not None else self.tools
        from forge.runtime.capabilities import capabilities_for
        if not capabilities_for(self.client).accepts_images and definitions:
            return [item for item in definitions if item['name'] != 'view_image']
        return definitions

    @property
    def capabilities(self) -> dict:
        from dataclasses import asdict
        from forge.runtime.capabilities import capabilities_for
        policy = self.completion_gate.policy if self.completion_gate else TaskPolicy()
        return {'model': asdict(capabilities_for(self.client)),
                'input': {'text': True, 'image': capabilities_for(self.client).accepts_images,
                          'model_modalities_verified': capabilities_for(self.client).model_input_modalities is not None},
                'delivery_repair': {**policy.delivery_repair_capability,
                                   'supported': self.completion_gate is not None}}

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

    def queue_input(self, prompt: UserInput, *, follow_up=False) -> str:
        from forge.messages import normalize_input
        from forge.runtime.capabilities import capabilities_for
        if self._close_task is not None:
            raise RuntimeError('Conversation is closed')
        prompt = normalize_input(prompt)
        if isinstance(prompt, list) and any(block['type'] == 'image' for block in prompt):
            if not capabilities_for(self.client).accepts_images:
                raise ValueError('Configured model adapter does not support image input')
        return self.inbox.queue(prompt, follow_up=follow_up)

    async def stream(self, prompt: UserInput, *, input_id=None) -> AsyncIterator[ConversationEvent]:
        '''Compatibility entry point delegated to the turn runner.'''
        from forge.messages import normalize_input
        from forge.runtime.capabilities import capabilities_for
        prompt = normalize_input(prompt)
        if self._running or self._close_task is not None:
            raise RuntimeError('Conversation is closed or already running a turn')
        if input_id is not None and not any(item['id'] == input_id and item['content'] == prompt
                                           for item in self.inbox.pending):
            raise ValueError('Queued input is missing or differs from the submitted content')
        if isinstance(prompt, list) and any(block['type'] == 'image' for block in prompt):
            if not capabilities_for(self.client).accepts_images:
                raise ValueError('Configured model adapter does not support image input')
        from forge.runtime.runner import TurnRunner
        from forge.sessions.workspace_lock import workspace_execution
        runner = TurnRunner(self)
        ownership = workspace_execution(self.task_manager.root) if self.registry is not None else nullcontext()
        from forge.observability.events import current
        from forge.observability.recorder import JournalRecorder
        inherited=current()
        recorder=(JournalRecorder(self.session_journal,scope=getattr(self.session_journal,'observation_scope',None),
                  scope_sink=getattr(self.session_journal,'observation_scope_sink',None)) if self.session_journal else inherited)
        with ownership, recorder.turn(nested=self.session_journal is None) if recorder else nullcontext():
            self._running = True
            try:
                if self.turn_baseline_handler is not None:
                    baseline = self.turn_baseline_handler()
                    if baseline is not None:
                        await baseline
                async with aclosing(runner.run(prompt, input_id=input_id)) as stream:
                    async for event in stream:
                        if self.event_recorder is not None:
                            self.event_recorder.record(event)
                        yield event
            finally:
                self._running = False

    def record_model_request(self, kind: str, attributes: dict[str, Any]) -> None:
        if self.session_journal is not None:
            self.session_journal.append(kind, attributes)
        if self.event_recorder is not None:
            self.event_recorder.record_request(kind, attributes)
        from forge.observability.events import current
        recorder=current()
        if recorder:
            recorder.record_request(kind,attributes)

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

    async def compact(self) -> CompactionReport:
        '''Manually summarize committed history for the /compact command.'''
        self._require_idle()
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
        if self.session_journal is not None and self.session_journal.deferred:
            return
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
        self._require_idle()
        self._resume_target(identifier)
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
        self._require_idle()
        await self.session_end(reason='branch')
        notice = self.session_branch(name)
        if self.mcp_manager is not None:
            await self.mcp_manager.reset_session()
        await self.session_start(source='branch')
        return notice

    async def session_clear_with_hooks(self) -> str:
        self._require_idle()
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
        if self._kernel_owns_events and isinstance(event, (ToolExecutionStarted, ToolExecutionCompleted)):
            return  # ToolExecutor owns the durable lifecycle, even without a consumer.
        if isinstance(event, ToolExecutionStarted):
            call = event.tool_call
            key = ('started', call.id)
            if key in self._persisted_event_keys:
                return
            self._persisted_event_keys.add(key)
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
            key = ('completed', call.id)
            if key in self._persisted_event_keys:
                return
            self._persisted_event_keys.add(key)
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
                status=execution_status_for_result(event.result),
                error_code=(
                    event.result.error.code
                    if event.result.error is not None
                    else None
                ),
                workspace_revision=optional_int(
                    event.result.metadata.get('workspace_revision')
                ),
                environment_epoch=optional_int(
                    event.result.metadata.get('environment_epoch')
                ),
            )
        elif isinstance(event, TurnCompleted):
            event_key = event.event_id
            if event_key in self._persisted_turn_events:
                return
            self._persisted_turn_events.add(event_key)
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

    async def runtime_close(self, *, reason: str = 'exit') -> dict | None:
        """Drain the owned resources once; children only close their own clients."""
        import asyncio
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_runtime(reason=reason))
        return await asyncio.shield(self._close_task)

    async def _close_runtime(self, *, reason: str) -> dict | None:
        cleanup = None
        try:
            await self.session_end(reason=reason)
            if self.mcp_manager is not None:
                await self.mcp_manager.close()
        finally:
            try:
                seen = set()
                for client in (self.client, getattr(self.intent_router, 'client', None)):
                    if client is not None and id(client) not in seen:
                        seen.add(id(client))
                        close = getattr(client, 'aclose', None)
                        if close is not None:
                            await close()
            finally:
                if self.owns_tool_backend and self.tool_executor is not None and self.tool_executor.backend is not None:
                    close_backend = getattr(self.tool_executor.backend, 'close', None)
                    if close_backend is not None:
                        cleanup = await close_backend()
        return cleanup

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
        self._require_idle()
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
            self.verification_history.clear()
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
        return self.session.history()

    def session_candidates(self) -> str:
        return self.session.candidates()

    def session_rename(self, name: str) -> str:
        return self.session.rename(name)

    def _resume_target(self, identifier: str):
        return self.session.resume_target(identifier, getattr(self.client, 'provider', ''))

    def session_resume(self, identifier: str) -> str:
        self._require_idle()
        state, journal = self._resume_target(identifier)
        if self.session_journal is not None and state.info.session_id == self.session_journal.session_id:
            return f'Session {state.info.session_id} is already active.'
        if self.session_journal is not None:
            self.session_journal.record_stopped()
        self.messages[:] = list(state.messages)
        self.inbox.restore(state.pending_inputs)
        self.verification_history[:] = state.verification_history
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
        self._require_idle()
        if self.session_store is None or self.session_journal is None:
            raise ValueError('Session persistence is unavailable.')
        if self.session_journal.deferred:
            raise ValueError('Start a conversation before creating a branch.')
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
        self.inbox.restore(())
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
        self._require_idle()
        self.inbox.restore(())
        self.verification_history.clear()
        if self.session_store is None or self.session_journal is None:
            self.messages.clear()
            self.task_manager.restore(None)
            return 'Cleared conversation history.'
        previous_id = self.session_journal.session_id
        self.session_journal.record_stopped()
        model = str(getattr(self.client, 'model', ''))
        journal = self.session_store.create(
            model=model, provider=str(getattr(self.client, 'provider', '')),
            deferred=getattr(self, 'defer_new_sessions', False),
        )
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
        blocks = result.metadata.get('content_blocks') or ([result.metadata['image']] if 'image' in result.metadata else [])
        if blocks:
            from dataclasses import replace
            from forge.messages import normalize_input
            description = replace(result, metadata={k: v for k, v in result.metadata.items() if k not in {'image', 'content_blocks'}})
            body = [{'type': 'text', 'text': serialize_tool_result(description)}, *normalize_input(blocks)]
        else:
            body = serialize_tool_result(result)
        content.append(
            {
                'type': 'tool_result',
                'tool_use_id': tool_call.id,
                'content': body,
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
    environment_epoch: int | None = None,
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
            diagnostic=str(result.content)[-8_000:],
            environment_epoch=(
                environment_epoch
                if environment_epoch is not None
                else int(metadata.get('environment_epoch', 0))
            ),
            coverage=tuple(
                str(item) for item in metadata.get('covers', ())
                if str(item).strip()
            ),
            limitations=tuple(
                str(item) for item in metadata.get('limitations', ())
                if str(item).strip()
            ),
            stdin_sha256=str(metadata.get('stdin_sha256', '')),
            evidence_valid=bool(metadata.get('evidence_valid', True)),
            evidence_issues=tuple(str(item) for item in metadata.get('evidence_issues', ())),
            check_signature=str(metadata.get('check_signature', '')),
            requirement_ids=tuple(str(item) for item in metadata.get('requirement_ids', ())),
            asserted_requirement_ids=tuple(str(item) for item in metadata.get('asserted_requirement_ids', ())),
            verification_id=str(metadata.get('verification_id', '')),
            output_checks=tuple(metadata.get('output_checks', ())),
            supersedes=tuple(str(item) for item in metadata.get('supersedes', ())),
            revision_reason=str(metadata.get('revision_reason', '')),
            check_id=str(metadata.get('check_id', '')),
            check_spec=dict(metadata.get('check_spec', {})),
        )
    except (KeyError, TypeError, ValueError):
        return None




def optional_int(value: object) -> int | None:
    '''Parse optional numeric execution metadata without trusting its shape.'''
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


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


from forge.tools.paths import mutation_target_paths, checkpoint_mutation_paths


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
            'text_no_change',
            'git_diff_path_is_directory',
        }
    )
