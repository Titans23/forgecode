"""Cancellation evidence uses actual Engine and owned subprocesses, never FakeSandbox."""
import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from forge.engine.persistence import new_id
from test_rpc import initialize, launch, receive, seed, send, stop


def test_pipe_eof_cancels_active_model_and_does_not_dispatch_queued_turn(tmp_path):
    fixture, _, turn = seed(tmp_path, wait=True)
    async def scenario():
        process = await launch(tmp_path, fixture)
        try:
            await initialize(process)
            await send(process, 'session.start_turn', turn, request_id='first')
            first = (await receive(process, wanted_id='first'))['result']
            async with asyncio.timeout(10):
                while True:
                    with closing(sqlite3.connect(tmp_path / 'data/engine.sqlite3')) as db:
                        row = db.execute('SELECT state FROM turns WHERE id=?', (first['turn_id'],)).fetchone()
                    if row[0] == 'running':
                        break
                    await asyncio.sleep(0.02)
            await send(process, 'session.start_turn', {**turn, 'client_action_id': new_id('act')}, request_id='second')
            second = (await receive(process, wanted_id='second'))['result']
            process.stdin.close()
            await asyncio.wait_for(process.stdout.read(), 5)
            assert await asyncio.wait_for(process.wait(), 5) == 0
            with closing(sqlite3.connect(tmp_path / 'data/engine.sqlite3')) as db:
                states = {r[0]: r[1:] for r in db.execute('SELECT id,state,outcome,native_ref FROM turns')}
            assert states[first['turn_id']][1] == 'cancelled'
            assert states[second['turn_id']] == ('finished', 'cancelled', None)
        finally:
            await stop(process)
    asyncio.run(scenario())


@pytest.mark.parametrize('effect', ['read_only', 'workspace_write'])
def test_cancelled_process_never_calls_model_again_or_replays_unknown_write(tmp_path, effect):
    from forge.config import ForgeConfig
    from forge.engine.test_profile import ScriptedModelClient
    from forge.permissions.policy import PermissionManager
    from forge.runtime.dependencies import RuntimeBindings
    from forge.runtime.factory import create_runtime
    from forge.tools.base import Tool, ToolInput, ToolResult
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'value.txt').write_text('public')
    started = asyncio.Event()
    class SlowRead(Tool):
        name = 'slow_read'
        description = 'Actual delayed file read used only in portable cancellation acceptance.'
        input_model = ToolInput
        child = None
        @property
        def effect(self):
            return effect
        async def execute(self, arguments):
            self.child = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c',
                "import pathlib,time,sys; p=pathlib.Path('value.txt'); p.write_text('actual child write') if sys.argv[1]=='workspace_write' else None; print('ready',flush=True); time.sleep(30); print(p.read_text())", effect,
                cwd=self.root, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                assert (await self.child.stdout.readline()).strip() == b'ready'
                started.set()
                content = await self.child.stdout.read()
                await self.child.wait()
                return ToolResult.ok('Read public file', content=content.decode())
            finally:
                if self.child.returncode is None:
                    self.child.kill()
                await self.child.wait()
    config = ForgeConfig(api_key='synthetic', model_id='scripted', max_tokens=1024)
    client = ScriptedModelClient(config, [{'usage': {'input_tokens': 11, 'output_tokens': 3},
        'tool_calls': [{'id': 'slow', 'name': 'slow_read', 'arguments': {}}]},
        {'usage': {'input_tokens': 12, 'output_tokens': 4}, 'text_chunks': ['This response must never run after cancellation.']}])
    bindings = RuntimeBindings(config=config, data_root=tmp_path / 'control', trusted_extensions=False,
        model_client_factory=lambda *args, **kwargs: client,
        permission_manager=PermissionManager(root, mode='auto', load_stored_rules=False), task_relation='new')
    async def scenario():
        conversation, _, _ = create_runtime(root, bindings=bindings)
        tool = SlowRead(root)
        conversation.registry.register(tool)
        async def consume():
            return [event async for event in conversation.stream('Read the public value slowly.')]
        task = asyncio.create_task(consume())
        try:
            waiting = asyncio.create_task(started.wait())
            done, _ = await asyncio.wait((task, waiting), timeout=5, return_when=asyncio.FIRST_COMPLETED)
            if waiting not in done:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
                assert False, [(type(e).__name__, str(getattr(e, 'result', ''))) for e in task.result()] if task.done() else 'Actual slow tool did not start'
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert client.calls == 1
            assert tool.child.returncode is not None
            records = conversation.turn_state.execution_records
            if effect == 'workspace_write':
                assert (root / 'value.txt').read_text() == 'actual child write'
                assert records[-1].status == 'indeterminate'
            else:
                assert records[-1].status == 'cancelled'
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await conversation.runtime_close()
    asyncio.run(scenario())


def test_agent_deadline_interrupts_inflight_real_engine_model_and_persists_cleanup(tmp_path):
    from test_application import setup
    from forge.engine.test_profile import ScriptedModelClient
    clients = []
    def factory(config, **kwargs):
        client = ScriptedModelClient(config, [{'delay_seconds': 30, 'text_chunks': ['too late']}])
        clients.append(client)
        return client
    service, store, _, _, _, turn = setup(tmp_path, factory=factory)
    try:
        # Reserve startup time so this case actually exercises an in-flight
        # request, even while other real build/test processes contend for I/O.
        budget = service.put_budget({'max_model_calls': 4, 'max_tool_calls': 6, 'wall_seconds': 5})
        accepted = service.start_turn({**turn, 'budget_profile_id': budget})
        async def run():
            await asyncio.wait_for(service.execute_turn(accepted['turn_id']), 10)
        asyncio.run(run())
        result = service.get_snapshot(turn['session_id'])['turns'][0]
        assert result['outcome'] == 'timed_out' and clients[0].calls == 1
        row = store.connection.execute('SELECT * FROM turn_lifecycle WHERE turn_id=?', (accepted['turn_id'],)).fetchone()
        assert row['cancel_state'] == 'confirmed' and row['cleanup_state'] == 'clean'
        assert row['agent_deadline_utc'] and row['environment_deadline_utc'] and row['grader_deadline_utc'] is None
        assert any(e['event_type'] == 'cancellation.confirmed' for e in store.events())
    finally:
        store.close()


def test_agent_deadline_expired_during_real_preparation_never_starts_model_request(tmp_path):
    import time
    from test_application import setup
    from forge.engine.test_profile import ScriptedModelClient
    clients=[]
    def factory(config,**kwargs):
        client=ScriptedModelClient(config,[{'delay_seconds':30,'text_chunks':['too late']}])
        clients.append(client)
        time.sleep(2)  # Actual preparation work exceeds the one-second agent deadline.
        return client
    service,store,_,_,_,turn=setup(tmp_path,factory=factory)
    try:
        budget=service.put_budget({'max_model_calls':4,'max_tool_calls':6,'wall_seconds':1})
        accepted=service.start_turn({**turn,'budget_profile_id':budget})
        async def run():await asyncio.wait_for(service.execute_turn(accepted['turn_id']),10)
        asyncio.run(run())
        assert clients and sum(client.calls for client in clients)==0
        assert service.get_snapshot(turn['session_id'])['turns'][0]['outcome']=='timed_out'
        row=store.connection.execute('SELECT cancel_state,cleanup_state FROM turn_lifecycle WHERE turn_id=?',(accepted['turn_id'],)).fetchone()
        assert tuple(row)==('confirmed','clean')
    finally:store.close()


def test_real_engine_crash_reopens_lifecycle_unknown_and_never_replays(tmp_path):
    from forge.engine.persistence import Store
    fixture, _, turn = seed(tmp_path, wait=True)
    async def run():
        process = await launch(tmp_path, fixture)
        try:
            await initialize(process)
            await send(process, 'session.start_turn', turn, request_id='start')
            accepted = (await receive(process, wanted_id='start'))['result']
            async with asyncio.timeout(10):
                while True:
                    with closing(sqlite3.connect(tmp_path / 'data/engine.sqlite3')) as db:
                        row = db.execute('SELECT native_ref FROM turns WHERE id=?', (accepted['turn_id'],)).fetchone()
                    if row[0] is not None:
                        break
                    await asyncio.sleep(0.02)
            process.kill()
            await asyncio.wait_for(process.wait(), 5)
            process = await launch(tmp_path, fixture)
            await initialize(process)
            await send(process, 'system.health', {}, request_id='health')
            health = (await receive(process, wanted_id='health'))['result']
            assert health['readiness']['status'] == 'blocked'
            assert 'previous_execution_requires_reconciliation' in health['readiness']['reasons']
            await send(process, 'system.shutdown', {'client_action_id': new_id('act'), 'mode': 'cancel'}, request_id='shutdown')
            await receive(process, wanted_id='shutdown')
            await asyncio.wait_for(process.stdout.read(), 5)
            assert await asyncio.wait_for(process.wait(), 5) == 0
            with Store(tmp_path / 'data') as reopened:
                row = reopened.connection.execute('SELECT * FROM turn_lifecycle WHERE turn_id=?', (accepted['turn_id'],)).fetchone()
                assert row['cleanup_state'] == 'unknown' and row['cancel_state'] == 'indeterminate'
                assert reopened.connection.execute('SELECT state FROM turns WHERE id=?', (accepted['turn_id'],)).fetchone()[0] == 'reconciling'
                assert reopened.connection.execute('SELECT count(*) FROM turns').fetchone()[0] == 1
        finally:
            await stop(process)
    asyncio.run(run())


def test_completed_turn_keeps_result_when_model_cleanup_observation_fails(tmp_path):
    from test_application import ScriptedClient, script, setup
    class CloseFault(ScriptedClient):
        async def aclose(self):
            self.closed = True
            raise OSError('Injected scripted-provider close failure')
    service, store, _, _, _, turn = setup(tmp_path, factory=lambda *args, **kwargs: CloseFault(script()))
    try:
        accepted = service.start_turn(turn)
        asyncio.run(service.execute_turn(accepted['turn_id']))
        assert service.get_snapshot(turn['session_id'])['turns'][0]['outcome'] == 'completed'
        row = store.connection.execute('SELECT cleanup_state,cleanup_json FROM turn_lifecycle WHERE turn_id=?', (accepted['turn_id'],)).fetchone()
        assert row[0] == 'unknown' and json.loads(row[1])['model_cleanup_error'] == 'OSError'
    finally:
        store.close()
