"""Optional execution providers; no native backend is imported until selected."""
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Awaitable, Callable

from forge.runtime.dependencies import ToolExecutionBackend


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    root: Path
    data_root: Path
    mode: str
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...] | None
    wall_seconds: int


@dataclass(frozen=True, slots=True)
class ExecutionSetup:
    backend: ToolExecutionBackend
    policy: dict


SandboxProvider = Callable[[ExecutionRequest], Awaitable[ExecutionSetup]]


async def open_execution(request: ExecutionRequest, provider: str | None, *, plugins=None) -> ExecutionSetup:
    """Select once before model construction; a failed provider never falls back."""
    if request.mode == 'full-access':
        from forge.sandbox.provider import open_host_execution
        factory = open_host_execution
    elif provider == 'native':
        from forge.sandbox.provider import open_native_execution
        factory = open_native_execution
    elif plugins is not None and provider in plugins.sandbox_providers:
        factory = plugins.sandbox_providers[provider]
    else:
        raise ValueError(f'Sandbox provider is not registered: {provider}')
    setup = await factory(request)
    if not isinstance(setup, ExecutionSetup):
        raise TypeError('Sandbox provider must return ExecutionSetup')
    try:
        from forge.protocol import ContractError, validate
        validate('sandbox-policy', setup.policy)
        if setup.policy.get('execution_mode', request.mode) != request.mode:
            raise ContractError('Prepared execution mode differs from the requested mode',
                                kind='CAPABILITY_UNSATISFIED', code=-32010)
        setup.backend.check_ready(setup.policy,
            required_mode='local-trusted' if request.mode == 'full-access' else request.mode)
    except BaseException as error:
        try:
            error.cleanup_report = await setup.backend.close()
        except BaseException as cleanup_error:
            error.cleanup_report = {'state': 'unknown', 'reason': type(cleanup_error).__name__}
        raise
    return setup


async def select_session_mode(conversation, mode, *, persist=True):
    """Switch an idle session's execution policy, with one durable mode event."""
    conversation._require_idle()
    options = getattr(conversation, 'session_options', None)
    if options is None or options.agent.base != 'coding' or conversation.tool_executor is None:
        raise ValueError('This session has no coding execution provider')
    selected = replace(options, sandbox=mode)
    manager = conversation.permission_manager
    if manager.execution_mode == selected.sandbox:
        return selected.sandbox
    previous = conversation.tool_executor.backend
    setup = None
    conversation._running = True
    try:
        setup = await open_execution(ExecutionRequest(selected.root.resolve(), conversation.session_store.data_root,
            selected.sandbox, selected.read_roots, selected.write_roots, selected.profile.wall_seconds),
            selected.agent.sandbox_provider, plugins=getattr(conversation, 'plugin_host', None))
        old_client, new_client = getattr(previous, 'observer', None), getattr(setup.backend, 'observer', None)
        if old_client is not None and new_client is not None:
            if old_client.workspace['file_identity'] != new_client.workspace['file_identity']:
                raise ValueError('Workspace identity changed while selecting the sandbox mode')
        cleanup = await previous.close()
        if not cleanup or cleanup.get('state') != 'clean':
            raise ValueError('Previous execution cleanup is unconfirmed; sandbox mode was not changed')
        if persist:
            conversation.session_journal.append('sandbox_mode_changed', {'mode': selected.sandbox})
        # No await between the durable event and updating its live projection.
        if hasattr(previous, '_observed') and hasattr(setup.backend, '_observed'):
            setup.backend._observed.update(previous._observed)
        conversation.tool_executor.backend = setup.backend
        conversation.execution_policy = setup.policy
        conversation.tool_executor._read_cache.clear()
        if conversation.workspace_tracker is not None:
            conversation.workspace_tracker.observer = new_client
        from forge.subagents.explore import ExploreRepositoryTool
        explore = conversation.registry.implementation('explore_repository')
        if isinstance(explore, ExploreRepositoryTool):
            explore.tool_backend = setup.backend
        manager.execution_mode = selected.sandbox
        manager.authorized_write_roots = tuple(map(Path, setup.policy['filesystem']['write_roots']))
        conversation.session_options = selected
        return selected.sandbox
    except BaseException:
        if setup is not None and conversation.tool_executor.backend is not setup.backend:
            await setup.backend.close()
        raise
    finally:
        conversation._running = False
