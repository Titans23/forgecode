"""Small programmatic entry shared by terminal, SDK and evaluation consumers.

The Journal and Conversation remain the facts for a session and active turn.
Profiles select entry behavior; sandbox permissions are an independent explicit input.
"""
from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from pathlib import Path

from forge.config import ForgeConfig, model_environment
from forge.agents import AgentDefinition
from forge.messages import UserInput
from forge.permissions.policy import PermissionManager
from forge.runtime.dependencies import RuntimeBindings
from forge.runtime.startup import ModelStartup
from forge.runtime.factory import create_runtime
from forge.runtime.state import TurnCompleted, TurnResult
from forge.runtime.model_registry import ProviderRegistry, ModelSelection, model_catalog
from forge.sessions.trajectory import TrajectoryRecorder, sanitize


@dataclass(frozen=True, slots=True)
class RunProfile:
    name: str = 'terminal'
    intent_routing: bool = False

    def __post_init__(self):
        if type(self.intent_routing) is not bool:
            raise ValueError('intent_routing must be a boolean')

    @classmethod
    def evaluation(cls):
        return cls(name='evaluation')


@dataclass(frozen=True, slots=True)
class SessionOptions:
    root: Path | None = None
    sandbox: str | None = None
    approval: str | None = None
    read_roots: tuple[Path, ...] = ()
    write_roots: tuple[Path, ...] | None = None
    data_root: Path | None = None
    config: ForgeConfig | None = None
    profile: RunProfile = RunProfile()
    agent: AgentDefinition = AgentDefinition()
    models: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self):
        if self.sandbox is None:
            scoped = self.write_roots is not None or self.agent.sandbox_provider not in (None, 'native')
            object.__setattr__(self, 'sandbox', 'workspace-write' if scoped else 'full-access')
        if self.sandbox == 'danger-full-access':
            object.__setattr__(self, 'sandbox', 'full-access')
        if self.sandbox not in ('read-only', 'workspace-write', 'full-access'):
            raise ValueError('Sandbox must be read-only, workspace-write or full-access')
        if self.approval is None:
            object.__setattr__(self, 'approval', 'never' if self.sandbox == 'full-access' else 'ask')
        if self.approval not in ('ask', 'never', 'plan', 'supervised', 'auto'):
            raise ValueError('Approval must be ask or never (legacy: plan, supervised, auto)')
        if self.agent.base == 'coding' and self.root is None:
            raise ValueError('The coding agent requires a workspace')
        if self.agent.base == 'coding' and self.sandbox != 'full-access' and self.agent.sandbox_provider is None:
            raise ValueError('A confined mode requires a sandbox provider; select full-access for host execution')
        if self.agent.base == 'core' and (self.read_roots or self.write_roots):
            raise ValueError('Core agents do not grant filesystem roots; select coding for file tools')


def event_dict(event):
    """Versioned, JSON-compatible event; opaque provider continuations stay private."""
    from forge.runtime.state import ModelProviderState
    if isinstance(event, ModelProviderState):
        return {'schema_version': 'forge.harness.event.v1', 'type': 'ModelProviderState',
            'payload': {'provider': event.provider}}
    if not is_dataclass(event):
        raise TypeError('Expected a Harness event')
    return {'schema_version': 'forge.harness.event.v1', 'type': type(event).__name__,
        'payload': sanitize(asdict(event))}


async def open_runtime(options, *, approval_handler=None, model_client_factory=None,
                       runtime_factory=create_runtime, **resume):
    """Load only explicitly trusted plugins, then assemble the existing runtime."""
    host = None
    if options.agent.plugins:
        from forge.plugins import PluginHost
        import os
        data = Path(options.data_root or os.environ.get('FORGE_DATA_DIR') or Path.home() / '.forge').resolve()
        host = await PluginHost(workspace=options.root, data_root=data).load(options.agent.plugins)
    try:
        from forge.runtime.model_registry import initial_config
        from dataclasses import replace
        providers = ProviderRegistry()
        if host:
            for name, factory in host.providers.items():
                providers.register(name, factory)
        options = replace(options, config=initial_config(options))
        from forge.runtime.factory import create_model_client
        result = await _open_runtime(options, approval_handler=approval_handler,
            model_client_factory=model_client_factory or (providers.create if host and host.providers else create_model_client),
            runtime_factory=runtime_factory, plugin_host=host, **resume)
        if not hasattr(result[0], 'session_options'):
            result[0].session_options = options
        return result
    except BaseException as error:
        if host:
            error.plugin_cleanup = await host.close()
        raise


async def _open_runtime(options, *, approval_handler=None, model_client_factory=None,
                        runtime_factory=create_runtime, plugin_host=None, **resume):
    """Bind the execution policy; native commands prepare confinement on demand."""
    if options.agent.base == 'core':
        return await _open_core_runtime(options, approval_handler, model_client_factory, runtime_factory, resume, plugin_host)
    from forge.execution import ExecutionRequest, open_execution
    from forge.sessions.store import SessionStore

    root = Path(options.root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Workspace must be a directory')
    config = options.config or ForgeConfig.from_env(model_environment(root / '.env'))
    data = SessionStore(root, data_root=options.data_root).data_root
    if root == data or data.is_relative_to(root) or root.is_relative_to(data):
        raise ValueError('Session storage and workspace must be separate')
    data.mkdir(parents=True, exist_ok=True)
    execution = await open_execution(ExecutionRequest(root, data, options.sandbox,
        options.read_roots, options.write_roots),
        options.agent.sandbox_provider, plugins=plugin_host)
    backend, policy = execution.backend, execution.policy
    writes = tuple(map(Path, policy['filesystem']['write_roots']))
    permissions = PermissionManager(root, mode=options.approval, approval_handler=approval_handler,
        execution_mode=options.sandbox, authorized_write_roots=tuple(map(Path, policy['filesystem']['write_roots'])),
        load_stored_rules=options.profile.name != 'evaluation')
    from forge.runtime.factory import create_model_client
    client_factory = model_client_factory or create_model_client
    startup = ModelStartup(client_factory)
    bindings = RuntimeBindings(config=config, data_root=data, model_client_factory=startup.create,
        permission_manager=permissions, backend=backend,
        trusted_extensions=options.sandbox == 'full-access' and options.profile.name != 'evaluation',
        intent_routing=options.profile.intent_routing,
        plugin_host=plugin_host,
        model_aliases=model_catalog(options),
        model_environment=model_environment(root / '.env', normalize_aliases=False))
    try:
        from forge.runtime.profile import ExecutionProfile
        conversation, journal, state = runtime_factory(root, bindings=bindings,
            execution_profile=ExecutionProfile.sandbox() if writes or options.sandbox == 'full-access' else ExecutionProfile.host(),
            **({'agent': options.agent} if options.agent != AgentDefinition() else {}), **resume)
        conversation.session_options = options
        conversation.execution_policy = policy
        conversation.default_sandbox_mode = options.sandbox
        conversation.default_approval_policy = options.approval
        permissions.bind_session(journal)
        if state is not None and state.sandbox_mode is not None:
            await conversation.select_sandbox(state.sandbox_mode, persist=False)
            backend = conversation.tool_executor.backend
            policy = conversation.execution_policy
        if state is not None and state.approval_policy is not None:
            permissions.mode = state.approval_policy
        conversation.session_options = replace(conversation.session_options, approval=permissions.mode)
        if state is None or state.sandbox_mode is None or state.approval_policy is None:
            journal.append('permissions_changed', {'sandbox_mode': permissions.execution_mode, 'approval_policy': permissions.mode})
        journal.append('execution_profile', {'profile': asdict(options.profile), 'sandbox': permissions.execution_mode,
            'sandbox_provider': options.agent.sandbox_provider if permissions.execution_mode != 'full-access' else None,
            'approval': permissions.mode, 'policy': policy})
        return conversation, journal, state, backend
    except BaseException as error:
        failures = await startup.close()
        try:
            cleanup = await backend.close()
        except Exception as failure:
            cleanup = {'state': 'unknown', 'reason': type(failure).__name__}
        error.cleanup_report = ({'state': 'unknown', 'client_close_errors': failures, 'backend': cleanup}
            if failures else cleanup)
        raise
    finally:
        startup.release()


async def _open_core_runtime(options, approval_handler, client_factory, runtime_factory, resume, plugin_host=None):
    """Use the same loop and Journal, with a private logical scope and no OS worker."""
    from forge.runtime.factory import create_model_client
    import os
    data = Path(options.data_root or os.environ.get('FORGE_DATA_DIR') or Path.home() / '.forge').resolve()
    scope = data / 'agents' / options.agent.name
    scope.mkdir(parents=True, exist_ok=True)
    config = options.config or ForgeConfig.from_env(model_environment())
    startup = ModelStartup(client_factory or create_model_client)
    permissions = PermissionManager(scope, mode=options.approval, approval_handler=approval_handler,
                                    load_stored_rules=False, execution_mode=options.sandbox)
    bindings = RuntimeBindings(config=config, data_root=data, model_client_factory=startup.create,
        permission_manager=permissions, trusted_extensions=False, intent_routing=False,
        plugin_host=plugin_host,
        model_aliases=model_catalog(options),
        model_environment=model_environment(normalize_aliases=False))
    try:
        conversation, journal, state = runtime_factory(scope, bindings=bindings, agent=options.agent, **resume)
        permissions.bind_session(journal)
        conversation.default_approval_policy = options.approval
        if state is not None and state.approval_policy is not None:
            permissions.mode = state.approval_policy
        else:
            journal.append('permission_mode_changed', {'mode': permissions.mode})
        journal.append('agent_configuration', {'name': options.agent.name, 'base': 'core', 'workspace': None})
        return conversation, journal, state, None
    except BaseException as error:
        failures = await startup.close()
        if failures:
            error.cleanup_report = {'state': 'unknown', 'client_close_errors': failures}
        raise
    finally:
        startup.release()


class HarnessSession:
    """One session, sequential turns, consumer-owned streaming and explicit cancellation."""
    def __init__(self, conversation, journal, backend, recorder):
        self.conversation, self._journal = conversation, journal
        self._backend, self.recorder = backend, recorder
        self._task = None
        self._closed = False
        self.cleanup = None

    @property
    def backend(self):
        executor = self.conversation.tool_executor
        return executor.backend if executor is not None else self._backend

    @property
    def journal(self):
        return self.conversation.session_journal or self._journal

    @classmethod
    async def open(cls, options: SessionOptions, *, approval_handler=None, model_client_factory=None,
                   trajectory_recorder: TrajectoryRecorder | None = None, **resume):
        conversation, journal, _, backend = await open_runtime(options,
            approval_handler=approval_handler, model_client_factory=model_client_factory, **resume)
        return cls(conversation, journal, backend, trajectory_recorder)

    @property
    def session_id(self):
        return self.journal.session_id

    @property
    def running(self):
        return self._task is not None

    def queue_input(self, prompt: UserInput, *, follow_up=False):
        if self._closed:
            raise RuntimeError('Session is closed')
        return self.conversation.queue_input(prompt, follow_up=follow_up)

    @property
    def pending_inputs(self):
        return self.conversation.inbox.pending

    async def run_next(self):
        item = self.conversation.inbox.peek()
        if item is None:
            raise ValueError('No queued input')
        result = None
        async for event in self.stream(item['content'], input_id=item['id']):
            if isinstance(event, TurnCompleted):
                result = event.result
        if result is None:
            raise RuntimeError('Harness ended without a terminal result')
        return result

    async def stream(self, prompt: UserInput, *, input_id=None):
        if self._closed or self.running:
            raise RuntimeError('Session is closed or already running a turn')
        from forge.messages import normalize_input
        prompt = normalize_input(prompt)
        self._task = asyncio.current_task()
        try:
            if self.recorder is not None:
                self.recorder.record_user_message(prompt)
            async with aclosing(self.conversation.stream(prompt, **({'input_id': input_id} if input_id else {}))) as stream:
                async for event in stream:
                    if self.recorder is not None:
                        self.recorder.record_event(event)
                    yield event
                    if self._task.cancelling():
                        # Deliver a pending cancel once, before closing streams.
                        # A caught cancellation may leave cancelling() nonzero.
                        await asyncio.sleep(0)
        except Exception as error:
            if self.recorder is not None:
                self.recorder.record_error(error)
            raise
        finally:
            self._task = None

    async def run(self, prompt: UserInput) -> TurnResult:
        result = None
        async for event in self.stream(prompt):
            if isinstance(event, TurnCompleted):
                result = event.result
        if result is None:
            raise RuntimeError('Harness ended without a terminal result')
        return result

    def cancel(self):
        if self._task is None:
            return False
        self._task.cancel()
        return True

    async def set_model(self, alias):
        if self._closed or self.running:
            raise RuntimeError('Model changes require an idle open session')
        return await self.conversation.select_model(alias)

    async def set_sandbox(self, mode):
        if self._closed or self.running:
            raise RuntimeError('Sandbox changes require an idle open session')
        return await self.conversation.select_sandbox(mode)

    async def set_permissions(self, preset):
        """Select one named sandbox/approval pair for this idle session."""
        if self._closed or self.running:
            raise RuntimeError('Session is closed or running')
        return await self.conversation.select_permissions(preset)

    def add_completion_check(self, name, callback):
        """Add a deterministic check returning unmet reasons; never grants authority."""
        if self._closed or self.running:
            raise RuntimeError('Checks must be registered at an idle session boundary')
        if not name or name in self.conversation.completion_checks or not callable(callback):
            raise ValueError('Completion check needs a unique name and callable')
        self.conversation.completion_checks[name] = callback

    def service(self, name):
        """Access an explicitly loaded plugin service, including a channel adapter."""
        host = getattr(self.conversation, 'plugin_host', None)
        if self._closed or host is None or name not in host.services:
            raise ValueError('Service is not available in this open session')
        return host.services[name]

    async def close(self):
        if self._closed:
            return self.cleanup
        if self._task is asyncio.current_task():
            raise RuntimeError('Close the stream before closing its session')
        self._closed = True
        if self._task is not None:
            task = self._task
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.cleanup = await self.conversation.runtime_close()
        self.journal.record_stopped()
        return self.cleanup

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
