"""Private JSONL transport to the published DSH sandbox/subprocess services."""
import asyncio
import base64
import json
import os
import sys

from forge.protocol import ContractError
from forge.sandbox.launcher import bridge_environment
from forge.sandbox.process_owner import LocalProcessOwner


class DshSession:
    def __init__(self, runtime, workspace, mode, identity, write_roots, control_root):
        self.runtime, self.workspace, self.mode = runtime, workspace, mode
        self.identity, self.write_roots, self.control_root = identity, write_roots, control_root
        self._pending = {}
        self._callbacks = {}
        self._writer = asyncio.Lock()
        self._closing = None
        self.owner = None
        self.diagnostic = b''

    async def start(self):
        scratch = self.control_root / 'scratch'
        scratch.mkdir(exist_ok=False)
        if sys.platform == 'win32':
            from forge.sandbox.windows_worker import grant_controller_access
            grant_controller_access(scratch)
        environment = bridge_environment(scratch)
        environment['PATH'] = os.environ.get('PATH', environment['PATH'])
        entry = self.runtime.entry
        self.owner = await LocalProcessOwner.start([str(self.runtime.node), str(entry)],
            cwd=self.workspace['canonical_path'], environment=environment, output=True, capture_stderr=True)
        self._reading = asyncio.create_task(self._read())
        self._diagnostics = asyncio.create_task(self._stderr())
        ready = self._future('init')
        tools = {name: str(path) for name, path in self.runtime.tools.items()}
        tools.update(python=sys.executable, node=str(self.runtime.node))
        shells = ({'pwsh': tools['powershell']} if 'powershell' in tools else {}) if sys.platform == 'win32' else {
            'bash': '/bin/bash', 'sh': '/bin/sh'}
        await self._send({'op': 'init', 'id': 'init', 'workspace': self.workspace['canonical_path'],
            'mode': self.mode, 'sessionId': self.identity, 'writeRoots': self.write_roots,
            'shells': shells, 'tools': tools})
        await asyncio.wait_for(asyncio.shield(ready), 15)

    def _future(self, identity):
        future = asyncio.get_running_loop().create_future()
        self._pending[identity] = future
        return future

    async def _send(self, value):
        raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
        if len(raw) > 1048576:
            raise ContractError('Native request exceeds transport quota', kind='ARTIFACT_LIMIT')
        async with self._writer:
            self.owner.process.stdin.write(raw)
            await self.owner.process.stdin.drain()

    async def _stderr(self):
        while raw := await self.owner.process.stderr.read(8192):
            self.diagnostic = (self.diagnostic + raw)[-16384:]

    async def _read(self):
        try:
            while raw := await self.owner.process.stdout.readline():
                message = json.loads(raw)
                identity, kind = message.get('id'), message.get('type')
                if kind == 'fatal':
                    raise ValueError(message.get('error', 'DSH protocol failed'))
                if identity not in self._pending:
                    raise ValueError('Native response has another execution identity')
                if kind == 'output':
                    if message['stream'] not in ('stdout', 'stderr'):
                        raise ValueError('Invalid native output stream')
                    await self._callbacks[identity](message['stream'], base64.b64decode(message['data'], validate=True))
                elif kind in ('ready', 'done', 'closed') and not self._pending[identity].done():
                    self._pending[identity].set_result(message)
                else:
                    raise ValueError('Invalid or duplicate native response')
                await asyncio.sleep(0)
            if any(not value.done() for value in self._pending.values()):
                raise ValueError('DSH controller exited before reporting the operation')
        except (Exception, asyncio.CancelledError) as error:
            failure = ContractError(f'DSH controller result unavailable: {error}', kind='INDETERMINATE')
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(failure)

    async def execute(self, identity, command, output):
        if self._closing is not None:
            raise ContractError('DSH session is closing', kind='SANDBOX_UNAVAILABLE')
        if identity in self._pending:
            raise ContractError('Execution identity already used', kind='POLICY_DENIED')
        result = self._future(identity)
        self._callbacks[identity] = output
        await self._send({'op': 'execute', 'id': identity, 'command': command})
        return await asyncio.shield(result)

    async def cancel(self, identity):
        future = self._pending.get(identity)
        if future is None:
            return {'state': 'clean'}
        try:
            if not future.done():
                await self._send({'op': 'cancel', 'id': identity})
            result = await asyncio.wait_for(asyncio.shield(future), 5)
            return result['cleanup']
        except Exception as error:
            return {'state': 'unknown', 'reason': type(error).__name__}

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        return await asyncio.shield(self._closing)

    async def _close(self):
        if self.owner is None:
            return {'state': 'clean'}
        cleanup = {'state': 'unknown'}
        try:
            if self.owner.process.returncode is None:
                response = self._future('close')
                await self._send({'op': 'close', 'id': 'close'})
                cleanup = (await asyncio.wait_for(asyncio.shield(response), 10))['cleanup']
                self.owner.process.stdin.close()
                await asyncio.wait_for(self.owner.process.wait(), 3)
        except Exception as error:
            cleanup = {'state': 'unknown', 'reason': type(error).__name__}
        finally:
            process = await self.owner.close()
            for task in (self._reading, self._diagnostics):
                if not task.done():
                    task.cancel()
            await asyncio.gather(self._reading, self._diagnostics, return_exceptions=True)
        return {'state': 'clean' if cleanup.get('state') == process.get('state') == 'clean' else 'unknown',
                'scope': 'dsh-session-and-managed-ranges', 'provider': cleanup, 'controller': process}
