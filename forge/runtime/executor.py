'''Unified validation, authorization, execution, and persistence boundary.'''

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from contextvars import Context, copy_context
from hashlib import sha256
from time import monotonic
from typing import Any
from uuid import uuid4

from forge.hooks import HookEvent, HookOutcome
from forge.permissions.policy import PermissionManager
from forge.permissions.context import ToolApprovalContext, tool_approval_context
from forge.permissions.risk import classify_tool_call
from forge.runtime.state import ToolCall
from forge.runtime.turn_state import (
    ExecutionRecord,
    ExecutionStatus,
    execution_record_from_result,
)
from forge.runtime.workspace import WorkspaceChange, WorkspaceTracker, fingerprint_path
from forge.sessions.checkpoint import CheckpointError, CheckpointStore
from forge.tools.base import ToolRegistry, ToolResult


HookRunner = Callable[[HookEvent], Awaitable[HookOutcome]]
ScopeChecker = Callable[[ToolCall], ToolResult | None]
PathResolver = Callable[[ToolCall], tuple[str, ...]]
HookContextSink = Callable[[HookOutcome], None]
ResultTransformer = Callable[[ToolResult], Awaitable[ToolResult]]


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    '''Result plus a durable status record for one tool request.'''

    result: ToolResult
    record: ExecutionRecord
    arguments: dict[str, Any]
    workspace_change: WorkspaceChange | None = None


@dataclass(frozen=True, slots=True)
class PreparedExecution:
    call: ToolCall
    run: Callable[[], Awaitable[ExecutionOutcome | DispatchedExecution]]
    skip: Callable[[str], ExecutionOutcome]
    abort: Callable[[], ExecutionOutcome]
    context: Context


@dataclass(frozen=True, slots=True)
class DispatchedExecution:
    call: ToolCall
    result: ToolResult
    finish: Callable[[], Awaitable[ExecutionOutcome]]
    abort: Callable[[], ExecutionOutcome]
    failed: Callable[[Exception], ExecutionOutcome]
    context: Context


class ToolExecutor:
    '''The only component allowed to cross from a tool request to execution.'''

    def __init__(
        self,
        registry: ToolRegistry,
        permission_manager: PermissionManager,
        *,
        workspace_tracker: WorkspaceTracker | None = None,
        hook_runner: HookRunner | None = None,
        session_journal: Any | None = None,
        checkpoint_store: CheckpointStore | None = None,
        path_resolver: PathResolver | None = None,
        hook_context_sink: HookContextSink | None = None,
        backend: Any | None = None,
    ) -> None:
        self.registry = registry
        self.permission_manager = permission_manager
        self.workspace_tracker = workspace_tracker
        self.hook_runner = hook_runner
        self.session_journal = session_journal
        self.checkpoint_store = checkpoint_store
        self.path_resolver = path_resolver or default_tool_paths
        self.hook_context_sink = hook_context_sink
        self.backend = backend
        self._read_cache: dict[str, ToolResult] = {}

    async def execute(self, call: ToolCall, **options) -> ExecutionOutcome:
        prepared = await self.prepare(call, **options)
        if isinstance(prepared, ExecutionOutcome):
            return prepared
        dispatched = await self.dispatch(prepared)
        return await self.finish(dispatched)

    async def prepare(self, call: ToolCall, **options) -> PreparedExecution | ExecutionOutcome:
        from forge.observability.events import current
        from contextlib import nullcontext
        recorder = current()
        with recorder.tool(call) if recorder else nullcontext():
            return await self._prepare(call, **options)

    async def dispatch(self, prepared: PreparedExecution) -> DispatchedExecution | ExecutionOutcome:
        return await asyncio.create_task(prepared.run(), context=prepared.context.copy())

    async def finish(self, dispatched: DispatchedExecution | ExecutionOutcome) -> ExecutionOutcome:
        if isinstance(dispatched, ExecutionOutcome):
            return dispatched
        try:
            return await asyncio.create_task(dispatched.finish(), context=dispatched.context.copy())
        except asyncio.CancelledError:
            return dispatched.context.run(dispatched.abort)
        except Exception as error:
            return dispatched.context.run(dispatched.failed, error)

    def skip(self, prepared: PreparedExecution, reason: str) -> ExecutionOutcome:
        return prepared.context.run(prepared.skip, reason)

    async def _prepare(
        self,
        call: ToolCall,
        *,
        checkpoint_id: str | None = None,
        scope_checker: ScopeChecker | None = None,
        result_transformer: ResultTransformer | None = None,
    ) -> PreparedExecution | ExecutionOutcome:
        '''Validate and authorize; the scheduler owns dispatch and settlement.'''
        started = monotonic()
        arguments = dict(call.arguments)
        effect = self.registry.effect(call.name)
        revision, epoch = self._state()

        validation = self.registry.validate(call.name, arguments)
        if validation is not None:
            return self._outcome(
                call,
                validation,
                'rejected',
                started,
                arguments,
                revision,
                epoch,
            )

        pre = await self._hook('PreToolUse', call, arguments)
        if pre.arguments is not None:
            arguments = dict(pre.arguments)
        if not pre.allowed:
            result = ToolResult.fail(
                'hook_denied',
                'PreToolUse hook denied this operation.',
                content=pre.reason,
            )
            return self._outcome(
                call, result, 'rejected', started, arguments, revision, epoch
            )
        validation = self.registry.validate(call.name, arguments)
        if validation is not None:
            return self._outcome(
                call,
                validation,
                'rejected',
                started,
                arguments,
                revision,
                epoch,
            )

        effective_call = ToolCall(call.index, call.id, call.name, arguments)
        if effect == 'workspace_write':
            before_edit = await self._hook('BeforeFileEdit', effective_call, arguments)
            if before_edit.arguments is not None:
                arguments = dict(before_edit.arguments)
                effective_call = ToolCall(
                    call.index,
                    call.id,
                    call.name,
                    arguments,
                )
                validation = self.registry.validate(call.name, arguments)
                if validation is not None:
                    return self._outcome(
                        call,
                        validation,
                        'rejected',
                        started,
                        arguments,
                        revision,
                        epoch,
                    )
            if not before_edit.allowed:
                result = ToolResult.fail(
                    'hook_denied',
                    'BeforeFileEdit hook denied this operation.',
                    content=before_edit.reason,
                )
                return self._outcome(
                    call, result, 'rejected', started, arguments, revision, epoch
                )

        if scope_checker is not None:
            scope_error = scope_checker(effective_call)
            if scope_error is not None:
                return self._outcome(
                    call,
                    scope_error,
                    'rejected',
                    started,
                    arguments,
                    revision,
                    epoch,
                )

        request = self.registry.permission_request(call.name, arguments)
        if request is None:
            request = classify_tool_call(effective_call, effect)
            if self.permission_manager.execution_mode is not None:
                # Legacy command/name heuristics are not a DSH sandbox policy.
                # Explicit tool requests and configured deny rules remain authoritative.
                request = replace(request, hard_deny=False,
                    reason='Access is governed by the selected sandbox mode.')
        execution_call = effective_call
        wider_mode = None
        from forge.tools.base import SandboxInput
        implementation = self.registry.implementation(call.name)
        input_model = getattr(implementation, 'input_model', None)
        if isinstance(input_model, type) and issubclass(input_model, SandboxInput):
            target = arguments.get('sandbox_permissions')
            target = 'full-access' if target == 'danger-full-access' else target
            current_mode = self.permission_manager.execution_mode
            if target is not None and target != current_mode:
                if (target not in {'read-only': ('workspace-write', 'full-access'),
                                   'workspace-write': ('full-access',)}.get(current_mode, ())
                        or not callable(getattr(self.backend, 'open_approved', None))
                        or not callable(getattr(self.backend, 'close_approved', None))):
                    return self._outcome(call, ToolResult.fail('permission_denied',
                        'This execution provider cannot widen the current sandbox to the requested mode.'),
                        'rejected', started, arguments, revision, epoch)
                wider_mode = target
                consequence = ('本次操作将使用宿主权限执行，不受工作区沙盒限制。' if target == 'full-access'
                               else '本次操作将允许写入已授权的工作区范围。')
                request = replace(request, sandbox_mode=target, risk=request.risk if request.hard_deny else 'high',
                    reason=(request.reason if request.hard_deny else
                            f'{consequence} 仅本次有效，会话默认模式不变。\n理由：{arguments["justification"]}'),
                    preview=json.dumps(arguments, ensure_ascii=False, indent=2),
                    arguments_hash=sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest())
            # Approval fields are control input, not part of a stored check or subprocess input.
            execution_call = replace(effective_call, arguments={key: value for key, value in arguments.items()
                if key not in {'sandbox_permissions', 'justification'}})
        from forge.observability.events import active, current
        recorder=current()
        if recorder:
            recorder.tool_intent(effective_call)
        execution_id=active.get().scope.execution_id if recorder else 'exec-'+str(uuid4())
        token = tool_approval_context.set(ToolApprovalContext(execution_id, effective_call, self.workspace_tracker))
        try:
            decision = await self.permission_manager.authorize(request)
        finally:
            tool_approval_context.reset(token)
        if decision.action == 'deny':
            result = ToolResult.fail(
                'permission_denied',
                decision.reason,
                details={
                    'tool': call.name,
                    'capability': request.capability,
                    'risk': request.risk,
                    'targets': list(request.targets),
                    'source': decision.source,
                },
            )
            return self._outcome(
                call, result, 'rejected', started, arguments, revision, epoch
            )

        checkpoint_paths = self._checkpoint_paths(effective_call, effect)
        cache_key = self._cache_key(effective_call) if effect == 'read_only' else None
        if cache_key is not None and cache_key in self._read_cache:
            cached = replace(self._read_cache[cache_key], metadata={
                **self._read_cache[cache_key].metadata, 'cache_hit': True,
            })
            await self._hook('PostToolUse', effective_call, arguments, result=cached)
            return self._outcome(call, cached, 'cached', started, arguments, *self._state())
        if effect != 'read_only':
            self._read_cache.clear()
            if self.workspace_tracker is not None:
                await self.workspace_tracker.ensure_baseline()
        async def dispatch():
            nonlocal checkpoint_paths
            execution_started = False
            execution_backend = self.backend
            temporary_backend = None
            cleanup = None
            try:
                if wider_mode is not None:
                    temporary_backend = self.backend.open_approved(wider_mode)
                    execution_backend = temporary_backend
                if self.workspace_tracker is not None and effect == 'workspace_write':
                    await self.workspace_tracker.watch_paths_async(checkpoint_paths)
                if checkpoint_id is not None and checkpoint_paths:
                    if self.checkpoint_store is None:
                        raise CheckpointError('Checkpoint store is unavailable.')
                    if execution_backend is not None and hasattr(execution_backend, 'snapshot'):
                        observed = await execution_backend.snapshot(checkpoint_paths, recursive=call.name == 'remove_directory')
                        self.checkpoint_store.capture_observations(checkpoint_id, observed['files'],
                            host=True)
                        checkpoint_paths = tuple(observed['files'])
                    else:
                        self.checkpoint_store.capture_before(checkpoint_id, checkpoint_paths)
                await self._journal_started(effective_call)
                execution_started = True
                if execution_backend is not None:
                    result = await execution_backend.execute(execution_call, self.registry)
                else:
                    result = await self.registry.execute(call.name, execution_call.arguments)
                if checkpoint_id is not None and checkpoint_paths:
                    if execution_backend is not None and hasattr(execution_backend, 'snapshot'):
                        observed = await execution_backend.snapshot(checkpoint_paths)
                        self.checkpoint_store.record_observations(checkpoint_id, observed['files'])
                    else:
                        self.checkpoint_store.record_after(checkpoint_id, checkpoint_paths)
            except asyncio.CancelledError:
                result = ToolResult.fail(
                    'execution_cancelled',
                    'Tool execution was cancelled before a determinate result was recorded.',
                )
                status: ExecutionStatus = (
                    'indeterminate' if effect in {'workspace_write', 'process'} else 'cancelled'
                )
                return self._outcome(
                    call,
                    result,
                    status,
                    started,
                    arguments,
                    *self._state(),
                )
            except CheckpointError as error:
                result = ToolResult.fail(
                    'checkpoint_failed',
                    ('The operation ran but its checkpoint result could not be recorded. Inspect current files before any retry.'
                     if execution_started else
                     'Workspace execution was refused because its checkpoint could not be created.'),
                    content=str(error),
                )
                return self._outcome(
                    call,
                    result,
                    'indeterminate' if execution_started else 'rejected',
                    started,
                    arguments,
                    revision,
                    epoch,
                )
            except Exception as error:
                result = ToolResult.fail(
                    getattr(error, 'kind', 'executor_failed'),
                    f'Tool executor failed before a determinate result: {error}',
                )
                return self._outcome(
                    call,
                    result,
                    'indeterminate' if execution_started and effect in {'workspace_write', 'process'} else 'rejected',
                    started,
                    arguments,
                    *self._state(),
                )
            finally:
                if temporary_backend is not None:
                    cleanup = await self.backend.close_approved(temporary_backend)

            if temporary_backend is not None:
                result = replace(result, metadata={**result.metadata, 'execution_mode': wider_mode,
                    'sandbox_override': 'allow_once', 'cleanup': cleanup,
                    **({'sandbox_covered': False} if wider_mode == 'full-access' else {})})
                if cleanup.get('state') != 'clean':
                    result = ToolResult.fail('INDETERMINATE', 'Operation cleanup was not proven clean; inspect before retrying.',
                        content=result.content, metadata=result.metadata)

            async def finalize():
                nonlocal result
                not_started = (result.error is not None
                    and result.error.code == 'SANDBOX_UNAVAILABLE'
                    and result.error.details.get('command_started') is False)
                if (
                    not not_started and effect == 'process'
                    and call.name != 'verify'
                    and self.workspace_tracker is not None
                    and hasattr(self.workspace_tracker, 'mark_environment_change')
                ):
                    self.workspace_tracker.mark_environment_change()
                change = (
                    (
                        await self.workspace_tracker.refresh_paths(checkpoint_paths)
                        if effect == 'workspace_write' and hasattr(self.workspace_tracker, 'refresh_paths')
                        else await self.workspace_tracker.refresh()
                    )
                    if self.workspace_tracker is not None and effect != 'read_only' and not not_started
                    else None
                )
                if effect == 'process':
                    metadata = dict(result.metadata)
                    metadata['environment_epoch'] = self._state()[1]
                    if change is not None:
                        metadata['workspace_revision'] = change.revision
                    result = replace(result, metadata=metadata)
                if effect == 'workspace_write' and result.success:
                    await self._hook('AfterFileEdit', effective_call, arguments)
                await self._hook('PostToolUse', effective_call, arguments, result=result)
                if result_transformer is not None:
                    result = await result_transformer(result)
                if cache_key is not None and result.success:
                    # Hooks and the read itself may have changed the target; only cache
                    # if the same content fingerprint is still valid after execution.
                    if self._cache_key(effective_call) == cache_key:
                        if len(self._read_cache) >= 128:
                            self._read_cache.clear()
                        self._read_cache[cache_key] = result
                return self._outcome(
                    call,
                    result,
                    ('indeterminate' if result.error and result.error.code == 'INDETERMINATE'
                     else 'rejected' if not_started else 'executed'),
                    started,
                    arguments,
                    *self._state(),
                    workspace_change=change,
                )

            return DispatchedExecution(effective_call, result, finalize, abort, failed, copy_context())

        def skip(reason):
            return self._outcome(call, ToolResult.fail(reason, 'The tool did not execute before the batch stopped.'),
                                 'cancelled', started, arguments, *self._state())

        def abort():
            return self._outcome(call, ToolResult.fail('execution_cancelled',
                'Execution was interrupted; effects of an admitted write or process may be unknown.'),
                'indeterminate' if effect in {'workspace_write', 'process'} else 'cancelled',
                started, arguments, *self._state())

        def failed(error):
            return self._outcome(call, ToolResult.fail('INDETERMINATE',
                f'Tool settlement failed after execution: {type(error).__name__}. Inspect before retrying.'),
                'indeterminate', started, arguments, *self._state())

        return PreparedExecution(effective_call, dispatch, skip, abort, copy_context())

    def record_result(
        self,
        call: ToolCall,
        result: ToolResult,
        *,
        status: ExecutionStatus,
        arguments: Mapping[str, Any] | None = None,
    ) -> ExecutionOutcome:
        '''Record a non-executed or cached result at the same boundary.

        Semantic loop guards may decide that a requested operation must not
        run (for example a malformed batch tail or a cached read).  They must
        still produce the same durable execution record as the real path.
        '''
        started = monotonic()
        effective_arguments = dict(arguments or call.arguments)
        revision, epoch = self._state()
        from forge.observability.events import current
        from contextlib import nullcontext
        recorder=current()
        with recorder.tool(call) if recorder else nullcontext():
            return self._outcome(call,result,status,started,effective_arguments,revision,epoch)

    def _state(self) -> tuple[int, int]:
        if self.workspace_tracker is None:
            return 0, 0
        return (
            self.workspace_tracker.revision,
            getattr(self.workspace_tracker, 'environment_epoch', 0),
        )

    def _checkpoint_paths(
        self,
        call: ToolCall,
        effect: str | None,
    ) -> tuple[str, ...]:
        if effect != 'workspace_write' or self.path_resolver is None:
            return ()
        return self.path_resolver(call)

    async def _hook(
        self,
        name: str,
        call: ToolCall,
        arguments: Mapping[str, Any],
        *,
        result: ToolResult | None = None,
    ) -> HookOutcome:
        if self.hook_runner is None:
            return HookOutcome(arguments=dict(arguments))
        payload = {}
        if result is not None:
            payload = {
                'success': result.success,
                'summary': result.summary,
                'error_code': result.error.code if result.error else None,
            }
        outcome = await self.hook_runner(
            HookEvent(
                name=name,  # type: ignore[arg-type]
                tool_name=call.name,
                tool_call_id=call.id,
                arguments=dict(arguments),
                paths=self.path_resolver(call),
                payload=payload,
            )
        )
        if self.hook_context_sink is not None:
            self.hook_context_sink(outcome)
        return outcome

    async def _journal_started(self, call: ToolCall) -> None:
        if self.session_journal is not None:
            self.session_journal.record_tool_started(call.id,call.name,self.registry.audit_arguments(call.name,call.arguments),
                                                    provenance=self.registry.provenance(call.name))
        from forge.observability.events import current
        recorder=current()
        if recorder:
            if call.name=='verify':
                await recorder.before_verification()
            recorder.tool_started(call,workspace_revision=self._state()[0],environment_epoch=self._state()[1])

    def _outcome(
        self,
        call: ToolCall,
        result: ToolResult,
        status: ExecutionStatus,
        started: float,
        arguments: dict[str, Any],
        revision: int,
        epoch: int,
        *,
        workspace_change: WorkspaceChange | None = None,
    ) -> ExecutionOutcome:
        result = replace(result, metadata={
            **result.metadata,
            'execution_status': status,
            'workspace_revision': revision,
            'environment_epoch': epoch,
        })
        record = execution_record_from_result(
            call,
            result,
            status=status,
            duration_seconds=max(0.0, monotonic() - started),
            workspace_revision=revision,
            environment_epoch=epoch,
        )
        if self.session_journal is not None:
            self.session_journal.record_tool_completed(
                call.id,
                call.name,
                result.success,
                provenance=self.registry.provenance(call.name),
                status=status,
                error_code=record.error_code,
                workspace_revision=record.workspace_revision,
                environment_epoch=record.environment_epoch,
                duration_seconds=record.duration_seconds,
                exit_code=result.metadata.get('exit_code'),
            )
        from forge.observability.events import current
        recorder=current()
        if recorder:
            recorder.evidence_state(revision,epoch)
            recorder.tool_finished(ToolCall(call.index,call.id,call.name,arguments),result,status)
        return ExecutionOutcome(result, record, arguments, workspace_change)

    def _cache_key(self, call: ToolCall) -> str | None:
        # Only a local file read has a cheap, exact validity proof. Searches,
        # directory listings, commands, task state and MCP reads are not cached.
        if self.backend is not None and hasattr(self.backend, 'observer'):
            return None
        if call.name != 'read_file' or self.workspace_tracker is None:
            return None
        path = call.arguments.get('path')
        if not isinstance(path, str):
            return None
        from forge.tools.base import resolve_repository_path, ToolExecutionError
        try:
            resolved = resolve_repository_path(self.workspace_tracker.root, path)
            if not resolved.is_file():
                return None
            fingerprint = fingerprint_path(self.workspace_tracker.root, str(resolved))
        except (OSError, ValueError, ToolExecutionError):
            return None
        return json.dumps(call.arguments, sort_keys=True, ensure_ascii=False) + fingerprint


def default_tool_paths(call: ToolCall) -> tuple[str, ...]:
    '''Extract audit paths without deciding authorization.'''
    values: list[str] = []
    for key in ('path', 'cwd'):
        value = call.arguments.get(key)
        if isinstance(value, str) and value:
            values.append(value.replace('\\', '/'))
    return tuple(dict.fromkeys(values))
