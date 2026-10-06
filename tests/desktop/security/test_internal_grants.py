"""Actual private stdio rejects Renderer control grants and unknown business methods."""
import asyncio
import json
from pathlib import Path
import sys
from forge.application.models import validate
from forge.engine.methods import manifest_hash
from forge.engine.persistence import new_id


def test_renderer_private_channel_cannot_forge_main_privileges(tmp_path):
    async def run():
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'forge.engine', '--data-dir', str(tmp_path / 'data'),
            '--principal', 'renderer', '--profile', 'desktop', cwd=Path(__file__).resolve().parents[3],
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async def call(method, params):
            process.stdin.write((json.dumps({'jsonrpc': '2.0', 'id': 'r', 'method': method, 'params': params}) + '\n').encode())
            await process.stdin.drain()
            response = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
            validate('rpc-response', response)
            return response
        try:
            assert 'result' in await call('system.initialize', {'protocol': {'major': 1, 'minor': 0}, 'client_build': 'security-test',
                'expected_manifest_hash': manifest_hash(), 'profile': 'desktop'})
            for method, params in [
                ('workspace.register', {'client_action_id': new_id('act'), 'path': str(tmp_path), 'selection_nonce': 'a' * 64}),
                ('workspace.prepare_authorization', {'workspace_id': new_id('ws'), 'expected_revision': 0}),
                ('approval.prepare_decision', {'approval_id': new_id('approval'), 'binding_hash': 'a' * 64}),
                ('approval.decide', {'client_action_id': new_id('act'), 'approval_id': new_id('approval'), 'binding_hash': 'a' * 64,
                    'confirmation_token': 'a' * 64, 'decision': 'approve'}),
                ('connection.prepare_set', {'connection_id': new_id('conn'), 'expected_revision': 0, 'provider': 'anthropic',
                    'base_url': 'https://example.invalid', 'requested_model': 'synthetic'}),
                ('connection.prepare_test', {'connection_id': new_id('conn'), 'expected_revision': 1}),
                ('credentials.inject', {'client_action_id': new_id('act'), 'connection_id': new_id('conn'), 'expected_revision': 1, 'credential': 'synthetic'}),
                ('credentials.clear', {'client_action_id': new_id('act'), 'connection_id': new_id('conn'), 'expected_revision': 1}),
            ]:
                response = await call(method, params)
                assert response['error']['data']['kind'] == 'UNAUTHORIZED'
            assert (await call('renderer.exec', {'command': 'whoami', 'role': 'main'}))['error']['code'] == -32601
        finally:
            if process.returncode is None: process.kill()
            await process.wait()
    asyncio.run(run())
