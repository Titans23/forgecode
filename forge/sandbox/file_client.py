"""Fixed helper invocation shared by native file tools and their observer."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

from forge.application.models import ContractError, MAX_FRAME_BYTES, canonical_hash, strict_loads, validate
from forge.sandbox.launcher import ROOT, bridge_environment


def worker_argv():
    return [sys.executable, 'file-worker'] if getattr(sys, 'frozen', False) else [sys.executable, '-I', '-B', '-m', 'forge.engine', 'file-worker']


class FileWorkerClient:
    def __init__(self, workspace, policy, *, native=None, local_control=None):
        if (native is None) == (local_control is None):
            raise ValueError('Choose a prepared native backend or explicit local-trusted helper')
        self.workspace = json.loads(json.dumps(workspace))
        self.policy = json.loads(json.dumps(policy))
        self.policy_hash = canonical_hash(self.policy)
        self.native = native
        self.local_control = Path(local_control) if local_control is not None else None
        self._lock = asyncio.Lock()
        self._owners = set()
        self._cleanup_reports = []

    async def request(self, operation, **values):
        request_id = 'exec-' + str(uuid4())
        request = {'schema_version': 'forge.file-worker.request.v1', 'request_id': request_id,
            'workspace': self.workspace, 'policy': self.policy, 'policy_hash': self.policy_hash,
            'operation': operation, **values}
        validate('file-worker.request', request)
        raw = json.dumps(request, ensure_ascii=False, separators=(',', ':')).encode()
        if len(raw) > 589824:
            raise ContractError('File helper request exceeds task stdin quota', kind='ARTIFACT_LIMIT', code=-32010)
        async with self._lock:
            if self.native is not None:
                output = await self._native_request(request_id, raw)
            else:
                # Explicit portable/local-trusted mode. It is never selected as
                # fallback after a native failure and makes no OS sandbox claim.
                from forge.sandbox.process_owner import LocalProcessOwner
                owner = await LocalProcessOwner.start(worker_argv(), cwd=str(ROOT),
                    environment=bridge_environment(self.local_control), output=True)
                child = owner.process
                self._owners.add(owner)
                try:
                    seconds = self._seconds(request)
                    async with asyncio.timeout(seconds + 5):
                        child.stdin.write(raw)
                        await child.stdin.drain()
                        child.stdin.close()
                        output = await child.stdout.read(MAX_FRAME_BYTES + 1)
                        if len(output) > MAX_FRAME_BYTES:
                            raise ContractError('Helper output exceeds quota', kind='ARTIFACT_LIMIT', code=-32010)
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
        return min(seconds, self.policy['limits']['wall_time_seconds'])

    async def _native_request(self, execution_id, raw):
        native = self.native
        if native._prepared is None or native._prepared.sha256 != self.policy_hash:
            raise ContractError('File helper requires its prepared native policy', kind='SANDBOX_UNAVAILABLE', code=-32010)
        seconds = self._seconds(strict_loads(raw))
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')
        command = {'mode': 'argv', 'argv': worker_argv(), 'cwd': self.workspace['canonical_path'],
            'environment': {}, 'deadline_utc': deadline, 'output_limit_bytes': min(MAX_FRAME_BYTES, self.policy['limits']['command_output_bytes']),
            'stdin_base64': base64.b64encode(raw).decode('ascii')}
        chunks, finished, size, closed_streams = [], asyncio.Event(), 0, set()
        async def collect():
            nonlocal size
            while True:
                frame = await native.outputs.get()
                if frame['execution_id'] != execution_id:
                    raise ContractError('Concurrent sandbox output has another owner', kind='INDETERMINATE', code=-32010)
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

    async def scan(self):
        return (await self.request('scan'))['result']

    async def snapshot(self, paths, *, recursive=False):
        return (await self.request('snapshot', paths=list(paths), recursive=recursive))['result']

    async def close(self):
        for owner in list(self._owners):
            self._cleanup_reports.append(await owner.close())
            self._owners.discard(owner)
        if self.native is not None:
            try:
                self._cleanup_reports.append(await self.native.close())
            finally:
                await self.native.aclose()
        states = {report['state'] for report in self._cleanup_reports}
        return {'state': 'unknown' if 'unknown' in states else 'residual' if 'residual' in states else 'clean',
            'scope': 'native-session' if self.native else 'local-trusted-process-tree',
            'reports': list(self._cleanup_reports)}
