"""Small programmatic entry shared by terminal, SDK and evaluation consumers.

The Journal and Conversation remain the facts for a session and active turn.
Profiles select budgets; sandbox permissions are an independent explicit input.
"""
from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from uuid import uuid4

from forge.config import ForgeConfig, model_environment
from forge.messages import UserInput
from forge.permissions.policy import PermissionManager
from forge.runtime.dependencies import RuntimeBindings
from forge.runtime.factory import create_runtime
from forge.runtime.state import TurnCompleted, TurnResult
from forge.sessions.trajectory import TrajectoryRecorder, sanitize


@dataclass(frozen=True, slots=True)
class RunProfile:
    name: str = 'terminal'
    max_model_calls: int = 80
    max_tool_calls: int = 120
    wall_seconds: int = 1800
    intent_routing: bool = True

    def __post_init__(self):
        if type(self.intent_routing) is not bool:
            raise ValueError('intent_routing must be a boolean')
        if any(type(value) is not int or value < 1 for value in (self.max_model_calls, self.max_tool_calls, self.wall_seconds)):
            raise ValueError('Profile budgets must be positive')

    @classmethod
    def evaluation(cls):
        return cls(name='evaluation')


@dataclass(frozen=True, slots=True)
class SessionOptions:
    root: Path
    sandbox: str = 'workspace-write'
    approval: str = 'supervised'
    read_roots: tuple[Path, ...] = ()
    write_roots: tuple[Path, ...] | None = None
    data_root: Path | None = None
    config: ForgeConfig | None = None
    profile: RunProfile = RunProfile()

    def __post_init__(self):
        if self.sandbox not in ('read-only', 'workspace-write', 'full-access'):
            raise ValueError('Sandbox must be read-only, workspace-write or full-access')
        if self.approval not in ('plan', 'supervised', 'auto'):
            raise ValueError('Approval must be plan, supervised or auto')
        if self.sandbox == 'read-only' and self.write_roots:
            raise ValueError('Read-only cannot grant write roots')


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
    """Initialize the actual boundary before constructing clients or opening a Journal."""
    from forge.sandbox.application_backend import NativeBackendFactory
    from forge.sandbox.file_client import FileWorkerClient
    from forge.sandbox.policy import compile_policy
    from forge.sandbox.tool_backend import FileToolBackend
    from forge.sessions.store import SessionStore

    root = Path(options.root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Workspace must be a directory')
    config = options.config or ForgeConfig.from_env(model_environment(root / '.env'))
    data = SessionStore(root, data_root=options.data_root).data_root
    if root == data or data.is_relative_to(root) or root.is_relative_to(data):
        raise ValueError('Session storage and workspace must be separate')
    data.mkdir(parents=True, exist_ok=True)
    identity = 'turn-' + str(uuid4())
    control = data / 'native' / identity
    info = root.stat()
    workspace = {'id': 'ws-' + str(uuid4()), 'canonical_path': str(root),
        'file_identity': f'{info.st_dev}:{info.st_ino}'}
    writes = (() if options.sandbox == 'read-only' else (root,)) if options.write_roots is None else options.write_roots
    policy = {'schema_version': 'forge.sandbox.policy.v1', 'policy_id': 'policy-' + str(uuid4()),
        'workspace_id': workspace['id'], 'filesystem': {'read_mode': 'host_default',
            'read_roots': [str(root), *(str(Path(p).resolve(strict=True)) for p in options.read_roots)],
            'write_roots': [str(Path(p).resolve(strict=True)) for p in writes],
            'protected_paths': [str(data)], 'deny_overrides_allow': True, 'reject_unsafe_links': True},
        'network': {'mode': 'inherit', 'allowed_domains': [], 'dns_isolation_required': False},
        'limits': {'memory_bytes': None, 'disk_bytes': None, 'pids': None,
            'wall_time_seconds': options.profile.wall_seconds, 'command_output_bytes': 1048576,
            'session_artifact_bytes': 16777216}, 'environment_keys': [], 'fallback': 'deny',
        'session_mutation': 'replace_session'}
    policy = compile_policy(policy, workspace, control_roots=(control,)).value
    if options.sandbox == 'full-access':
        backend = FileToolBackend(FileWorkerClient(workspace, policy, local_control=control))
    else:
        owner = {'engine_epoch': 'epoch-' + str(uuid4()), 'sandbox_session_id': 'sandbox-' + str(uuid4()),
            'execution_id': None}
        backend = await NativeBackendFactory(data, mode=options.sandbox).open(workspace, policy, owner, identity)
    permissions = PermissionManager(root, mode=options.approval, approval_handler=approval_handler,
        execution_mode=options.sandbox, authorized_write_roots=tuple(map(Path, policy['filesystem']['write_roots'])))
    bindings = RuntimeBindings(config=config, data_root=data, model_client_factory=model_client_factory,
        permission_manager=permissions, backend=backend, trusted_extensions=options.sandbox == 'full-access',
        max_model_calls=options.profile.max_model_calls, max_tool_calls=options.profile.max_tool_calls,
        intent_routing=options.profile.intent_routing,
        wall_seconds=options.profile.wall_seconds)
    try:
        from forge.runtime.profile import ExecutionProfile
        conversation, journal, state = runtime_factory(root, bindings=bindings,
            execution_profile=ExecutionProfile.sandbox() if writes else ExecutionProfile.host(), **resume)
        permissions.bind_session(journal)
        journal.append('execution_profile', {'profile': asdict(options.profile), 'sandbox': options.sandbox,
            'approval': options.approval, 'policy': policy})
        return conversation, journal, state, backend
    except BaseException:
        await backend.close()
        raise


class HarnessSession:
    """One session, sequential turns, consumer-owned streaming and explicit cancellation."""
    def __init__(self, conversation, journal, backend, recorder):
        self.conversation, self.journal = conversation, journal
        self.backend, self.recorder = backend, recorder
        self._task = None
        self._closed = False
        self.cleanup = None

    @classmethod
    async def open(cls, options: SessionOptions, *, approval_handler=None, model_client_factory=None, **resume):
        conversation, journal, _, backend = await open_runtime(options,
            approval_handler=approval_handler, model_client_factory=model_client_factory, **resume)
        recorder = TrajectoryRecorder(journal.path.with_suffix('.trajectory.jsonl'))
        return cls(conversation, journal, backend, recorder)

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
            self.recorder.record_user_message(prompt)
            async with aclosing(self.conversation.stream(prompt, **({'input_id': input_id} if input_id else {}))) as stream:
                async for event in stream:
                    self.recorder.record_event(event)
                    yield event
                    if self._task.cancelling():
                        # Deliver a pending cancel once, before closing streams.
                        # A caught cancellation may leave cancelling() nonzero.
                        await asyncio.sleep(0)
        except Exception as error:
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
