"""Built-in execution providers over the existing file and native backends."""
from pathlib import Path
from copy import deepcopy
import tempfile
from uuid import uuid4

from forge.execution import ExecutionRequest, ExecutionSetup
from forge.sandbox.file_client import FileWorkerClient
from forge.sandbox.policy import compile_policy
from forge.sandbox.tool_backend import FileToolBackend


def platform_temp_roots():
    return tuple(dict.fromkeys(path.resolve() for path in (Path('/tmp'), Path(tempfile.gettempdir())) if path.is_dir()))


def _policy(request, factory=None):
    root, data = request.root, request.data_root
    identity = 'turn-' + str(uuid4())
    control = data / 'native' / identity
    info = root.stat()
    workspace = {'id': 'ws-' + str(uuid4()), 'canonical_path': str(root),
        'file_identity': f'{info.st_dev}:{info.st_ino}'}
    temp_roots = platform_temp_roots()
    writes = () if request.mode == 'read-only' else ((root, *temp_roots) if request.write_roots is None else request.write_roots)
    policy = {'schema_version': 'forge.sandbox.policy.v1', 'policy_id': 'policy-' + str(uuid4()),
        'execution_mode': request.mode,
        'workspace_id': workspace['id'], 'filesystem': {'read_mode': 'host_default',
            'read_roots': [str(root), *(str(Path(p).resolve(strict=True)) for p in request.read_roots)],
            'write_roots': [str(Path(p).resolve(strict=True)) for p in writes],
            'protected_paths': [], 'deny_overrides_allow': True, 'reject_unsafe_links': True},
        'network': {'mode': 'inherit', 'allowed_domains': [], 'dns_isolation_required': False},
        'limits': {'memory_bytes': None, 'disk_bytes': None, 'pids': None,
            'wall_time_seconds': request.wall_seconds, 'command_output_bytes': 1048576,
            'session_artifact_bytes': 16777216}, 'environment_keys': [], 'fallback': 'deny',
        'session_mutation': 'replace_session'}
    policy = compile_policy(policy, workspace, control_roots=(control,
        *(factory.protected_roots if factory is not None else ())),
        temporary_roots=temp_roots,
        audit_workspace=request.mode != 'full-access').value
    return workspace, policy, control, identity


async def open_host_execution(request: ExecutionRequest) -> ExecutionSetup:
    workspace, policy, control, _ = _policy(request)
    return ExecutionSetup(FileToolBackend(FileWorkerClient(workspace, policy, local_control=control)), policy)


async def open_native_execution(request: ExecutionRequest) -> ExecutionSetup:
    from forge.sandbox.application_backend import NativeBackendFactory
    from forge.sandbox.policy_backend import PolicyToolBackend
    factory = NativeBackendFactory(request.data_root, mode=request.mode)
    workspace, policy, control, identity = _policy(request, factory)
    temporary_roots = platform_temp_roots()
    owner = {'engine_epoch': 'epoch-' + str(uuid4()), 'sandbox_session_id': 'sandbox-' + str(uuid4()),
        'execution_id': None}
    def process_policy(value):
        native = deepcopy(value)
        # DSH's file API permits platform temp roots. Windows native commands
        # instead receive a private temp capability owned by the runner.
        native['filesystem']['write_roots'] = [path for path in native['filesystem']['write_roots']
                                              if Path(path).is_relative_to(request.root)]
        return native
    async def open_process():
        return await factory.open(workspace, process_policy(policy), owner, identity)

    def open_for_mode(mode):
        # Retain the frozen workspace identity and limits; never mutate the standing policy.
        temporary = deepcopy(policy)
        temporary['policy_id'] = 'policy-' + str(uuid4())
        temporary['execution_mode'] = mode
        if mode == 'read-only':
            temporary['filesystem']['write_roots'] = []
        elif mode == 'workspace-write':
            temporary['filesystem']['write_roots'] = [str(path) for path in
                ((request.root, *temporary_roots) if request.write_roots is None else request.write_roots)]
        turn_id = 'turn-' + str(uuid4())
        call_control = request.data_root / 'native' / turn_id
        temporary = compile_policy(temporary, workspace, control_roots=(call_control, *factory.protected_roots),
                                   temporary_roots=temporary_roots,
                                   audit_workspace=mode != 'full-access').value
        client = FileWorkerClient(workspace, temporary, local_control=call_control)
        if mode == 'full-access':
            backend = FileToolBackend(client)
            backend.check_ready(temporary, required_mode='local-trusted')
            return backend
        async def prepare():
            return await NativeBackendFactory(request.data_root, mode=mode).open(workspace, process_policy(temporary),
                {**owner, 'sandbox_session_id': 'sandbox-' + str(uuid4())}, turn_id)
        return PolicyToolBackend(client, prepare)

    backend = PolicyToolBackend(FileWorkerClient(workspace, policy, local_control=control), open_process,
                                mode_factory=open_for_mode)
    return ExecutionSetup(backend, policy)
