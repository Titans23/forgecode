"""Built-in execution providers over the existing file and native backends."""
from pathlib import Path
from uuid import uuid4

from forge.execution import ExecutionRequest, ExecutionSetup
from forge.sandbox.file_client import FileWorkerClient
from forge.sandbox.policy import compile_policy
from forge.sandbox.tool_backend import FileToolBackend


def _policy(request, factory=None):
    root, data = request.root, request.data_root
    identity = 'turn-' + str(uuid4())
    control = data / 'native' / identity
    info = root.stat()
    workspace = {'id': 'ws-' + str(uuid4()), 'canonical_path': str(root),
        'file_identity': f'{info.st_dev}:{info.st_ino}'}
    writes = (() if request.mode == 'read-only' else (root,)) if request.write_roots is None else request.write_roots
    policy = {'schema_version': 'forge.sandbox.policy.v1', 'policy_id': 'policy-' + str(uuid4()),
        'workspace_id': workspace['id'], 'filesystem': {'read_mode': 'host_default',
            'read_roots': [str(root), *(str(Path(p).resolve(strict=True)) for p in request.read_roots)],
            'write_roots': [str(Path(p).resolve(strict=True)) for p in writes],
            'protected_paths': [str(data)], 'deny_overrides_allow': True, 'reject_unsafe_links': True},
        'network': {'mode': 'inherit', 'allowed_domains': [], 'dns_isolation_required': False},
        'limits': {'memory_bytes': None, 'disk_bytes': None, 'pids': None,
            'wall_time_seconds': request.wall_seconds, 'command_output_bytes': 1048576,
            'session_artifact_bytes': 16777216}, 'environment_keys': [], 'fallback': 'deny',
        'session_mutation': 'replace_session'}
    policy = compile_policy(policy, workspace, control_roots=(control,
        *(factory.protected_roots if factory is not None else ())),
        audit_workspace=request.mode != 'full-access').value
    return workspace, policy, control, identity


async def open_host_execution(request: ExecutionRequest) -> ExecutionSetup:
    workspace, policy, control, _ = _policy(request)
    return ExecutionSetup(FileToolBackend(FileWorkerClient(workspace, policy, local_control=control)), policy)


async def open_native_execution(request: ExecutionRequest) -> ExecutionSetup:
    from forge.sandbox.application_backend import NativeBackendFactory
    factory = NativeBackendFactory(request.data_root, mode=request.mode)
    workspace, policy, _, identity = _policy(request, factory)
    owner = {'engine_epoch': 'epoch-' + str(uuid4()), 'sandbox_session_id': 'sandbox-' + str(uuid4()),
        'execution_id': None}
    backend = await factory.open(workspace, policy, owner, identity)
    return ExecutionSetup(backend, policy)
