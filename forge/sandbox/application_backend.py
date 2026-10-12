"""Create an actual owned Bridge/file boundary per accepted turn, before any model request."""
import asyncio
from pathlib import Path
from forge.application.models import ContractError,canonical_hash,validate
from forge.sandbox.file_client import FileWorkerClient
from forge.sandbox.tool_backend import FileToolBackend


class NativeBackendFactory:
    mode='workspace-write'

    def __init__(self,data_dir,*,mode='workspace-write'):
        if mode not in ('strict', 'read-only', 'workspace-write'): raise ValueError('Invalid native mode')
        self.data_dir=Path(data_dir).resolve()
        self.mode=mode

    @property
    def protected_roots(self):
        return (self.data_dir,)

    def check_request(self,policy,*,required_mode):
        validate('sandbox-policy',policy)
        if required_mode!=self.mode:
            raise ContractError('Native factory mode differs from the requested sandbox',kind='SANDBOX_UNAVAILABLE',code=-32010)
        if self.mode == 'strict':
            raise ContractError('Strict isolation is unavailable in the terminal bundle',kind='SANDBOX_UNAVAILABLE',code=-32010)
        if self.mode in ('read-only','workspace-write'):
            from forge.sandbox.workspace_backend import require_workspace_policy
            require_workspace_policy(policy)
            if self.mode=='read-only' and policy['filesystem']['write_roots']:
                raise ContractError('Read-only cannot grant writable roots',kind='CAPABILITY_UNSATISFIED',code=-32010)
            return

    async def open(self,workspace,policy,owner,turn_id):
        self.check_request(policy,required_mode=self.mode)
        from forge.sandbox.workspace_backend import WorkspaceWriteBackend
        native=WorkspaceWriteBackend(workspace,owner,self.data_dir/'native'/turn_id,mode=self.mode)
        client=FileWorkerClient(workspace,policy,native=native)
        try:
            await native.prepare(policy)  # Real prerequisite/boundary proof, never invented verified capabilities.
            if native.policy_hash!=canonical_hash(policy):
                raise ContractError('Prepared policy differs from accepted snapshot',kind='STALE_REVISION',code=-32010)
            backend=FileToolBackend(client)
            backend.check_ready(policy,required_mode=self.mode)
            return backend
        except BaseException as error:
            try:cleanup=await asyncio.wait_for(client.close(),8)
            except BaseException as cleanup_error:cleanup={'state':'unknown','reason':type(cleanup_error).__name__}
            error.cleanup_report=cleanup
            raise
