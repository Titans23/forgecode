"""Service tests use scripted models and real Harness, Journal, tools and SQLite."""
import asyncio
import builtins
import json
from pathlib import Path

import pytest

from forge.application.models import ContractError, ErrorKind
from forge.application.harness_adapter import LocalTrustedBackend
from forge.application.services import ApplicationServices
from forge.config import ForgeConfig
from forge.engine.persistence import Store, new_id
from forge.runtime.dependencies import RuntimeBindings
from forge.runtime.state import ModelTextDelta, ModelToolCallCompleted, ModelUsageUpdate, TokenUsage, ToolCall, TurnCompleted


class ScriptedClient:
    provider = 'anthropic'
    model = 'scripted-test'
    max_tokens = 1024

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    async def stream(self, messages, tools=None, system=None):
        self.calls.append({'messages': messages, 'tools': tools, 'system': system})
        assert self.responses, 'Unplanned model call'
        for event in self.responses.pop(0):
            yield event

    async def aclose(self):
        self.closed = True


class Vault:
    def __init__(self):
        self.keys = {}

    def resolve(self, connection_id):
        return self.keys.get(connection_id)


class Recorder:
    def __init__(self):
        self.events = []
        self.requests = []

    def record(self, event):
        self.events.append(event)

    def record_request(self, kind, attributes):
        self.requests.append((kind, attributes))


def script():
    return [
        [ModelUsageUpdate(TokenUsage(11, 3)), ModelToolCallCompleted(ToolCall(0, 'read', 'read_file', {'path': 'value.txt'}))],
        [ModelUsageUpdate(TokenUsage(12, 4)), ModelToolCallCompleted(ToolCall(0, 'finish', 'finish_task', {
            'task_kind': 'answer', 'status': 'completed', 'summary': 'The value is B.'}))],
    ]


def setup(tmp_path, *, backend=None, mode='local-trusted', factory=None, recorder=None, approval=None):
    root = tmp_path / 'project'
    root.mkdir(exist_ok=True)
    (root / 'value.txt').write_text('B', encoding='utf-8')
    vault = Vault()
    clients = []

    def create(config, *, max_tokens=None):
        client = ScriptedClient(script())
        clients.append(client)
        return client

    store = Store(tmp_path / 'data')
    service = ApplicationServices(store, profile_id='test-profile', credentials=vault,
        mode=mode, backend=backend or LocalTrustedBackend(), model_client_factory=factory or create,
        recorder=recorder, approval_handler=approval, task_relation='new')
    workspace = service.open_workspace(root)
    workspace = service.authorize_workspace(workspace['id'], expected_revision=0, allow=True)
    config = ForgeConfig(api_key='sensitive-do-not-persist', model_id='scripted-test', max_tokens=1024)
    connection_id = service.put_connection(config)
    vault.keys[connection_id] = config.api_key
    policy_id = new_id('policy')
    policy = {'schema_version': 'forge.sandbox.policy.v1', 'policy_id': policy_id, 'workspace_id': workspace['id'],
        'filesystem': {'read_mode': 'backend_default_with_protected_paths', 'read_roots': [str(root)],
            'write_roots': [str(root)], 'protected_paths': [], 'deny_overrides_allow': True, 'reject_unsafe_links': True},
        'network': {'mode': 'deny_direct', 'allowed_domains': [], 'dns_isolation_required': False},
        'limits': {'memory_bytes': None, 'disk_bytes': None, 'pids': None, 'wall_time_seconds': 30,
            'command_output_bytes': 1048576, 'session_artifact_bytes': 104857600},
        'environment_keys': [], 'fallback': 'deny', 'session_mutation': 'replace_session'}
    service.put_policy(policy)
    budget_id = service.put_budget({'max_model_calls': 4, 'max_tool_calls': 6, 'wall_seconds': 30})
    params = {'client_action_id': new_id('act'), 'workspace_id': workspace['id'], 'expected_workspace_revision': 1,
        'connection_id': connection_id, 'policy_id': policy_id, 'budget_profile_id': budget_id}
    session = service.create_session(params)
    turn = {key: value for key, value in params.items() if key != 'workspace_id'}
    turn.update(client_action_id=new_id('act'), session_id=session['session_id'], input=[{'type': 'text', 'text': 'Read the value.'}])
    return service, store, vault, clients, params, turn


def test_cli_and_service_use_same_tools_and_completion(tmp_path, monkeypatch, capsys):
    recorder = Recorder()
    service, store, vault, clients, params, turn = setup(tmp_path, recorder=recorder)
    monkeypatch.setattr(builtins, 'input', lambda *a: pytest.fail('Engine requested terminal input'))
    from forge.cli import create_session_runtime
    cli_client = ScriptedClient(script())
    bindings = RuntimeBindings(config=ForgeConfig(api_key='test', model_id='scripted-test', max_tokens=1024),
        model_client_factory=lambda config, **kwargs: cli_client, data_root=tmp_path / 'cli-data',
        trusted_extensions=False, task_relation='new', max_model_calls=4, max_tool_calls=6, wall_seconds=30)

    async def run():
        cli, _, _ = create_session_runtime(tmp_path / 'project', bindings=bindings)
        try:
            events = [e async for e in cli.stream('Read the value.')]
        finally:
            await cli.runtime_close()
        accepted = service.start_turn(turn)
        result = await service.execute_turn(accepted['turn_id'])
        return next(e.result for e in events if isinstance(e, TurnCompleted)), result

    try:
        cli_result, result = asyncio.run(run())
        assert [(c.name, c.arguments) for c in cli_result.tool_calls] == [(c.name, c.arguments) for c in result.tool_calls]
        assert (cli_result.status, cli_result.stop_reason) == (result.status, result.stop_reason)
        assert cli_client.calls[0]['tools'] == clients[0].calls[0]['tools']
        assert len(clients[0].calls) == 2 and clients[0].closed
        assert recorder.events and recorder.requests
        snapshot = service.get_snapshot(turn['session_id'])
        assert snapshot['turns'][0]['outcome'] == 'completed'
        assert snapshot['turns'][0]['native_outcome']['stop_reason'] == result.stop_reason
        assert 'sensitive-do-not-persist' not in store.db_path.read_bytes().decode('utf-8', errors='ignore')
        assert capsys.readouterr().out == ''
    finally:
        store.close()


@pytest.mark.parametrize('missing', ['authorization', 'connection', 'credential', 'revision', 'policy', 'budget'])
def test_missing_dependency_never_creates_model_or_accepts_turn(tmp_path, missing):
    service, store, vault, clients, params, turn = setup(tmp_path)
    try:
        if missing == 'authorization':
            service.authorize_workspace(params['workspace_id'], expected_revision=1, allow=False)
            turn['expected_workspace_revision'] = 2
        elif missing == 'connection':
            service.delete_connection(params['connection_id'])
        elif missing == 'credential':
            vault.keys.clear()
        elif missing == 'revision':
            turn['expected_workspace_revision'] = 0
        elif missing == 'policy':
            turn['policy_id'] = new_id('policy')
        else:
            turn['budget_profile_id'] = new_id('budget')
        with pytest.raises(ContractError) as error:
            service.start_turn(turn)
        assert error.value.kind in ErrorKind
        assert not clients
        assert not store.connection.execute('SELECT 1 FROM turns').fetchone()
    finally:
        store.close()


def test_strict_default_refuses_local_backend_without_model_calls(tmp_path):
    service, store, _, clients, _, turn = setup(tmp_path, mode='strict')
    try:
        with pytest.raises(ContractError, match='strict'):
            service.start_turn(turn)
        assert not clients
    finally:
        store.close()


def test_durable_retry_and_queued_cancel_have_no_model_calls(tmp_path):
    service, store, _, clients, _, turn = setup(tmp_path)
    try:
        accepted = service.start_turn(turn)
        assert service.start_turn(turn)['turn_id'] == accepted['turn_id']
        cancellation = {'turn_id': accepted['turn_id'], 'client_action_id': new_id('act'), 'reason': 'Stop before dispatch'}
        first = service.cancel_turn(cancellation)
        assert first['state'] == 'finished' and first['outcome'] == 'cancelled'
        assert service.cancel_turn(cancellation) == first
        with pytest.raises(ContractError):
            asyncio.run(service.execute_turn(accepted['turn_id']))
        assert not clients
    finally:
        store.close()


def test_connection_removed_after_acceptance_blocks_dispatch(tmp_path):
    service, store, _, clients, params, turn = setup(tmp_path)
    try:
        accepted = service.start_turn(turn)
        service.delete_connection(params['connection_id'])
        assert service.start_turn(turn)['turn_id'] == accepted['turn_id']
        asyncio.run(service.execute_turn(accepted['turn_id']))
        assert not clients
        assert service.get_snapshot(turn['session_id'])['turns'][0]['outcome'] == 'blocked'
    finally:
        store.close()


def test_running_model_cancel_propagates_and_preserves_journal(tmp_path):
    started = asyncio.Event()
    cancelled = []

    class WaitingClient(ScriptedClient):
        async def stream(self, *args, **kwargs):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            yield ModelTextDelta('unreachable')

    service, store, _, _, _, turn = setup(tmp_path, factory=lambda *a, **k: WaitingClient([]))

    async def run():
        accepted = service.start_turn(turn)
        task = asyncio.create_task(service.execute_turn(accepted['turn_id']))
        await asyncio.wait_for(started.wait(), 5)
        ack = service.cancel_turn({'turn_id': accepted['turn_id'], 'client_action_id': new_id('act'), 'reason': 'User cancelled'})
        assert ack['state'] == 'cancel_requested' and ack['outcome'] is None
        await asyncio.wait_for(task, 5)

    try:
        asyncio.run(run())
        assert cancelled
        snapshot = service.get_snapshot(turn['session_id'])
        assert snapshot['turns'][0]['outcome'] == 'cancelled'
        assert any(e['attributes'].get('native_type') == 'turn_cancelled' for e in store.events())
    finally:
        store.close()


def test_budget_is_enforced_by_original_runner(tmp_path):
    service, store, _, clients, params, turn = setup(tmp_path)
    try:
        turn['budget_profile_id'] = service.put_budget({'max_model_calls': 1, 'max_tool_calls': 6, 'wall_seconds': 30})
        accepted = service.start_turn(turn)
        result = asyncio.run(service.execute_turn(accepted['turn_id']))
        assert len(clients[0].calls) == 1
        assert result.stop_reason == 'model_budget_exhausted'
        assert service.get_snapshot(turn['session_id'])['turns'][0]['outcome'] == 'budget_exhausted'
    finally:
        store.close()


def test_session_resume_and_action_retry_survive_service_restart(tmp_path):
    service, store, vault, clients, params, turn = setup(tmp_path)
    accepted = service.start_turn(turn)
    asyncio.run(service.execute_turn(accepted['turn_id']))
    legacy = store.connection.execute('SELECT legacy_ref FROM sessions').fetchone()[0]
    store.close()
    with Store(tmp_path / 'data') as reopened:
        resumed = ApplicationServices(reopened, profile_id='test-profile', credentials=vault,
            mode='local-trusted', backend=LocalTrustedBackend(),
            model_client_factory=lambda *a, **k: ScriptedClient(script()), task_relation='new')
        assert resumed.start_turn(turn)['turn_id'] == accepted['turn_id']
        assert resumed.get_snapshot(turn['session_id'])['turns'][0]['outcome'] == 'completed'
        second = resumed.start_turn({**turn, 'client_action_id': new_id('act')})
        result = asyncio.run(resumed.execute_turn(second['turn_id']))
        assert result.status == 'completed'
        assert reopened.connection.execute('SELECT legacy_ref FROM sessions').fetchone()[0] == legacy
        assert len(resumed.get_snapshot(turn['session_id'])['turns']) == 2


def test_approval_and_backend_follow_durable_intent_and_ignore_project_grants(tmp_path):
    approved = []
    dispatched = []
    from forge.permissions.policy import ApprovalResponse

    async def approve(request):
        approved.append(request.tool_name)
        return ApprovalResponse('allow_once', 'Test explicit approval')

    class InspectBackend(LocalTrustedBackend):
        async def execute(self, call, registry):
            # The actual legacy intent is already fsynced when backend execution starts.
            if call.name == 'write_file':
                paths = list((tmp_path / 'data/harness').rglob('*.jsonl'))
                assert any('tool_started' in p.read_text(encoding='utf-8') for p in paths)
            dispatched.append(call.name)
            return await super().execute(call, registry)

    write_script = [[ModelUsageUpdate(TokenUsage(11, 3)), ModelToolCallCompleted(ToolCall(0, 'write', 'write_file', {'path': 'actual.txt', 'content': 'actual data'}))],
        [ModelUsageUpdate(TokenUsage(12, 4)), ModelToolCallCompleted(ToolCall(0, 'finish', 'finish_task', {'task_kind': 'change', 'status': 'failed',
            'summary': 'Written; this deterministic probe intentionally ends without verification.', 'blocked_reasons': ['Probe ends here.']}))]]
    service, store, _, _, _, turn = setup(tmp_path, backend=InspectBackend(), approval=approve,
        factory=lambda *a, **k: ScriptedClient(write_script))
    project_rules = tmp_path / 'project/.forge/permissions.json'
    project_rules.parent.mkdir()
    project_rules.write_text(json.dumps({'rules': [{'action': 'allow', 'capability': '*', 'target': '*'}]}))
    try:
        accepted = service.start_turn(turn)
        asyncio.run(service.execute_turn(accepted['turn_id']))
        assert (tmp_path / 'project/actual.txt').read_text() == 'actual data'
        assert approved == ['write_file']
        assert 'write_file' in dispatched
        assert service.get_snapshot(turn['session_id'])['turns'][0]['outcome'] == 'failed'
    finally:
        store.close()


def test_cli_and_service_share_workspace_execution_lock(tmp_path):
    started = asyncio.Event()

    class WaitingClient(ScriptedClient):
        async def stream(self, *args, **kwargs):
            started.set()
            await asyncio.Future()
            yield ModelTextDelta('unreachable')

    service, store, _, _, _, turn = setup(tmp_path, factory=lambda *a, **k: WaitingClient([]))
    from forge.cli import create_session_runtime
    cli_client = ScriptedClient(script())
    bindings = RuntimeBindings(config=ForgeConfig(api_key='test', model_id='scripted-test', max_tokens=1024),
        data_root=tmp_path / 'cli-data', model_client_factory=lambda *a, **k: cli_client,
        trusted_extensions=False, task_relation='new')

    async def run():
        accepted = service.start_turn(turn)
        task = asyncio.create_task(service.execute_turn(accepted['turn_id']))
        await asyncio.wait_for(started.wait(), 5)
        cli, _, _ = create_session_runtime(tmp_path / 'project', bindings=bindings)
        try:
            with pytest.raises((BlockingIOError, PermissionError, OSError)):
                [e async for e in cli.stream('Read the value.')]
            assert not cli_client.calls
            service.cancel_turn({'turn_id': accepted['turn_id'], 'client_action_id': new_id('act'), 'reason': 'Release workspace'})
            await task
            events = [e async for e in cli.stream('Read the value.')]
            assert any(isinstance(e, TurnCompleted) for e in events)
        finally:
            await cli.runtime_close()

    try:
        asyncio.run(run())
    finally:
        store.close()


def test_cancelled_process_does_not_replan_or_claim_confirmed_cleanup(tmp_path, monkeypatch):
    import shlex
    import subprocess
    import sys
    from forge.permissions.policy import ApprovalResponse
    started = asyncio.Event()
    processes = []
    real_spawn = asyncio.create_subprocess_exec

    async def observe_spawn(*args, **kwargs):
        process = await real_spawn(*args, **kwargs)
        if any('time.sleep(30)' in str(arg) for arg in args):
            processes.append(process)
            started.set()
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', observe_spawn)
    argv = [sys.executable, '-c', "import time; time.sleep(30)"]
    command = subprocess.list2cmdline(argv) if sys.platform == 'win32' else shlex.join(argv)
    clients = []

    def factory(*a, **k):
        client = ScriptedClient([[ModelUsageUpdate(TokenUsage(11, 3)), ModelToolCallCompleted(ToolCall(0, 'process', 'run_command', {'command': command}))]])
        clients.append(client)
        return client

    async def approve(request):
        return ApprovalResponse('allow_once')

    service, store, _, _, _, turn = setup(tmp_path, factory=factory, approval=approve)

    async def run():
        accepted = service.start_turn(turn)
        task = asyncio.create_task(service.execute_turn(accepted['turn_id']))
        try:
            async with asyncio.timeout(10):
                while not started.is_set():
                    if task.done():
                        await task
                        pytest.fail('Process did not actually start')
                    await asyncio.sleep(0.02)
            service.cancel_turn({'turn_id': accepted['turn_id'], 'client_action_id': new_id('act'), 'reason': 'Cancel real process'})
            await asyncio.wait_for(task, 10)
        finally:
            if not task.done():
                task.cancel()
                await task

    try:
        asyncio.run(run())
        assert processes and processes[0].pid > 0
        assert len(clients[0].calls) == 1
        assert service.get_snapshot(turn['session_id'])['turns'][0]['outcome'] == 'indeterminate'
    finally:
        store.close()
