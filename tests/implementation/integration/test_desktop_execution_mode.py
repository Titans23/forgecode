"""Desktop mode selection and real private Engine admission; zero public model calls."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_rpc import initialize, receive, send, stop
from forge.engine.persistence import new_id
from forge.engine.persistence import Store
from forge.engine.methods import manifest_hash
from forge.testing.demo import seed_demo
from forge.testing.rpc_client import RpcClient
from forge.sessions.store import SessionStore

ROOT = Path(__file__).resolve().parents[3]


def test_desktop_mode_consent_persistence_and_shutdown_boundary():
    result = subprocess.run(['node', '--test', 'tests/implementation/node/execution-mode.test.mjs'], cwd=ROOT,
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('mode', ['strict', 'local-trusted'])
def test_real_desktop_engine_reports_the_explicit_mode_without_native_claim(tmp_path, mode):
    async def run():
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'forge.engine', '--data-dir', str(tmp_path / 'data'),
            '--profile', 'desktop', '--principal', 'main', '--main-owner-pid', str(os.getpid()), '--execution-mode', mode,
            cwd=ROOT, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            hello = (await initialize(process, profile='desktop'))['result']
            assert hello['readiness']['status'] == ('blocked' if mode == 'strict' else 'degraded')
            assert hello['capabilities']['sandbox'] == 'unavailable'
            assert 'provider-model' in hello['capabilities']['features']
            assert 'scripted-model' not in hello['capabilities']['features']
            if mode == 'local-trusted':
                assert 'local-trusted has no OS sandbox isolation' in hello['readiness']['reasons']
            await send(process, 'system.shutdown', {'client_action_id': new_id('act'), 'mode': 'drain'}, request_id='shutdown')
            assert (await receive(process, wanted_id='shutdown'))['result']['state'] == 'draining'
            assert await asyncio.wait_for(process.wait(), 15) == 0
        finally:
            await stop(process)
    asyncio.run(run())


@pytest.mark.parametrize('profile,principal,owner', [('evaluation', 'main', True), ('desktop', 'renderer', True), ('desktop', 'main', False)])
def test_local_desktop_mode_cannot_replace_evaluation_or_renderer_admission(tmp_path, profile, principal, owner):
    args = [sys.executable, '-m', 'forge.engine', '--data-dir', str(tmp_path / 'data'), '--profile', profile,
            '--principal', principal, '--execution-mode', 'local-trusted']
    if owner:
        args += ['--main-owner-pid', str(os.getpid())]
    result = subprocess.run(args, cwd=ROOT, input=b'', capture_output=True, timeout=20)
    assert result.returncode == 3
    assert json.loads(result.stderr)['status'] == 'invalid_configuration'
    assert not (tmp_path / 'data').exists()


def exercise_local_desktop(directory, engine_command, *, turn_timeout=90):
    """Real provider SDK/desktop RPC/Harness/tools; only model responses use loopback fixtures."""
    params, fixture = seed_demo(directory)
    responses = json.loads(fixture.read_text())['responses']
    scratch = directory / 'project' / 'scratch.txt'
    scratch.write_text('Disposable approval fixture', encoding='utf-8')
    patch = responses[2]['tool_calls'][0]['arguments']
    patch['patch'] = patch['patch'].replace('*** End Patch', '*** Delete File: scratch.txt\n*** End Patch')
    original_tests = (directory / 'project' / 'test_calculator.py').read_bytes()
    requests = []
    tool_requests = []

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            request = requests[-1]
            if request.get('tools'):
                tool_requests.append(request)
                response = responses[len(tool_requests) - 1]
            else:
                response = {'tool_calls': []}
            events = [{'type': 'message_start', 'message': {'id': 'local-' + str(len(requests)),
                'type': 'message', 'role': 'assistant', 'model': 'scripted-demo', 'content': [],
                'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 12, 'output_tokens': 0}}}]
            if not request.get('tools'):
                route = {'intent': 'change_task', 'task_relation': 'new', 'requires_workspace_change': True,
                    'requires_verification': True, 'allows_delete_only': False, 'confidence': 1.0, 'reason': 'Explicit repair request'}
                events.extend([{'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
                    {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': json.dumps(route)}},
                    {'type': 'content_block_stop', 'index': 0}])
            for index, call in enumerate(response['tool_calls']):
                events.extend([
                    {'type': 'content_block_start', 'index': index, 'content_block':
                        {'type': 'tool_use', 'id': call['id'], 'name': call['name'], 'input': {}}},
                    {'type': 'content_block_delta', 'index': index, 'delta':
                        {'type': 'input_json_delta', 'partial_json': json.dumps(call['arguments'])}},
                    {'type': 'content_block_stop', 'index': index}])
            events.extend([{'type': 'message_delta', 'delta': {'stop_reason': 'tool_use' if response['tool_calls'] else 'end_turn', 'stop_sequence': None},
                'usage': {'output_tokens': 4}}, {'type': 'message_stop'}])
            body = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def run():
        process = await asyncio.create_subprocess_exec(*engine_command, '--data-dir', str(directory / 'data'),
            '--profile', 'desktop', '--principal', 'main', '--main-owner-pid', str(os.getpid()),
            '--execution-mode', 'local-trusted', cwd=ROOT, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1048577)
        client = RpcClient(process)
        approvals = []
        try:
            hello = await client.call('system.initialize', {'protocol': {'major': 1, 'minor': 0},
                'client_build': 'desktop-local-integration', 'expected_manifest_hash': manifest_hash(), 'profile': 'desktop'})
            assert hello['readiness']['status'] == 'degraded'
            assert hello['capabilities']['sandbox'] == 'unavailable'
            assert 'scripted-model' not in hello['capabilities']['features']
            desired = {'connection_id': params['connection_id'], 'expected_revision': 1, 'provider': 'anthropic',
                'base_url': f'http://127.0.0.1:{server.server_port}', 'requested_model': 'scripted-demo'}
            grant = await client.call('connection.prepare_set', desired)
            await client.call('connection.set', {**desired, 'client_action_id': new_id('act'),
                'confirmation_token': grant['confirmation_token']})
            await client.call('credentials.inject', {'connection_id': params['connection_id'], 'expected_revision': 2,
                'client_action_id': new_id('act'), 'credential': 'synthetic-loopback-only'})
            session = await client.call('session.create_default', {key: params[key] for key in (
                'client_action_id', 'workspace_id', 'expected_workspace_revision', 'connection_id')})
            accepted = await client.call('session.submit', {'session_id': session['session_id'],
                'client_action_id': new_id('act'), 'input': [{'type': 'text',
                    'text': 'Fix addition, preserve tests, verify before and after, delete the disposable scratch.txt.'}]})
            async with asyncio.timeout(turn_timeout):
                while True:
                    pending = await client.call('approval.list', {'scope': {'kind': 'turn', 'id': accepted['turn_id']}})
                    for approval in pending['items']:
                        if approval['state'] != 'pending':
                            continue
                        if approval['tool_name'] == 'apply_patch':
                            assert scratch.exists(), 'Destructive patch ran before Main approval'
                            assert 'left - right' in (directory / 'project' / 'calculator.py').read_text()
                        binding = {key: approval[key] for key in ('approval_id', 'binding_hash')}
                        grant = await client.call('approval.prepare_decision', binding)
                        await client.call('approval.decide', {**binding, 'confirmation_token': grant['confirmation_token'],
                            'decision': 'approve', 'client_action_id': new_id('act')})
                        approvals.append(approval['tool_name'])
                    snapshot = await client.call('session.get', {'session_id': session['session_id']})
                    if snapshot['turns'][0]['state'] == 'finished':
                        break
                    await asyncio.sleep(.02)
            turn = snapshot['turns'][0]
            assert turn['outcome'] == 'completed', turn
            assert 'apply_patch' in approvals
            await client.call('system.shutdown', {'client_action_id': new_id('act'), 'mode': 'drain'})
            assert await client.drain() == 0
            return session['session_id']
        finally:
            await stop(process)

    try:
        session_id = asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert len(requests) == 6 and len(tool_requests) == 5
    assert 'left + right' in (directory / 'project' / 'calculator.py').read_text()
    assert not scratch.exists()
    assert (directory / 'project' / 'test_calculator.py').read_bytes() == original_tests
    with Store(directory / 'data') as store:
        configuration = json.loads(store.connection.execute('SELECT cs.normalized_json FROM configuration_snapshots cs '
            'JOIN session_configurations sc ON sc.snapshot_id=cs.id WHERE sc.session_id=?', (session_id,)).fetchone()[0])
        assert configuration['mode'] == 'local-trusted' and configuration['model_source'] == 'provider'
        row = store.connection.execute('SELECT legacy_ref FROM sessions WHERE id=?', (session_id,)).fetchone()
        journal = SessionStore(directory / 'project', data_root=store.data_dir / 'harness').load(row['legacy_ref'])
        assert [item.success for item in journal.verification_history] == [False, True]
    return {'status': 'pass', 'mode': 'local-trusted', 'provider_requests': len(requests),
        'approval_checked': True, 'verification_before_after': [False, True], 'eligible_for_native_pass': False}


def test_desktop_local_provider_completes_real_repair_with_main_approval(tmp_path):
    exercise_local_desktop(tmp_path, [sys.executable, '-m', 'forge.engine'])


@pytest.mark.parametrize('previous,current', [('strict', 'local-trusted'), ('local-trusted', 'strict')])
def test_mode_change_preserves_old_policy_and_requires_new_session(tmp_path, previous, current):
    from forge.application.services import ApplicationServices
    from forge.application.session_views import create_default_session, submit
    from forge.application.models import ContractError
    from forge.engine.test_profile import MemoryCredentials
    params, _ = seed_demo(tmp_path)
    credentials = MemoryCredentials({params['connection_id']: 'synthetic-test-key'})
    with Store(tmp_path / 'data') as store:
        old = ApplicationServices(store, profile_id='desktop', mode=previous, credentials=credentials)
        request = {key: params[key] for key in ('client_action_id', 'workspace_id', 'expected_workspace_revision', 'connection_id')}
        session = create_default_session(old, request)
        configuration_id = session['configuration']['snapshot_id']
        new = ApplicationServices(store, profile_id='desktop', mode=current, credentials=credentials)
        with pytest.raises(ContractError) as caught:
            submit(new, {'session_id': session['session_id'], 'client_action_id': new_id('act'),
                'input': [{'type': 'text', 'text': 'Continue'}]})
        assert caught.value.kind == 'STALE_REVISION'
        assert store.connection.execute('SELECT COUNT(*) FROM turns').fetchone()[0] == 0
        def mode(snapshot):
            return json.loads(store.connection.execute('SELECT normalized_json FROM configuration_snapshots WHERE id=?',
                (snapshot,)).fetchone()[0])['mode']
        assert mode(configuration_id) == previous
        created = create_default_session(new, {**request, 'client_action_id': new_id('act')})
        assert mode(created['configuration']['snapshot_id']) == current
