"""Real stdio Engine processes; scripted profile replaces only the model."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

from forge.application.models import ContractError, METHODS, validate
from forge.engine.event_stream import EventStream
from forge.engine.persistence import new_id
from test_application import setup


ROOT = Path(__file__).resolve().parents[3]


def test_snapshot_high_watermark_and_persistent_replay_have_no_gap(tmp_path):
    service, store, _, _, _, turn = setup(tmp_path)
    try:
        stream = EventStream(service)
        scope = {'kind': 'session', 'id': turn['session_id']}
        subscribed = stream.subscribe({'scope': scope})
        accepted = service.start_turn(turn)
        batch = stream.next_batch(subscribed['subscription_id'])
        assert batch['params']['events'][0]['turn_id'] == accepted['turn_id']
        assert stream.next_batch(subscribed['subscription_id']) is None  # wait for reducer ack
        stream.ack({'subscription_id': subscribed['subscription_id'], 'cursor': batch['params']['cursor']})
        asyncio.run(service.execute_turn(accepted['turn_id']))
        following = stream.next_batch(subscribed['subscription_id'])
        assert following and len(following['params']['events']) > 1
        validate('event-notification', following)
        replay_stream = EventStream(service)
        replay = replay_stream.subscribe({'scope': scope, 'after_cursor': subscribed['cursor']})
        repeated = replay_stream.next_batch(replay['subscription_id'])
        assert repeated['params']['events'][0]['event_id'] == batch['params']['events'][0]['event_id']
        assert int(repeated['params']['events'][0]['store_seq']) > int(subscribed['high_watermark'])
    finally:
        store.close()


def test_cursor_rejects_wrong_scope_forgery_and_unpublished_ack(tmp_path):
    service, store, _, _, _, turn = setup(tmp_path)
    try:
        stream = EventStream(service)
        subscribed = stream.subscribe({'scope': {'kind': 'session', 'id': turn['session_id']}})
        with pytest.raises(ContractError):
            stream.subscribe({'scope': {'kind': 'all'}, 'after_cursor': subscribed['cursor']})
        with pytest.raises(ContractError):
            stream.subscribe({'scope': {'kind': 'session', 'id': turn['session_id']}, 'after_cursor': 'forged'})
        accepted = service.start_turn(turn)
        future = stream.cursor({'kind': 'session', 'id': turn['session_id']}, 999999)
        with pytest.raises(ContractError):
            stream.ack({'subscription_id': subscribed['subscription_id'], 'cursor': future})
        assert stream.next_batch(subscribed['subscription_id'])['params']['events'][0]['turn_id'] == accepted['turn_id']
    finally:
        store.close()


def seed(tmp_path, *, wait=False):
    service, store, vault, _, params, turn = setup(tmp_path)
    fixture = tmp_path / 'scripted.json'
    fixture.write_text(json.dumps({'schema_version': 'forge.scripted-model.v1', 'origin': 'scripted', 'max_steps': 2,
        'allowed_tools': ['read_file', 'finish_task'], 'approve_scripted_calls': False,
        'credentials': vault.keys, 'responses': [
        {'delay_seconds': 30 if wait else 0, 'usage': {'input_tokens': 11, 'output_tokens': 3},
         'tool_calls': [{'id': 'read', 'name': 'read_file', 'arguments': {'path': 'value.txt'}}]},
        {'usage': {'input_tokens': 12, 'output_tokens': 4}, 'tool_calls': [{'id': 'finish', 'name': 'finish_task',
          'arguments': {'task_kind': 'answer', 'status': 'completed', 'summary': 'The value is B.'}}]},
    ]}), encoding='utf-8')
    store.close()
    return fixture, params, turn


async def launch(tmp_path, fixture=None, *, principal='main', profile='test', interactive_approvals=False):
    argv = [sys.executable, '-m', 'forge.engine', '--data-dir', str(tmp_path / 'data'), '--profile', profile,
            '--principal', principal]
    if fixture:
        argv += ['--scripted-fixture', str(fixture), '--execution-mode', 'local-trusted']
    if interactive_approvals:
        argv.append('--interactive-approvals')
    return await asyncio.create_subprocess_exec(*argv, cwd=ROOT, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1048577)


async def send(process, method, params, *, request_id='r'):
    message = {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}
    process.stdin.write((json.dumps(message) + '\n').encode())
    await process.stdin.drain()


async def receive(process, *, wanted_id=None):
    async with asyncio.timeout(15):
        while True:
            raw = await process.stdout.readline()
            assert raw, (await process.stderr.read()).decode()
            value = json.loads(raw)
            if isinstance(value, dict):
                validate('event-notification' if value.get('method') == 'events.batch' else 'rpc-response', value)
            if wanted_id is None or isinstance(value, dict) and value.get('id') == wanted_id:
                return value


async def initialize(process, *, profile='test'):
    from forge.engine.methods import manifest_hash
    await send(process, 'system.initialize', {'protocol': {'major': 1, 'minor': 0}, 'client_build': 'integration-test',
        'expected_manifest_hash': manifest_hash(), 'profile': profile}, request_id='init')
    return await receive(process, wanted_id='init')


async def stop(process):
    if process.returncode is None:
        process.kill()
    await process.wait()


def test_real_child_handshake_start_events_cancel_action_and_shutdown(tmp_path):
    fixture, _, turn = seed(tmp_path, wait=True)

    async def run():
        process = await launch(tmp_path, fixture)
        try:
            hello = await initialize(process)
            assert hello['result']['readiness']['status'] == 'degraded'
            supported=set(hello['result']['capabilities']['supported_methods'])
            assert 'evaluation.create_run' in supported
            assert supported.isdisjoint({name for name,item in METHODS.items() if item.get('implementation_status')=='contract_only'})
            await send(process, 'events.subscribe', {'scope': {'kind': 'session', 'id': turn['session_id']}}, request_id='sub')
            subscription = (await receive(process, wanted_id='sub'))['result']
            await send(process, 'session.start_turn', turn, request_id='start')
            accepted = (await receive(process, wanted_id='start'))['result']
            batch = await receive(process)
            assert batch['method'] == 'events.batch'
            await send(process, 'events.ack', {'subscription_id': subscription['subscription_id'], 'cursor': batch['params']['cursor']}, request_id='ack')
            await receive(process, wanted_id='ack')
            # The original accepted action is durable, even if the start response was lost.
            await send(process, 'action.get', {'client_action_id': turn['client_action_id'], 'method': 'session.start_turn'}, request_id='action')
            assert (await receive(process, wanted_id='action'))['result']['result']['turn_id'] == accepted['turn_id']
            await send(process, 'session.cancel_turn', {'client_action_id': new_id('act'), 'turn_id': accepted['turn_id'], 'reason': 'Cancel child turn'}, request_id='cancel')
            assert (await receive(process, wanted_id='cancel'))['result']['state'] in ('cancel_requested', 'finished')
            await send(process, 'system.shutdown', {'client_action_id': new_id('act'), 'mode': 'cancel'}, request_id='shutdown')
            assert (await receive(process, wanted_id='shutdown'))['result']['state'] == 'draining'
            assert await asyncio.wait_for(process.wait(), 15) == 0
        finally:
            await stop(process)

    asyncio.run(run())


def test_child_rejects_invalid_frames_and_notifications_without_mutation(tmp_path):
    fixture, _, turn = seed(tmp_path)

    async def run():
        process = await launch(tmp_path, fixture)
        try:
            await initialize(process)
            process.stdin.write(b'{"jsonrpc":"2.0","id":"duplicate","id":"other","method":"system.health","params":{}}\n')
            await process.stdin.drain()
            assert (await receive(process))['error']['code'] == -32700
            process.stdin.write((json.dumps([1, {'jsonrpc': '2.0', 'method': 'system.health', 'params': {}},
                {'jsonrpc': '2.0', 'method': 'session.start_turn', 'params': turn},
                {'jsonrpc': '2.0', 'id': 'valid', 'method': 'system.health', 'params': {}}]) + '\n').encode())
            await process.stdin.drain()
            batch = await receive(process)
            assert len(batch) == 2 and batch[0]['id'] is None and batch[0]['error']['code'] == -32600
            assert batch[1]['id'] == 'valid'
            await send(process, 'session.get', {'session_id': turn['session_id']}, request_id='session')
            assert not (await receive(process, wanted_id='session'))['result']['turns']
            await send(process, 'system.shutdown', {'client_action_id': new_id('act'), 'mode': 'drain'}, request_id='shutdown')
            await receive(process, wanted_id='shutdown')
            assert await asyncio.wait_for(process.wait(), 15) == 0
        finally:
            await stop(process)

    asyncio.run(run())


def test_child_main_acl_and_manifest_mismatch(tmp_path):
    async def run():
        process = await launch(tmp_path, principal='renderer', profile='desktop')
        try:
            await send(process, 'system.health', {}, request_id='before-init')
            assert (await receive(process, wanted_id='before-init'))['error']['data']['kind'] == 'INCOMPATIBLE_PROTOCOL'
            await send(process, 'system.initialize', {'protocol': {'major': 1, 'minor': 0}, 'client_build': 'test',
                'expected_manifest_hash': '0' * 64, 'profile': 'desktop'}, request_id='wrong-manifest')
            assert (await receive(process, wanted_id='wrong-manifest'))['error']['data']['kind'] == 'INCOMPATIBLE_PROTOCOL'
            hello = await initialize(process, profile='desktop')
            assert hello['result']['readiness']['status'] == 'blocked'
            await send(process, 'workspace.register', {'client_action_id': new_id('act'), 'path': str(tmp_path),
                'selection_nonce': 'a' * 32}, request_id='denied')
            assert (await receive(process, wanted_id='denied'))['error']['data']['kind'] == 'UNAUTHORIZED'
        finally:
            await stop(process)

    asyncio.run(run())


def test_scripted_fixture_is_rejected_outside_test_profile(tmp_path):
    fixture, _, _ = seed(tmp_path)
    completed = subprocess.run([sys.executable, '-m', 'forge.engine', '--data-dir', str(tmp_path / 'other-data'),
        '--profile', 'desktop', '--scripted-fixture', str(fixture)], cwd=ROOT, capture_output=True, timeout=15)
    assert completed.returncode == 3
    assert not completed.stdout


def test_child_oversized_depth_nonfinite_and_blank_frames_resynchronize(tmp_path):
    async def run():
        process = await launch(tmp_path, profile='desktop')
        try:
            await initialize(process, profile='desktop')
            for raw in (b'\n', b'[' * 34 + b'0' + b']' * 34 + b'\n', b'{"value":NaN}\n', b'x' * 1048577 + b'\n'):
                process.stdin.write(raw)
                await process.stdin.drain()
                assert (await receive(process))['error']['code'] == -32700
            await send(process, 'system.health', {}, request_id='after-bad-frames')
            assert (await receive(process, wanted_id='after-bad-frames'))['result']['active_work_items'] == 0
            await send(process, 'system.shutdown', {'client_action_id': new_id('act'), 'mode': 'drain'}, request_id='shutdown')
            await receive(process, wanted_id='shutdown')
            assert await asyncio.wait_for(process.wait(), 15) == 0
        finally:
            await stop(process)

    asyncio.run(run())


def test_slow_reader_still_accepts_cancel_and_durable_action_query(tmp_path):
    import sqlite3
    from contextlib import closing
    from forge.engine.journal_projection import JournalProjector
    from forge.sessions.store import SessionStore
    service, store, vault, _, _, turn = setup(tmp_path)
    stream = EventStream(service)
    cursor = stream.cursor({'kind': 'all'}, 0)
    journal = SessionStore(tmp_path / 'project', data_root=store.data_dir / 'load-journal').create(model='transport-load-test')
    for i in range(120):
        journal.append('transport_test_record', {'index': i})
    JournalProjector(store).project(journal.path, turn['session_id'])
    fixture = tmp_path / 'scripted.json'
    fixture.write_text(json.dumps({'schema_version': 'forge.scripted-model.v1', 'origin': 'scripted', 'max_steps': 1,
        'allowed_tools': [], 'approve_scripted_calls': False, 'credentials': vault.keys, 'responses': [
        {'delay_seconds': 30, 'usage': {'input_tokens': 11, 'output_tokens': 3}, 'text_chunks': ['Waiting test model']}]}))
    store.close()

    async def run():
        process = await launch(tmp_path, fixture)
        try:
            await initialize(process)
            for i in range(8):
                await send(process, 'events.subscribe', {'scope': {'kind': 'all'}, 'after_cursor': cursor}, request_id=f'sub-{i}')
            await send(process, 'session.start_turn', turn, request_id='unread-start')
            # Do not read stdout while the server's bounded batches fill the OS pipe.
            async with asyncio.timeout(10):
                while True:
                    with closing(sqlite3.connect(tmp_path / 'data/engine.sqlite3')) as reader:
                        row = reader.execute('SELECT id,state FROM turns').fetchone()
                    if row and row[1] == 'running':
                        break
                    await asyncio.sleep(0.02)
            cancel_action = new_id('act')
            await send(process, 'session.cancel_turn', {'client_action_id': cancel_action, 'turn_id': row[0], 'reason': 'Cancel while stdout is backpressured'}, request_id='cancel')
            async with asyncio.timeout(10):
                while True:
                    with closing(sqlite3.connect(tmp_path / 'data/engine.sqlite3')) as reader:
                        state = reader.execute('SELECT state,outcome FROM turns WHERE id=?', (row[0],)).fetchone()
                    if state[0] == 'finished':
                        break
                    await asyncio.sleep(0.02)
            assert state[1] == 'cancelled'
            await receive(process, wanted_id='cancel')
            await send(process, 'action.get', {'client_action_id': turn['client_action_id'], 'method': 'session.start_turn'}, request_id='recovered')
            recovered = (await receive(process, wanted_id='recovered'))['result']
            assert recovered['result']['turn_id'] == row[0] and recovered['state'] == 'cancelled'
            await send(process, 'system.shutdown', {'client_action_id': new_id('act'), 'mode': 'cancel'}, request_id='shutdown')
            await receive(process, wanted_id='shutdown')
            # Drain the finite queue so the shared pipe can close normally.
            await asyncio.wait_for(process.stdout.read(), 15)
            assert await asyncio.wait_for(process.wait(), 15) == 0
        finally:
            await stop(process)

    asyncio.run(run())


def test_restarted_child_does_not_dispatch_before_handshake(tmp_path):
    import sqlite3
    from contextlib import closing
    fixture, _, turn = seed(tmp_path, wait=True)
    from forge.engine.persistence import Store
    from forge.application.services import ApplicationServices
    from forge.application.harness_adapter import LocalTrustedBackend
    from forge.engine.test_profile import load_scripted_profile
    profile = load_scripted_profile(fixture)
    with Store(tmp_path / 'data') as store:
        service = ApplicationServices(store, profile_id='test-profile', credentials=profile.credentials,
            mode='local-trusted', backend=LocalTrustedBackend(), model_client_factory=profile.model_client_factory)
        accepted = service.start_turn(turn)

    async def run():
        process = await launch(tmp_path, fixture)
        try:
            await send(process, 'system.health', {}, request_id='before-init')
            assert (await receive(process, wanted_id='before-init'))['error']['data']['kind'] == 'INCOMPATIBLE_PROTOCOL'
            with closing(sqlite3.connect(tmp_path / 'data/engine.sqlite3')) as reader:
                assert reader.execute('SELECT state,native_ref FROM turns').fetchone() == ('queued', None)
            await initialize(process)
            await send(process, 'session.cancel_turn', {'client_action_id': new_id('act'), 'turn_id': accepted['turn_id'], 'reason': 'Stop resumed queue'}, request_id='cancel')
            await receive(process, wanted_id='cancel')
            await send(process, 'system.shutdown', {'client_action_id': new_id('act'), 'mode': 'cancel'}, request_id='shutdown')
            await receive(process, wanted_id='shutdown')
            assert await asyncio.wait_for(process.wait(), 15) == 0
        finally:
            await stop(process)

    asyncio.run(run())


def test_real_turn_paging_idempotency_conflict_and_retained_history_gap(tmp_path):
    from forge.engine.methods import EngineMethods
    service, store, _, _, _, turn = setup(tmp_path)
    try:
        methods = EngineMethods(service, profile='test')
        identities = []
        for _ in range(5):
            identities.append(service.start_turn({**turn, 'client_action_id': new_id('act')})['turn_id'])
        page = methods.get_session({'session_id': turn['session_id'], 'limit': 2})
        validate('session.get.result', page)
        second = methods.get_session({'session_id': turn['session_id'], 'limit': 2, 'cursor': page['next_cursor']})
        last = methods.get_session({'session_id': turn['session_id'], 'limit': 2, 'cursor': second['next_cursor']})
        assert [t['turn_id'] for p in (page, second, last) for t in p['turns']] == identities
        assert last['next_cursor'] is None
        accepted = service.start_turn(turn)
        with pytest.raises(ContractError) as conflict:
            service.start_turn({**turn, 'input': [{'type': 'text', 'text': 'Changed action parameters'}]})
        assert conflict.value.kind == 'IDEMPOTENCY_CONFLICT'
        assert service.start_turn(turn)['turn_id'] == accepted['turn_id']
        stream = methods.events
        old_cursor = stream.cursor({'kind': 'all'}, 0)
        with store.transaction():
            store.connection.execute('DELETE FROM events WHERE store_seq<=3')
        with pytest.raises(ContractError) as expired:
            stream.subscribe({'scope': {'kind': 'all'}, 'after_cursor': old_cursor})
        assert expired.value.kind == 'INVALID_CURSOR'
        assert stream.subscribe({'scope': {'kind': 'all'}})['history_gap'] is True
    finally:
        store.close()
