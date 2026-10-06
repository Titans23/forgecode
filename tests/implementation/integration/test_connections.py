"""Bound credentials use actual metadata/SQLite and never follow endpoint changes."""
import json
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager

import pytest

from forge.application.connections import ConnectionService
from forge.application.models import ContractError
from forge.engine.methods import EngineMethods
from forge.engine.persistence import new_id
from test_application import setup


@contextmanager
def endpoint_server(*, redirect=None, hold=None):
    observed = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append({'path': self.path, 'headers': dict(self.headers)})
            if hold: hold.wait(10)
            self.send_response(302 if redirect else 200)
            if redirect: self.send_header('Location', redirect)
            self.send_header('Content-Length', '2')
            self.end_headers()
            try: self.wfile.write(b'{}')
            except (BrokenPipeError, ConnectionResetError): pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            self.do_GET()
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try: yield 'http://127.0.0.1:' + str(server.server_port), observed
    finally:
        if hold: hold.set()
        server.shutdown(); server.server_close(); thread.join(2)


def select_endpoint(connections, connection_id, url):
    request = {**change(connection_id), 'base_url': url}
    grant = connections.prepare_set({key: value for key, value in request.items() if key != 'client_action_id'})
    connections.set({**request, 'confirmation_token': grant['confirmation_token']})
    connections.inject({'client_action_id': new_id('act'), 'connection_id': connection_id, 'expected_revision': 2,
        'credential': 'synthetic-f15-http-key'})


def test_unconfirmed_connection_makes_no_request_and_confirmed_retry_sends_once(tmp_path):
    service, store, _, _, params, _ = setup(tmp_path)
    connections = EngineMethods(service, profile='desktop').connections
    try:
        with endpoint_server() as (url, requests):
            select_endpoint(connections, params['connection_id'], url)
            test = {'client_action_id': new_id('act'), 'connection_id': params['connection_id'], 'expected_revision': 2,
                'confirmation_token': 'forged' + 'a' * 40}
            with pytest.raises(ContractError): asyncio.run(connections.test(test))
            assert requests == []
            grant = connections.prepare_test({key: test[key] for key in ('connection_id', 'expected_revision')})
            test['confirmation_token'] = grant['confirmation_token']
            assert asyncio.run(connections.test(test))['status'] == 'pass'
            assert asyncio.run(connections.test(test))['reused_existing_action']
            query = EngineMethods(service, profile='desktop').get_action({'method': 'connection.test', 'client_action_id': test['client_action_id']})
            assert query['state'] == 'completed' and query['result']['status'] == 'pass'
            assert len(requests) == 1 and requests[0]['path'] == '/v1/models'
            assert requests[0]['headers']['x-api-key'] == 'synthetic-f15-http-key'
            assert all('synthetic-f15-http-key' not in row[0] for row in store.connection.execute('SELECT result_json FROM actions'))
    finally: store.close()


def test_redirect_does_not_send_old_credential_to_another_origin(tmp_path):
    service, store, _, _, params, _ = setup(tmp_path)
    connections = EngineMethods(service, profile='desktop').connections
    try:
        with endpoint_server() as (other, escaped), endpoint_server(redirect=other + '/stolen') as (url, requests):
            select_endpoint(connections, params['connection_id'], url)
            grant = connections.prepare_test({'connection_id': params['connection_id'], 'expected_revision': 2})
            result = asyncio.run(connections.test({'client_action_id': new_id('act'), 'connection_id': params['connection_id'],
                'expected_revision': 2, 'confirmation_token': grant['confirmation_token']}))
            assert result['status'] == 'fail' and len(requests) == 1 and escaped == []
    finally: store.close()


def test_clear_credential_blocks_dispatch_and_is_idempotent(tmp_path):
    service, store, _, clients, params, turn = setup(tmp_path)
    connections = EngineMethods(service, profile='desktop').connections
    try:
        accepted = service.start_turn(turn)
        clear = {'client_action_id': new_id('act'), 'connection_id': params['connection_id'], 'expected_revision': 1}
        assert connections.clear(clear)['cleared']
        assert connections.clear(clear)['reused_existing_action']
        asyncio.run(service.execute_turn(accepted['turn_id']))
        assert not clients and service.get_snapshot(turn['session_id'])['turns'][0]['outcome'] == 'blocked'
        with pytest.raises(ContractError) as error:
            service.start_turn({**turn, 'client_action_id': new_id('act')})
        assert error.value.kind == 'CONNECTION_UNAVAILABLE'
    finally: store.close()


def test_revocation_cancels_actual_running_model_and_stops_further_calls(tmp_path):
    from test_application import ScriptedClient
    from forge.runtime.state import ModelTextDelta
    started, cancelled = asyncio.Event(), []
    class WaitingClient(ScriptedClient):
        async def stream(self, *args, **kwargs):
            started.set()
            try: await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.append(True); raise
            yield ModelTextDelta('unreachable')
    service, store, _, _, params, turn = setup(tmp_path, factory=lambda *a, **k: WaitingClient([]))
    connections = EngineMethods(service, profile='desktop').connections
    async def run():
        accepted = service.start_turn(turn)
        task = asyncio.create_task(service.execute_turn(accepted['turn_id']))
        await asyncio.wait_for(started.wait(), 5)
        connections.clear({'client_action_id': new_id('act'), 'connection_id': params['connection_id'], 'expected_revision': 1})
        await asyncio.wait_for(task, 5)
        assert cancelled and service.get_snapshot(turn['session_id'])['turns'][0]['outcome'] == 'cancelled'
    try: asyncio.run(run())
    finally: store.close()


def test_retried_injection_after_restart_does_not_claim_missing_memory_key(tmp_path):
    from forge.application.services import ApplicationServices
    from forge.engine.persistence import Store
    from test_application import Vault
    service, store, _, _, params, _ = setup(tmp_path)
    connections = EngineMethods(service, profile='desktop').connections
    inject = {'client_action_id': new_id('act'), 'connection_id': params['connection_id'], 'expected_revision': 1,
        'credential': 'synthetic-f15-restart-key'}
    assert connections.inject(inject)['installed']
    store.close()
    with Store(tmp_path / 'data') as reopened:
        fresh = ApplicationServices(reopened, profile_id='test-profile', credentials=Vault())
        retried = EngineMethods(fresh, profile='desktop').connections.inject(inject)
        assert retried == {'installed': False, 'reused_existing_action': True}


@pytest.mark.parametrize('operation', ['revoke', 'eof'])
def test_actual_rpc_reader_accepts_revocation_and_eof_during_slow_connection_test(tmp_path, operation):
    from test_rpc import seed, launch, send, receive, initialize
    fixture, params, _ = seed(tmp_path)
    hold = threading.Event()
    with endpoint_server(hold=hold) as (url, requests):
        async def run():
            process = await launch(tmp_path, fixture)
            async def call(method, payload):
                await send(process, method, payload)
                reply = await receive(process, wanted_id='r')
                assert 'result' in reply, reply
                return reply['result']
            try:
                await initialize(process)
                desired = {key: value for key, value in {**change(params['connection_id']), 'base_url': url}.items() if key != 'client_action_id'}
                grant = await call('connection.prepare_set', desired)
                await call('connection.set', {**desired, 'client_action_id': new_id('act'), 'confirmation_token': grant['confirmation_token']})
                await call('credentials.inject', {'client_action_id': new_id('act'), 'connection_id': params['connection_id'],
                    'expected_revision': 2, 'credential': 'synthetic-f15-rpc-key'})
                grant = await call('connection.prepare_test', {'connection_id': params['connection_id'], 'expected_revision': 2})
                await send(process, 'connection.test', {'client_action_id': new_id('act'), 'connection_id': params['connection_id'],
                    'expected_revision': 2, 'confirmation_token': grant['confirmation_token']}, request_id='slow')
                async with asyncio.timeout(5):
                    while not requests: await asyncio.sleep(.01)
                if operation == 'eof':
                    process.stdin.close()
                    assert await asyncio.wait_for(process.wait(), 3) == 0
                else:
                    await send(process, 'credentials.clear', {'client_action_id': new_id('act'), 'connection_id': params['connection_id'],
                        'expected_revision': 2}, request_id='clear')
                    replies = {}
                    async with asyncio.timeout(3):
                        while len(replies) < 2:
                            reply = await receive(process)
                            if reply.get('id') in ('slow', 'clear'): replies[reply['id']] = reply
                    assert replies['clear']['result']['cleared'] and replies['slow']['result']['status'] == 'blocked'
            finally:
                hold.set()
                if process.returncode is None:
                    process.stdin.close()
                    await asyncio.wait_for(process.wait(), 5)
            assert len(requests) == 1
        asyncio.run(run())


@pytest.mark.parametrize('provider', ['anthropic', 'openai_responses', 'deepseek'])
def test_real_provider_sdk_does_not_follow_cross_origin_redirect(provider):
    from forge.config import ForgeConfig
    from forge.runtime.model_client import AnthropicModelClient, ModelCallError
    from forge.runtime.providers import NativeModelClient
    with endpoint_server() as (other, escaped), endpoint_server(redirect=other + '/stolen') as (url, requests):
        config = ForgeConfig(api_key='synthetic-f15-sdk-key', model_id='test-model', provider=provider, base_url=url, request_timeout_seconds=10)
        async def run():
            adapter = AnthropicModelClient(config.model_id, max_retries=0, config=config) if provider == 'anthropic' else NativeModelClient(config, max_attempts=1)
            try:
                with pytest.raises(ModelCallError):
                    async for _ in adapter.stream([{'role': 'user', 'content': 'Local redirect acceptance probe'}]): pass
            finally: await adapter.aclose()
        asyncio.run(run())
        assert len(requests) == 1 and escaped == []


def test_injected_key_is_absent_from_real_child_environment_journal_and_database(tmp_path):
    import sys
    from forge.permissions.policy import ApprovalResponse
    from forge.runtime.state import ModelUsageUpdate, ModelToolCallCompleted, TokenUsage, ToolCall
    from test_application import ScriptedClient
    key = 'synthetic-f15-child-secret'
    events = [[ModelUsageUpdate(TokenUsage(10, 2)), ModelToolCallCompleted(ToolCall(0, 'environment', 'run_command', {
        'command': '"' + sys.executable + '" -c "import os,json; print(json.dumps(dict(found=any(v.startswith(\'synthetic-f15-\') for v in os.environ.values()))))"'}))],
        [ModelUsageUpdate(TokenUsage(11, 2)), ModelToolCallCompleted(ToolCall(0, 'finish', 'finish_task', {
            'task_kind': 'answer', 'status': 'completed', 'summary': 'Environment was observed.'}))]]
    async def approve(request): return ApprovalResponse('allow_once', 'Explicit local test')
    service, store, _, _, params, turn = setup(tmp_path, factory=lambda *a, **k: ScriptedClient(events), approval=approve)
    connections = EngineMethods(service, profile='desktop').connections
    try:
        connections.inject({'client_action_id': new_id('act'), 'connection_id': params['connection_id'], 'expected_revision': 1, 'credential': key})
        accepted = service.start_turn(turn)
        asyncio.run(service.execute_turn(accepted['turn_id']))
        snapshot = service.get_snapshot(turn['session_id'])
        assert snapshot['turns'][0]['outcome'] == 'completed'
        assert key not in json.dumps(snapshot)
        journals = list((tmp_path / 'data/harness').rglob('*.jsonl'))
        assert journals
        records = [json.loads(line) for path in journals for line in path.read_text(encoding='utf-8').splitlines()]
        assert any(row['type'] == 'tool_completed' and row['payload']['success'] for row in records)
        output = next(row['payload']['message']['content'][0]['content'] for row in records if row['type'] == 'tool_result_message')
        assert json.loads(output)['success'] and '"found": false' in json.loads(output)['content']
    finally: store.close()
    for path in (tmp_path / 'data').rglob('*'):
        if path.is_file(): assert key.encode() not in path.read_bytes()


def change(connection_id, revision=1):
    return {'client_action_id': new_id('act'), 'connection_id': connection_id, 'expected_revision': revision,
        'provider': 'anthropic', 'base_url': 'https://other.example.invalid/v1', 'requested_model': 'scripted-test'}


def test_changed_endpoint_revokes_old_key_and_requires_exact_new_confirmation(tmp_path):
    service, store, vault, _, params, _ = setup(tmp_path)
    methods = EngineMethods(service, profile='desktop')
    connections = ConnectionService(service, methods.confirmations)
    connection_id = params['connection_id']
    request = change(connection_id)
    try:
        assert service.credentials.resolve(connection_id) == vault.keys[connection_id]
        with pytest.raises(ContractError): connections.set({**request, 'confirmation_token': 'forged' + 'a' * 40})
        grant = connections.prepare_set({key: value for key, value in request.items() if key != 'client_action_id'})
        with pytest.raises(ContractError): connections.set({**request, 'base_url': 'https://third.example.invalid', 'confirmation_token': grant['confirmation_token']})
        result = connections.set({**request, 'confirmation_token': grant['confirmation_token']})
        assert result['revision'] == 2 and result['credential_present'] is False
        assert service.credentials.resolve(connection_id) is None
        inject = {'client_action_id': new_id('act'), 'connection_id': connection_id, 'expected_revision': 2, 'credential': 'synthetic-f15-private-key'}
        assert connections.inject(inject)['installed']
        assert connections.inject(inject)['reused_existing_action']
        assert service.credentials.resolve(connection_id) == inject['credential']
        assert b'synthetic-f15-private-key' not in store.db_path.read_bytes()
        assert all(inject['credential'] not in row[0] for row in store.connection.execute('SELECT configuration_json FROM connections'))
        assert all(inject['credential'] not in row[0] for row in store.connection.execute('SELECT result_json FROM actions'))
        assert connections.delete({'client_action_id': new_id('act'), 'connection_id': connection_id, 'expected_revision': 2})['deleted']
        assert service.credentials.resolve(connection_id) is None
    finally:
        store.close()


@pytest.mark.parametrize('url', ['https://user:secret@example.invalid', 'https://example.invalid?key=secret',
    'https://example.invalid/#key', 'http://example.invalid', 'file:///tmp/model', 'https://example.invalid\\@other.invalid', 'https://example.invalid/\npath'])
def test_unsafe_connection_metadata_is_rejected_before_any_request(tmp_path, url):
    service, store, _, _, params, _ = setup(tmp_path)
    methods = EngineMethods(service, profile='desktop')
    connections = ConnectionService(service, methods.confirmations)
    try:
        with pytest.raises(ContractError):
            connections.prepare_set({key: value for key, value in {**change(params['connection_id']), 'base_url': url}.items() if key != 'client_action_id'})
    finally:
        store.close()
