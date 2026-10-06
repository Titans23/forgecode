"""One supervised, fixed-asset Bridge per sandbox session. No ordinary-spawn fallback."""
import asyncio
import base64
import json
from pathlib import Path
import sys

from forge.application.models import ContractError, canonical_hash, strict_loads, validate
from forge.sandbox.capabilities import CapabilityReport
from forge.sandbox.launcher import ROOT, bridge_environment, verify_runtime
from forge.sandbox.path_policy import inspect_path
from forge.sandbox.policy import compile_policy


class SrtBackend:
    mode = 'strict'

    def __init__(self, workspace: dict, owner: dict, control_root: Path, *, trusted_root=ROOT):
        validate('cleanup-report', {'owner': owner, 'state': 'unknown', 'remaining_processes': 0,
                                   'diagnostic_refs': [], 'completed_at_utc': None})
        if owner['execution_id'] is not None:
            raise ContractError('Bridge owner must identify a session')
        self.workspace = dict(workspace)
        self.owner = dict(owner)
        self.control_root = Path(control_root).absolute()
        self.trusted_root = Path(trusted_root)
        self.process = None
        self._reader = None
        self._stderr = None
        self._pending = {}
        self._counter = 0
        self._write_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._prepared = None
        self._cleanup = None
        self._failure = None
        self.outputs = asyncio.Queue(maxsize=8)
        self.discarded_transport_bytes = 0
        self._discarded_by_execution = {}
        self.stderr_bytes = 0
        self._worker_lease = None

    async def _start(self):
        async with self._start_lock:
            if self.process is not None:
                if self.process.returncode is not None:
                    raise ContractError('Bridge exited; execution outcomes require reconciliation', kind='SANDBOX_UNAVAILABLE', code=-32010)
                return
            runtime = verify_runtime(self.trusted_root)
            inspect_path(str(self.control_root), absolute=True).assert_current()
            self.control_root.mkdir(parents=True, exist_ok=False)
            try:
                self.process = await asyncio.create_subprocess_exec(str(runtime.node), str(runtime.entry),
                    '--control-root', str(self.control_root), '--owner', json.dumps(self.owner, separators=(',', ':')),
                    cwd=str(runtime.root), env=bridge_environment(self.control_root),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    limit=1048577)
            except OSError as error:
                raise ContractError('Trusted Bridge could not start', kind='SANDBOX_UNAVAILABLE', code=-32010) from error
            self._reader = asyncio.create_task(self._read())
            self._stderr = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self):
        while value := await self.process.stderr.read(65536):
            self.stderr_bytes += len(value)  # Diagnostic bytes cannot become policy or RPC facts.

    async def _read(self):
        try:
            while raw := await self.process.stdout.readline():
                response = strict_loads(raw)
                if response.get('protocol') != 'forge.bridge.v1' or response.get('jsonrpc') != '2.0':
                    raise ContractError('Bridge protocol mismatch')
                if response.get('method') == 'bridge.output' and set(response) == {'protocol', 'jsonrpc', 'method', 'params'}:
                    output = response['params']
                    validate('bridge-output', output)
                    if output['owner'] != {**self.owner, 'execution_id': output['execution_id']}:
                        raise ContractError('Output ownership mismatch')
                    size = len(base64.b64decode(output['raw_base64'], validate=True))
                    try:
                        self.outputs.put_nowait(output)
                    except asyncio.QueueFull:
                        self.discarded_transport_bytes += size
                        identity = output['execution_id']
                        self._discarded_by_execution[identity] = self._discarded_by_execution.get(identity, 0) + size
                    continue
                if set(response) not in ({'protocol', 'jsonrpc', 'id', 'result'}, {'protocol', 'jsonrpc', 'id', 'error'}):
                    raise ContractError('Invalid Bridge response envelope')
                future = self._pending.get(response.get('id'))
                if future is not None and not future.done():
                    future.set_result(response)
        except (ValueError, ContractError, asyncio.LimitOverrunError) as error:
            failure = ContractError('Bridge returned invalid control data', kind='SANDBOX_UNAVAILABLE', code=-32010)
            failure.__cause__ = error
        else:
            failure = ContractError('Bridge exited; do not repeat an uncertain execution', kind='SANDBOX_UNAVAILABLE', code=-32010)
        self._failure = failure
        for future in self._pending.values():
            if not future.done():
                future.set_exception(failure)

    async def _request(self, method, params):
        await self._start()
        if self._failure is not None:
            raise self._failure
        if len(self._pending) >= 32:
            raise ContractError('Bridge request capacity exhausted', kind='SANDBOX_UNAVAILABLE', code=-32010)
        self._counter += 1
        request_id = str(self._counter)
        request = {'protocol': 'forge.bridge.v1', 'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}
        raw = (json.dumps(request, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        if len(raw) > 1048576:
            raise ContractError('Bridge request exceeds 1 MiB')
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with self._write_lock:
                self.process.stdin.write(raw)
                await asyncio.wait_for(self.process.stdin.drain(), 10)
            response = await asyncio.wait_for(future, 30)
            if 'error' in response:
                error = response['error']
                if error['code'] == -32010:
                    validate('business-error', error)
                raise ContractError(error['message'], kind=error['data']['kind'], code=error['code'])
            return response['result']
        except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError) as error:
            raise ContractError('Bridge operation is unconfirmed; do not replay execution', kind='SANDBOX_UNAVAILABLE', code=-32010) from error
        finally:
            self._pending.pop(request_id, None)

    async def probe(self):
        result = await self._request('probe', {'workspace_id': self.workspace['id'], 'workspace_path': self.workspace['canonical_path']})
        return CapabilityReport(result)

    async def prepare(self, policy):
        worker_root = None
        if sys.platform == 'win32':
            from forge.sandbox.windows_worker import windows_worker_root
            worker_root = windows_worker_root()
        roots = (str(self.control_root),) + ((str(worker_root),) if worker_root else ())
        snapshot = compile_policy(policy, self.workspace, control_roots=roots)
        capabilities = await self.probe()
        capabilities.require(snapshot.value)
        snapshot.srt_config(capabilities)
        if worker_root and self._worker_lease is None:
            from forge.sandbox.windows_worker import WindowsWorkerLease
            self._worker_lease = WindowsWorkerLease(worker_root, self.owner, snapshot.sha256)
        result = await self._request('prepare', {'policy': snapshot.value, 'policy_hash': snapshot.sha256, 'owner': self.owner})
        validate('bridge.prepare.result', result)
        self._prepared = snapshot
        return result

    async def execute(self, execution_id, command):
        if self._prepared is None:
            raise ContractError('No executable prepared session', kind='SANDBOX_UNAVAILABLE', code=-32010)
        validate('command-spec', command)
        self._prepared.paths.authorize(command['cwd'], write=False).assert_current()
        result = await self._request('execute', {'sandbox_session_id': self.owner['sandbox_session_id'],
            'execution_id': execution_id, 'command': command, 'command_hash': canonical_hash(command)})
        validate('bridge.execute.result', result)
        return result

    async def status(self, execution_id):
        result = await self._request('status', {'execution_id': execution_id})
        validate('execution-status', result)
        result['discarded_bytes'] += self._discarded_by_execution.get(execution_id, 0)
        return result

    async def cancel(self, execution_id, reason, deadline_utc):
        result = await self._request('cancel', {'execution_id': execution_id, 'reason': reason, 'deadline_utc': deadline_utc})
        validate('bridge.cancel.result', result)
        return result

    async def close(self):
        if self._cleanup is None:
            self._cleanup = await self._request('close', {'sandbox_session_id': self.owner['sandbox_session_id']})
            validate('cleanup-report', self._cleanup)
            if self._worker_lease:
                self._worker_lease.close(self._cleanup)
        return json.loads(json.dumps(self._cleanup))

    async def aclose(self):
        if self.process is None:
            return
        try:
            if self.process.returncode is None:
                await self.close()
        finally:
            if self.process.stdin and not self.process.stdin.is_closing():
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()  # Exact owned Bridge handle; sandbox descendants still require reconciliation.
                await self.process.wait()
            await asyncio.gather(self._reader, self._stderr, return_exceptions=True)
            if self._worker_lease:
                self._worker_lease.close(self._cleanup)
