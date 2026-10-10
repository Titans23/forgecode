"""Create an actual owned Bridge/file boundary per accepted turn, before any model request."""
import asyncio
from pathlib import Path
from forge.application.models import ContractError,canonical_hash,validate
from forge.sandbox.file_client import FileWorkerClient
from forge.sandbox.srt_backend import SrtBackend
from forge.sandbox.tool_backend import FileToolBackend


class NativeBackendFactory:
    mode='strict'

    def __init__(self,data_dir,*,mode='strict'):
        if mode not in ('strict', 'workspace-write'): raise ValueError('Invalid native mode')
        self.data_dir=Path(data_dir).resolve()
        self.mode=mode

    @property
    def protected_roots(self):
        return (self.data_dir,)

    def check_request(self,policy,*,required_mode):
        validate('sandbox-policy',policy)
        if required_mode!=self.mode:
            raise ContractError('Native factory requires strict execution',kind='SANDBOX_UNAVAILABLE',code=-32010)
        if self.mode=='workspace-write':
            from forge.sandbox.workspace_backend import require_workspace_policy
            require_workspace_policy(policy)
            return
        if policy['filesystem']['read_mode']=='host_default' or policy['network']['mode']=='inherit':
            raise ContractError('Strict execution cannot accept a lightweight policy',kind='CAPABILITY_UNSATISFIED',code=-32010)
        if policy['filesystem']['read_mode']=='strict_allowlist_required' or any(
            policy['limits'][name] and policy['limits'][name]['enforcement']=='hard_required'
            for name in ('memory_bytes','disk_bytes','pids')):
            raise ContractError('Locked native backend cannot enforce the stronger request',kind='CAPABILITY_UNSATISFIED',code=-32010)

    async def open(self,workspace,policy,owner,turn_id):
        from forge.sandbox.workspace_backend import WorkspaceWriteBackend
        backend_type=WorkspaceWriteBackend if self.mode=='workspace-write' else SrtBackend
        native=backend_type(workspace,owner,self.data_dir/'native'/turn_id)
        client=FileWorkerClient(workspace,policy,native=native)
        try:
            await native.prepare(policy)  # Real prerequisite/boundary proof, never invented verified capabilities.
            if native._prepared.sha256!=canonical_hash(policy):
                raise ContractError('Prepared policy differs from accepted snapshot',kind='STALE_REVISION',code=-32010)
            backend=FileToolBackend(client)
            backend.check_ready(policy,required_mode=self.mode)
            return backend
        except BaseException as error:
            try:cleanup=await asyncio.wait_for(client.close(),8)
            except BaseException as cleanup_error:cleanup={'state':'unknown','reason':type(cleanup_error).__name__}
            error.cleanup_report=cleanup
            raise
