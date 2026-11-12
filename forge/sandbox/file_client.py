"""Fixed helper invocation shared by native file tools and their observer."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

from forge.protocol import ContractError, MAX_FRAME_BYTES, canonical_hash, strict_loads, validate
from forge.sandbox.launcher import ROOT, bridge_environment


def worker_argv():
    return [sys.executable, 'file-worker'] if getattr(sys, 'frozen', False) else [sys.executable, '-I', '-B', '-m', 'forge.engine', 'file-worker']


class FileWorkerClient:
    def __init__(self, workspace, policy, *, native=None, local_control=None):
        if (native is None) == (local_control is None):
            raise ValueError('Choose a prepared native backend or trusted file helper')
        self.workspace = json.loads(json.dumps(workspace))
        self.policy = json.loads(json.dumps(policy))
        self.policy_hash = canonical_hash(self.policy)
        self.native = native
        self.local_control = Path(local_control) if local_control is not None else None
        self._requests = set()
        self._routes = {}
        self._retired = set()
        self._router = None
        self._router_error = None
        self._owners = set()
        self._cleanup_reports = []
        self._closing = None

    def _require_open(self):
        if self._closing is not None:
            raise ContractError('File helper session is closed', kind='SANDBOX_UNAVAILABLE', code=-32010)

    async def request(self, operation, **values):
        self._require_open()
        task = asyncio.create_task(self._request(operation, **values))
        self._requests.add(task)
        try:
            return await task
        finally:
            self._requests.discard(task)

    async def _request(self, operation, **values):
        self._require_open()
        request_id = 'exec-' + str(uuid4())
        request = {'schema_version': 'forge.file-worker.request.v1', 'request_id': request_id,
            'workspace': self.workspace, 'policy': self.policy, 'policy_hash': self.policy_hash,
            'operation': operation, **values}
        validate('file-worker.request', request)
        raw = json.dumps(request, ensure_ascii=False, separators=(',', ':')).encode()
        if len(raw) > 589824:
            raise ContractError('File helper request exceeds task stdin quota', kind='ARTIFACT_LIMIT', code=-32010)
        self._require_open()
        if self.native is not None:
            output = await self._native_request(request_id, raw)
        else:
            # Trusted file-policy operations or explicit full-access mode.
            # Never a native-failure fallback; makes no OS sandbox claim.
            from forge.sandbox.process_owner import LocalProcessOwner
            import os
            import shutil
            environment = bridge_environment(self.local_control)
            directories = [str(Path(sys.executable).parent)]
            for name in ('git', 'rg', 'node', 'pwsh'):
                executable = shutil.which(name)
                if executable:
                    directories.append(str(Path(executable).resolve().parent))
            environment['PATH'] = os.pathsep.join([*dict.fromkeys(directories), os.environ.get('PATH', environment['PATH'])])
            owner = await LocalProcessOwner.start(worker_argv(), cwd=str(ROOT),
                environment=environment, output=True)
            child = owner.process
            self._owners.add(owner)
            try:
                self._require_open()  # Close may have started while the owned process was launching.
                seconds = self._seconds(request)
                async with asyncio.timeout(seconds + 5):
                    child.stdin.write(raw)
                    await child.stdin.drain()
                    child.stdin.close()
                    chunks, size = [], 0
                    while chunk := await child.stdout.read(65536):
                        size += len(chunk)
                        if size > MAX_FRAME_BYTES:
                            raise ContractError('Helper output exceeds quota', kind='ARTIFACT_LIMIT', code=-32010)
                        chunks.append(chunk)
                    output = b''.join(chunks)
                    await child.wait()
            finally:
                self._cleanup_reports.append(await owner.close())
                self._owners.discard(owner)
            if child.returncode not in (0, 2):
                raise ContractError('File helper exited without an owned result', kind='INDETERMINATE', code=-32010)
        result = strict_loads(output)
        validate('file-worker.response', result)
        if result.get('schema_version') != 'forge.file-worker.response.v1' or result.get('request_id') != request_id or result.get('policy_hash') != self.policy_hash:
            raise ContractError('File helper response binding differs', kind='INDETERMINATE', code=-32010)
        if result.get('status') == 'error':
            raise ContractError(result['error']['message'], kind=result['error']['code'], code=-32010)
        if result.get('status') != 'ok' or 'result' not in result or not isinstance(result.get('observations'), dict):
            raise ContractError('Invalid file helper response', kind='INDETERMINATE', code=-32010)
        return result

    def _seconds(self, request):
        seconds = 30
        if request.get('name') in ('run_command', 'verify'):
            seconds = request['arguments'].get('timeout_seconds', 120) + 5
        limit = self.policy['limits']['wall_time_seconds']
        return min(seconds, limit) if limit is not None else seconds

    async def _native_request(self, execution_id, raw):
        native = self.native
        if native.policy_hash != self.policy_hash:
            raise ContractError('File helper requires its prepared native policy', kind='SANDBOX_UNAVAILABLE', code=-32010)
        seconds = self._seconds(strict_loads(raw))
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')
        command = {'mode': 'argv', 'argv': worker_argv(), 'cwd': self.workspace['canonical_path'],
            'environment': {}, 'deadline_utc': deadline, 'output_limit_bytes': min(MAX_FRAME_BYTES, self.policy['limits']['command_output_bytes']),
            'stdin_base64': base64.b64encode(raw).decode('ascii')}
        if self._router_error is not None:
            raise self._router_error
        queue = asyncio.Queue(maxsize=8)
        self._routes[execution_id] = queue
        if self._router is None:
            self._router = asyncio.create_task(self._route_outputs())
        chunks, finished, size, closed_streams = [], asyncio.Event(), 0, set()
        async def collect():
            nonlocal size
            while True:
                frame = await queue.get()
                if isinstance(frame, Exception):
                    raise frame
                if frame['stream'] == 'stdout':
                    chunk = base64.b64decode(frame['raw_base64'], validate=True)
                    size += len(chunk)
                    if size > MAX_FRAME_BYTES:
                        raise ContractError('File helper output exceeds quota', kind='ARTIFACT_LIMIT', code=-32010)
                    chunks.append(chunk)
                if frame.get('final'):
                    closed_streams.add(frame['stream'])
                    if len(closed_streams) == 2:
                        finished.set()
                        return
        collector = asyncio.create_task(collect())
        try:
            await native.execute(execution_id, command)
            async with asyncio.timeout(seconds + 5):
                while True:
                    if collector.done():
                        collector.result()
                    status = await native.status(execution_id)
                    if status['state'] in ('finished', 'indeterminate'):
                        if status.get('runner_failed'):
                            detail = str(status.get('error') or 'Confinement runner failed')
                            if 'SetNamedSecurityInfoW' in detail and 'grantWrite' in detail:
                                detail += '\nDSH workspace grants require directory ownership and WRITE_OWNER.'
                            raise ContractError('DSH sandbox unavailable; command not started. ' + detail,
                                                kind='SANDBOX_UNAVAILABLE')
                        if status['state'] != 'finished' or status['discarded_bytes'] or status['exit_code'] not in (0, 2):
                            raise ContractError('File helper execution is unconfirmed; do not replay', kind='INDETERMINATE', code=-32010)
                        await asyncio.wait_for(finished.wait(), 1)
                        collector.result()
                        return b''.join(chunks)
                    await asyncio.sleep(0.02)
        except BaseException:
            cleanup_deadline = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat().replace('+00:00', 'Z')
            try:
                report = await asyncio.wait_for(native.cancel(execution_id, 'Helper interrupted', cleanup_deadline), 3)
                self._cleanup_reports.append({'state': 'clean' if report.get('confirmed') is True and
                    report.get('cleanup', {}).get('state') == 'clean' else 'unknown', 'native_cancel': report})
            except Exception as error:
                self._cleanup_reports.append({'state': 'unknown', 'reason': type(error).__name__})
            raise
        finally:
            collector.cancel()
            await asyncio.gather(collector, return_exceptions=True)
            self._routes.pop(execution_id, None)
            self._retired.add(execution_id)
            while not queue.empty():
                queue.get_nowait()

    async def _route_outputs(self):
        try:
            while True:
                frame = await self.native.outputs.get()
                identity = frame.get('execution_id')
                queue = self._routes.get(identity)
                if queue is not None:
                    await queue.put(frame)
                elif identity not in self._retired:
                    raise ContractError('Sandbox output has an unregistered owner', kind='INDETERMINATE', code=-32010)
        except Exception as error:
            self._router_error = error
            for queue in tuple(self._routes.values()):
                await queue.put(error)


    async def scan(self):
        return (await self.request('scan'))['result']

    async def snapshot(self, paths, *, recursive=False, include_content=False):
        return (await self.request('snapshot', paths=list(paths), recursive=recursive,
                                   include_content=include_content))['result']

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        return json.loads(json.dumps(await asyncio.shield(self._closing)))

    async def _close(self):
        requests = tuple(self._requests)
        for task in requests:
            task.cancel()
        await asyncio.gather(*requests, return_exceptions=True)
        for owner in list(self._owners):
            self._cleanup_reports.append(await owner.close())
            self._owners.discard(owner)
        if self.native is not None:
            try:
                self._cleanup_reports.append(await self.native.close())
            finally:
                await self.native.aclose()
        if self._router is not None:
            self._router.cancel()
            await asyncio.gather(self._router, return_exceptions=True)
        states = {report['state'] for report in self._cleanup_reports}
        return {'state': 'unknown' if 'unknown' in states else 'residual' if 'residual' in states else 'clean',
                'scope': 'native-session' if self.native else 'local-trusted-process-tree',
                'reports': list(self._cleanup_reports)}
