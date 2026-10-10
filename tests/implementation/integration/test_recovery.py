"""F25 recovery uses committed SQLite/Journal and actual owned children, never OS capability claims."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from forge.application.models import ContractError, validate
from forge.application.harness_adapter import LocalTrustedBackend
from forge.application.services import ApplicationServices
from forge.engine.methods import EngineMethods
from forge.engine.persistence import Store, new_id, encoded
from forge.engine.journal_projection import JournalProjector
from forge.sessions.store import SessionStore
from test_application import setup
from test_rpc import seed

ROOT = Path(__file__).resolve().parents[3]


def interrupted(tmp_path, *, complete=False, peer_pid=None):
    service, store, vault, clients, _, params = setup(tmp_path)
    accepted = service.start_turn(params)
    work = store.connection.execute('SELECT * FROM work_items WHERE business_id=?', (accepted['turn_id'],)).fetchone()
    store.claim_work_item(work['id'], expected_version=work['version'])
    journal = SessionStore(tmp_path / 'project', data_root=store.data_dir / 'harness').create(model='scripted-test')
    journal.record_turn_started('Write exactly once.', None)
    journal.record_tool_started('write-once', 'write_file', {'path': 'value.txt'})
    (tmp_path / 'project/value.txt').write_text('actual side effect')
    if complete:
        journal.record_tool_completed('write-once', 'write_file', True)
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat().replace('+00:00', 'Z')
    with store.transaction():
        store.connection.execute('UPDATE turns SET native_ref=? WHERE id=?', (journal.session_id, accepted['turn_id']))
        store.connection.execute('UPDATE sessions SET legacy_ref=? WHERE id=?', (journal.session_id, params['session_id']))
        store.connection.execute('INSERT INTO turn_lifecycle(turn_id,owner_epoch,agent_deadline_utc,environment_deadline_utc,cancel_state,cleanup_state,cleanup_json) VALUES(?,?,?,?,?,?,?)',
            (accepted['turn_id'], store.epoch, deadline, deadline, 'requested', 'pending', encoded({'historical_pid': peer_pid})))
    return service, store, vault, clients, params, accepted, journal


def reopen(tmp_path, vault):
    store = Store(tmp_path / 'data')
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError('Recovery invoked a model')
    service = ApplicationServices(store, profile_id='test-profile', credentials=vault, mode='local-trusted',
        backend=LocalTrustedBackend(), model_client_factory=forbidden)
    return EngineMethods(service, profile='test'), store, calls


def test_committed_intent_without_result_is_explained_and_never_replayed(tmp_path):
    _, original, vault, _, params, accepted, journal = interrupted(tmp_path)
    before = journal.path.read_bytes()
    original.close()
    methods, store, calls = reopen(tmp_path, vault)
    try:
        report = methods.recovery.inspect({'turn_id': accepted['turn_id']})
        validate('recovery.inspect.result', report)
        assert report['state'] == 'reconciling' and report['unknown_side_effects']
        assert report['journal']['unmatched_intents'] == ['write-once']
        assert report['cancel_state'] == 'indeterminate' and report['cleanup_state'] == 'unknown'
        assert report['process_ownership'] == 'unverified_historical'
        retry = methods.start_turn(params)
        assert retry['turn_id'] == accepted['turn_id'] and retry['reused_existing_action']
        assert store.connection.execute('SELECT count(*) FROM turns').fetchone()[0] == 1
        assert not calls and journal.path.read_bytes() == before
        assert (tmp_path / 'project/value.txt').read_text() == 'actual side effect'
        assert store.connection.execute('SELECT count(*) FROM recovery_observations').fetchone()[0] == 1
        with pytest.raises(Exception):
            store.connection.execute("UPDATE recovery_observations SET report_json='{}'")
    finally:
        store.close()


def test_wal_projection_gap_is_rebuilt_once_from_real_durable_result(tmp_path):
    _, original, vault, _, params, accepted, journal = interrupted(tmp_path, complete=True)
    original.connection.execute("CREATE TRIGGER fail_projection BEFORE INSERT ON events WHEN NEW.source_id LIKE 'journal:%' BEGIN SELECT RAISE(ABORT,'injected projection failure'); END")
    with pytest.raises(Exception):
        JournalProjector(original).project(journal.path, params['session_id'], trusted=True)
    assert original.connection.execute('SELECT count(*) FROM projection_offsets').fetchone()[0] == 0
    assert Path(str(original.db_path) + '-wal').stat().st_size > 0
    original.connection.execute('DROP TRIGGER fail_projection')
    before = journal.path.read_bytes()
    original.close()
    methods, store, calls = reopen(tmp_path, vault)
    try:
        report = methods.recovery.inspect({'turn_id': accepted['turn_id']})
        assert report['journal']['state'] == 'complete'
        assert report['journal']['projected_records'] == report['journal']['records'] == 4
        assert report['journal']['unmatched_intents'] == []
        assert report['state'] == 'reconciling' and report['cleanup_state'] == 'unknown'
        count = store.connection.execute('SELECT count(*) FROM events').fetchone()[0]
        methods.recovery.reconcile_startup()
        assert store.connection.execute('SELECT count(*) FROM events').fetchone()[0] == count
        assert store.connection.execute('SELECT count(*) FROM recovery_observations').fetchone()[0] == 1
        assert not calls and journal.path.read_bytes() == before
    finally:
        store.close()


@pytest.mark.parametrize('damage', ['partial_tail', 'invalid_header', 'invalid_chain', 'invalid_record', 'missing'])
def test_incomplete_or_unbound_journal_does_not_guess_success_or_repair_bytes(tmp_path, damage):
    _, original, vault, _, _, accepted, journal = interrupted(tmp_path)
    if damage == 'partial_tail':
        with journal.path.open('ab') as stream:
            stream.write(b'{"interrupted":')
    elif damage == 'invalid_header':
        rows = journal.path.read_text().splitlines()
        header = json.loads(rows[0]); header['session_id'] = 'session-' + 'f' * 24
        rows[0] = json.dumps(header)
        journal.path.write_text('\n'.join(rows) + '\n')
    elif damage in ('invalid_chain','invalid_record'):
        rows=journal.path.read_text().splitlines()
        record=json.loads(rows[-1]);record['sequence']=999
        rows[-1]=json.dumps(record if damage=='invalid_chain' else [])
        journal.path.write_text('\n'.join(rows)+'\n')
    else:
        journal.path.unlink()
    before = journal.path.read_bytes() if journal.path.exists() else None
    original.close()
    methods, store, calls = reopen(tmp_path, vault)
    try:
        report = methods.recovery.inspect({'turn_id': accepted['turn_id']})
        assert report['journal']['state'] == ('invalid' if damage.startswith('invalid_') else 'unavailable' if damage == 'missing' else damage)
        assert report['state'] == 'reconciling' and report['unknown_side_effects']
        assert report['blockers'] and not calls
        assert (journal.path.read_bytes() if journal.path.exists() else None) == before
    finally:
        store.close()


def test_artifact_reconciliation_detects_missing_and_changed_bytes_without_deletion(tmp_path):
    _, original, vault, _, _, accepted, _ = interrupted(tmp_path)
    missing = original.publish_artifact(b'missing', origin='trusted_engine', classification='metadata', profile_id='test-profile')
    changed = original.publish_artifact(b'original', origin='trusted_engine', classification='metadata', profile_id='test-profile')
    (original.data_dir / missing['relative_storage_key']).unlink()
    path = original.data_dir / changed['relative_storage_key']; path.write_bytes(b'changed')
    orphan = original.data_dir / 'artifacts/orphan-unpublished'; orphan.write_bytes(b'preserve uncertain bytes')
    original.close()
    methods, store, _ = reopen(tmp_path, vault)
    try:
        report = methods.recovery.inspect({'turn_id': accepted['turn_id']})
        facts = {item['artifact_id']: item['state'] for item in report['artifacts']['items']}
        assert facts == {missing['id']: 'missing', changed['id']: 'changed'}
        assert report['artifacts']['scope'] == 'profile' and not report['artifacts']['history_gap']
        assert orphan.read_bytes() == b'preserve uncertain bytes' and path.read_bytes() == b'changed'
    finally:
        store.close()


def test_historical_pid_reuse_fixture_never_kills_actual_unrelated_peer(tmp_path):
    peer = subprocess.Popen([sys.executable, '-I', '-c', "import time; print('ready',flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        assert peer.stdout.readline().strip() == b'ready'
        _, original, vault, _, params, accepted, _ = interrupted(tmp_path, peer_pid=peer.pid)
        original.close()
        methods, store, _ = reopen(tmp_path, vault)
        try:
            report = methods.recovery.inspect({'turn_id': accepted['turn_id']})
            assert report['process_ownership'] == 'unverified_historical' and peer.poll() is None
            work = store.connection.execute('SELECT id,version FROM work_items WHERE business_id=?', (accepted['turn_id'],)).fetchone()
            store.mark_indeterminate(work['id'], expected_version=work['version'])
            assert methods.readiness()['status'] == 'blocked'
            queued = methods.start_turn({**params, 'client_action_id': new_id('act')})
            next_work = store.connection.execute('SELECT id,version FROM work_items WHERE business_id=?', (queued['turn_id'],)).fetchone()
            with pytest.raises(ContractError) as error:
                store.claim_work_item(next_work['id'], expected_version=next_work['version'])
            assert error.value.kind == 'INDETERMINATE' and peer.poll() is None
        finally:
            store.close()
    finally:
        peer.terminate(); peer.wait(timeout=5)
        peer.stdout.close()


def test_recovery_report_profile_binding_and_expired_cursor_rebuild_are_explicit(tmp_path):
    _, original, vault, _, _, accepted, _ = interrupted(tmp_path)
    original.close()
    methods, store, _ = reopen(tmp_path, vault)
    try:
        methods.service.profile_id = 'another-profile'
        with pytest.raises(ContractError) as denied:
            methods.recovery.inspect({'turn_id': accepted['turn_id']})
        assert denied.value.kind == 'UNAUTHORIZED'
        methods.service.profile_id = 'test-profile'
        from forge.engine.event_stream import EventStream
        instant = [1000]
        stream = EventStream(methods.service, clock=lambda: instant[0], cursor_ttl_seconds=10)
        scope = {'kind': 'turn', 'id': accepted['turn_id']}
        cursor = stream.cursor(scope, 0); instant[0] = 1011
        with pytest.raises(ContractError) as expired:
            stream.subscribe({'scope': scope, 'after_cursor': cursor})
        assert expired.value.kind == 'INVALID_CURSOR'
        rebuilt = stream.subscribe({'scope': scope})
        assert rebuilt['snapshot']['turns'][0]['state'] == 'reconciling'
        assert stream.decode_cursor(rebuilt['cursor'], scope) == int(rebuilt['high_watermark'])
    finally:
        store.close()


def test_real_bridge_process_loss_never_respawns_or_claims_confirmed_cleanup(tmp_path):
    from test_bridge import policy_input
    from forge.sandbox.srt_backend import SrtBackend
    async def scenario():
        _, workspace, _ = policy_input(tmp_path)
        backend = SrtBackend(workspace, {'engine_epoch': new_id('epoch'), 'sandbox_session_id': new_id('sandbox'),
            'execution_id': None}, tmp_path / 'control')
        try:
            await backend.probe()
            child = backend.process
            child.kill(); await child.wait()
            with pytest.raises(ContractError):
                await backend.probe()
            with pytest.raises(ContractError):
                await backend.close()
            assert backend.process is child and backend._cleanup is None
        finally:
            await backend.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['response-loss', 'double-response-loss', 'late-response', 'unknown-response', 'unconfirmed-limit'])
def test_real_main_response_loss_reconciles_action_without_duplicate_turn(tmp_path, mode):
    fixture, params, turn = seed(tmp_path)
    config = tmp_path / 'recovery-supervisor.json'
    config.write_text(json.dumps({'mode': mode, 'directory': str(tmp_path), 'fixture': str(fixture),
        'params': params, 'turn': turn}), encoding='utf-8')
    result = subprocess.run(['node', str(ROOT / 'tests/implementation/node/recovery-supervisor-case.mjs'), str(config)],
        cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['mode'] == mode


def test_modified_already_projected_record_stays_invalid_without_overwriting_events(tmp_path):
    _, original, vault, _, params, accepted, journal = interrupted(tmp_path)
    JournalProjector(original).project(journal.path, params['session_id'], trusted=True)
    digests = list(original.connection.execute("SELECT hash FROM events WHERE source_id LIKE 'journal:%' ORDER BY store_seq"))
    rows = journal.path.read_text().splitlines()
    record = json.loads(rows[-1]); record['payload']['name'] = 'changed_tool'
    rows[-1] = json.dumps(record); journal.path.write_text('\n'.join(rows)+'\n')
    changed = journal.path.read_bytes()
    original.close()
    methods, store, calls = reopen(tmp_path,vault)
    try:
        assert methods.recovery.inspect({'turn_id':accepted['turn_id']})['journal']['state']=='invalid'
        assert [r[0] for r in store.connection.execute("SELECT hash FROM events WHERE source_id LIKE 'journal:%' ORDER BY store_seq")]==[r[0] for r in digests]
        assert not calls and journal.path.read_bytes()==changed
    finally:store.close()


@pytest.mark.parametrize('expired',[False,True])
def test_restart_deadline_never_grants_a_new_budget(tmp_path,expired):
    _,original,vault,_,_,accepted,_=interrupted(tmp_path)
    if expired:
        original.connection.execute("UPDATE turn_lifecycle SET agent_deadline_utc='2000-01-01T00:00:00Z'")
    original.close()
    methods,store,calls=reopen(tmp_path,vault)
    try:
        report=methods.recovery.inspect({'turn_id':accepted['turn_id']})
        assert report['deadline_status']==('expired' if expired else 'unverified')
        assert report['state']=='reconciling' and not calls
    finally:store.close()


def test_actual_grader_result_survives_unconfirmed_evaluation_cancellation(tmp_path):
    from test_evaluations import make_evaluation,create,start,claim_next
    from test_failures import ActualFailureExecutor
    methods,spec=make_evaluation(tmp_path)
    try:
        run,_=create(methods,spec);start(methods,run);work=claim_next(methods)
        class LostCleanup:
            def finish(self,*args,**kwargs):
                methods.evaluations.cancel({'client_action_id':new_id('act'),'run_id':run['run_id'],'reason':'Cancel after actual grading'})
                kwargs.update(execution_state='cancelled',cleanup_state='unknown',agent_outcome='cancelled')
                methods.evaluations.scheduler.finish(*args,**kwargs)
        asyncio.run(ActualFailureExecutor(methods,tmp_path).execute(work,LostCleanup()))
        facts=methods.evaluations.report_data(run['run_id'])['attempts'][0]
        assert facts['execution_state']=='error' and facts['agent_outcome']=='indeterminate'
        assert facts['cleanup_state']=='unknown' and facts['grade_result']=='fail'
        assert methods.evaluations.run(run['run_id'])['state']=='indeterminate'
        report=methods.recovery.inspect({'attempt_id':work['business_id']})
        assert report['cleanup_state']=='unknown' and report['state']=='reconciling'
        assert report['artifacts']['scope']=='attempt' and report['artifacts']['items'][0]['state']=='valid'
        next_run,_=create(methods,spec);start(methods,next_run)
        with pytest.raises(ContractError):claim_next(methods)
        assert methods.store.connection.execute('SELECT count(*) FROM grades').fetchone()[0]==1
    finally:methods.store.close()


def test_actual_engine_kill_restart_queries_original_action_and_recovery_observation(tmp_path):
    from contextlib import closing
    import sqlite3
    from test_rpc import launch,initialize,send,receive,stop
    fixture,_,turn=seed(tmp_path,wait=True)
    async def scenario():
        process=await launch(tmp_path,fixture)
        try:
            await initialize(process)
            await send(process,'session.start_turn',turn,request_id='start')
            accepted=(await receive(process,wanted_id='start'))['result']
            async with asyncio.timeout(10):
                while True:
                    with closing(sqlite3.connect(tmp_path/'data/engine.sqlite3')) as db:
                        native=db.execute('SELECT native_ref FROM turns WHERE id=?',(accepted['turn_id'],)).fetchone()[0]
                    if native:break
                    await asyncio.sleep(0.02)
            path=next((tmp_path/'data/harness').rglob(native+'.jsonl'))
            process.kill();await process.wait();before=path.read_bytes()
            process=await launch(tmp_path,fixture);await initialize(process)
            await send(process,'action.get',{'method':'session.start_turn','client_action_id':turn['client_action_id']},request_id='action')
            action=(await receive(process,wanted_id='action'))['result']
            assert action['result']['turn_id']==accepted['turn_id'] and action['state']=='indeterminate'
            await send(process,'recovery.inspect',{'turn_id':accepted['turn_id']},request_id='recovery')
            report=(await receive(process,wanted_id='recovery'))['result']
            assert report['startup_observation_id'] and report['state']=='reconciling'
            assert report['cleanup_state']=='unknown' and report['cancel_state']=='indeterminate'
            await send(process,'session.start_turn',turn,request_id='retry')
            assert (await receive(process,wanted_id='retry'))['result']['turn_id']==accepted['turn_id']
            assert path.read_bytes()==before
            with closing(sqlite3.connect(tmp_path/'data/engine.sqlite3')) as db:
                assert db.execute('SELECT count(*) FROM turns').fetchone()[0]==1
        finally:await stop(process)
    asyncio.run(scenario())


def test_actual_main_hard_kill_reconciles_unconfirmed_work_without_replay(tmp_path):
    import sqlite3
    from contextlib import closing
    from test_rpc import launch,initialize,send,receive,stop
    fixture,params,turn=seed(tmp_path,wait=True)
    config=tmp_path/'main-crash.json'
    config.write_text(json.dumps({'mode':'main-crash','directory':str(tmp_path),'fixture':str(fixture),'params':params,'turn':turn}),encoding='utf-8')
    main=subprocess.Popen(['node',str(ROOT/'tests/implementation/node/recovery-supervisor-case.mjs'),str(config)],
        cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    try:
        ready=json.loads(main.stdout.readline());assert ready['mode']=='main-crash'
        assert ready['actual_main_pid']==main.pid
        main.kill();main.wait(timeout=5)
        # A hard kill may terminate Engine before graceful EOF cancellation can commit.
        # Reopening the exclusive writer is evidence of exit, never proof of native cleanup.
        async def scenario():
            process=await launch(tmp_path,fixture)
            try:
                await initialize(process)
                await send(process,'action.get',{'method':'session.start_turn','client_action_id':turn['client_action_id']},request_id='action')
                action=(await receive(process,wanted_id='action'))['result']
                await send(process,'recovery.inspect',{'turn_id':ready['turn_id']},request_id='recovery')
                report=(await receive(process,wanted_id='recovery'))['result']
                assert action['result']['turn_id']==ready['turn_id']
                if report['state']=='finished':
                    assert action['state']=='cancelled'
                    assert report['cleanup_state']=='clean' and report['cancel_state']=='confirmed'
                else:
                    assert report['state']=='reconciling' and action['state']=='indeterminate'
                    assert report['unknown_side_effects'] and report['startup_observation_id']
                    assert report['cleanup_state']=='unknown' and report['cancel_state']=='indeterminate'
                await send(process,'session.start_turn',turn,request_id='retry')
                assert (await receive(process,wanted_id='retry'))['result']['turn_id']==ready['turn_id']
                with closing(sqlite3.connect(tmp_path/'data/engine.sqlite3')) as db:
                    assert db.execute('SELECT count(*) FROM turns').fetchone()[0]==1
            finally:await stop(process)
        asyncio.run(scenario())
    finally:
        if main.poll() is None:main.kill();main.wait(timeout=5)
        main.stdout.close();main.stderr.close()


def test_cleanup_gate_keeps_real_scheduler_waiting_and_readonly_rpc_responsive(tmp_path):
    from forge.engine.scheduler import TurnScheduler
    service,store,_,clients,_,turn=setup(tmp_path)
    try:
        first=service.start_turn(turn)
        work=store.connection.execute('SELECT * FROM work_items WHERE business_id=?',(first['turn_id'],)).fetchone()
        claimed=store.claim_work_item(work['id'],expected_version=work['version'])
        with store.transaction():
            store.connection.execute("INSERT INTO turn_lifecycle(turn_id,owner_epoch,cancel_state,cleanup_state) VALUES(?,?,?,?)",
                (first['turn_id'],store.epoch,'indeterminate','unknown'))
        store.finish_work_item(work['id'],expected_version=claimed['version'],owner_epoch=store.epoch,outcome='indeterminate')
        service.start_turn({**turn,'client_action_id':new_id('act')})
        methods=EngineMethods(service,profile='test');methods.initialized=True
        async def scenario():
            ready=asyncio.Event();scheduler=TurnScheduler(methods,ready)
            running=asyncio.create_task(scheduler.run())
            try:
                await asyncio.wait_for(asyncio.sleep(0.05),1)
                assert scheduler.active is None and not clients
                assert methods.health({})['readiness']['status']=='blocked'
                assert store.connection.execute("SELECT count(*) FROM work_items WHERE state='queued'").fetchone()[0]==1
            finally:
                methods.stopping=True;methods.shutdown_mode='cancel';ready.set()
                await asyncio.wait_for(running,1)
        asyncio.run(scenario())
    finally:store.close()


def test_live_parent_lease_rejects_an_unrelated_peer_without_terminating_it():
    from forge.engine.parent_watch import ParentLease
    import os
    lease=ParentLease.open(os.getppid())
    try:assert lease.alive()
    finally:lease.close()
    assert not lease.alive()
    peer=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    try:
        with pytest.raises((ContractError,OSError)):
            ParentLease.open(peer.pid)
        assert peer.poll() is None
    finally:peer.terminate();peer.wait(timeout=5)
