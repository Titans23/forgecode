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
                child = await asyncio.create_subprocess_exec(*worker_argv(), cwd=str(ROOT),
                    env=bridge_environment(self.local_control), stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                try:
                    output, _ = await asyncio.wait_for(child.communicate(raw), 30)
                except BaseException:
                    if child.returncode is None:
                        child.kill()
                    await child.wait()
                    raise
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

    async def _native_request(self, execution_id, raw):
        native = self.native
        if native._prepared is None or native._prepared.sha256 != self.policy_hash:
            raise ContractError('File helper requires its prepared native policy', kind='SANDBOX_UNAVAILABLE', code=-32010)
        seconds = min(30, self.policy['limits']['wall_time_seconds'])
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
                await native.cancel(execution_id, 'File helper interrupted', cleanup_deadline)
            except Exception:
                pass
            raise
        finally:
            collector.cancel()
            await asyncio.gather(collector, return_exceptions=True)

    async def scan(self):
        return (await self.request('scan'))['result']

    async def snapshot(self, paths, *, recursive=False):
        return (await self.request('snapshot', paths=list(paths), recursive=recursive))['result']
