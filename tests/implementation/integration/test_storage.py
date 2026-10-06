"""F03 uses real SQLite, Journal files and separate processes; no model calls."""
from hashlib import sha256
import json
import asyncio
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from uuid import uuid4

import pytest
from contextlib import closing

from forge.application.models import ContractError
from forge.engine.persistence import Store, new_id
from forge.engine.journal_projection import JournalProjector
from forge.sessions.store import SessionJournal

ROOT = Path(__file__).resolve().parents[3]


def request(session, revision=0):
    return {'client_action_id': new_id('act'), 'session_id': session['id'], 'expected_workspace_revision': revision,
            'input': [{'type': 'text', 'text': 'Inspect project'}], 'connection_id': new_id('conn'),
            'policy_id': new_id('policy'), 'budget_profile_id': new_id('budget')}


def initialize(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    store = Store(tmp_path / 'data')
    workspace = store.register_workspace(project)
    session = store.create_session(workspace['id'])
    return store, workspace, session


def test_post_commit_process_crash_retry_reuses_one_turn(tmp_path):
    store, _, session = initialize(tmp_path)
    params = request(session)
    store.close()
    payload = tmp_path / 'request.json'
    payload.write_text(json.dumps(params))
    marker = tmp_path / 'accepted.json'
    script = '''import json, os, sys
from pathlib import Path
from forge.engine.persistence import Store
store = Store(Path(sys.argv[1]))
result = store.accept_turn('desktop', json.loads(Path(sys.argv[2]).read_text()), {'max_model_calls': 3})
Path(sys.argv[3]).write_text(json.dumps(result))
os._exit(91)
'''
    process = subprocess.run([sys.executable, '-c', script, str(tmp_path / 'data'), str(payload), str(marker)],
                             cwd=ROOT, capture_output=True)
    assert process.returncode == 91 and process.stdout == b'', process.stderr
    with Store(tmp_path / 'data') as restarted:
        result = restarted.accept_turn('desktop', params, {'max_model_calls': 3})
        assert result['turn_id'] == json.loads(marker.read_text())['turn_id']
        assert result['reused_existing_action'] is True
        for table in ('actions', 'turns', 'work_items', 'events'):
            assert restarted.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 1
        with pytest.raises(ContractError) as caught:
            restarted.accept_turn('desktop', {**params, 'input': [{'type': 'text', 'text': 'Changed'}]}, {'max_model_calls': 3})
        assert caught.value.kind == 'IDEMPOTENCY_CONFLICT'


def test_real_sqlite_full_rolls_back_acceptance_and_schedules_nothing(tmp_path):
    store, _, session = initialize(tmp_path)
    with store:
        pages = store.connection.execute('PRAGMA page_count').fetchone()[0]
        store.connection.execute(f'PRAGMA max_page_count={pages}')
        with pytest.raises(sqlite3.OperationalError, match='full'):
            store.accept_turn('desktop', request(session), {'padding': 'x' * 2000000})
        for table in ('actions', 'turns', 'work_items', 'events'):
            assert store.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0


def test_event_insert_failure_rolls_back_action_turn_and_work_item(tmp_path):
    store, _, session = initialize(tmp_path)
    with store:
        store.connection.execute("CREATE TRIGGER fail_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'event failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match='event failure'):
            store.accept_turn('desktop', request(session), {'max_model_calls': 3})
        for table in ('actions', 'turns', 'work_items'):
            assert store.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0


def test_journal_result_durable_projection_failure_restarts_without_execution(tmp_path):
    store, _, session = initialize(tmp_path)
    journal = SessionJournal(tmp_path / 'journal.jsonl', session_id=uuid4().hex, project_root=tmp_path)
    journal.append('session_started', {})
    journal.record_tool_started('call-one', 'run_command', {'command': 'never replay me'})
    journal.record_tool_completed('call-one', 'run_command', True)
    with store:
        store.connection.execute("CREATE TRIGGER fail_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'projection failure'); END")
        with pytest.raises(sqlite3.IntegrityError):
            JournalProjector(store).project(journal.path, session['id'])
        assert store.connection.execute('SELECT COUNT(*) FROM projection_offsets').fetchone()[0] == 0
        store.connection.execute('DROP TRIGGER fail_event')
    before = journal.path.read_bytes()
    with Store(tmp_path / 'data') as restarted:
        projector = JournalProjector(restarted)
        assert projector.project(journal.path, session['id']) == 3
        assert projector.project(journal.path, session['id']) == 0
        assert restarted.connection.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 3
        assert restarted.connection.execute('SELECT last_applied_seq FROM projection_offsets').fetchone()[0] == 3
    assert journal.path.read_bytes() == before


def test_wal_backup_restores_committed_data_while_writer_is_open(tmp_path):
    store, workspace, session = initialize(tmp_path)
    with store:
        accepted = store.accept_turn('desktop', request(session), {'max_model_calls': 3})
        assert Path(str(store.db_path) + '-wal').stat().st_size > 0
        restored = tmp_path / 'restored'
        restored.mkdir()
        store.backup(restored / 'engine.sqlite3')
        with Store(restored) as copy:
            assert copy.connection.execute('SELECT id FROM turns').fetchone()[0] == accepted['turn_id']
            assert copy.connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert copy.connection.execute('SELECT canonical_path FROM workspaces').fetchone()[0] == workspace['canonical_path']


def test_data_directory_owner_is_a_live_os_lock_and_released_after_process_exit(tmp_path):
    with Store(tmp_path / 'data'):
        code = '''from pathlib import Path
import sys
from forge.engine.persistence import Store
from forge.application.models import ContractError
try: Store(Path(sys.argv[1]))
except ContractError as error:
 print(error.kind)
 sys.exit(2)
sys.exit(1)
'''
        result = subprocess.run([sys.executable, '-c', code, str(tmp_path / 'data')], cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 2 and result.stdout.strip() == 'DATA_DIR_IN_USE'
    with Store(tmp_path / 'data') as reopened:
        assert reopened.read_only is False


def test_future_schema_enters_read_only_diagnostics_and_refuses_write(tmp_path):
    with Store(tmp_path / 'data') as store:
        store.connection.execute('PRAGMA user_version=999')
    before = (tmp_path / 'data/engine.sqlite3').read_bytes()
    with Store(tmp_path / 'data') as old:
        assert old.read_only
        assert old.diagnostics()['schema_version'] == 999
        with pytest.raises(ContractError) as caught:
            with old.transaction():
                old.connection.execute("DELETE FROM workspaces")
        assert caught.value.kind == 'INCOMPATIBLE_PROTOCOL'
    assert (tmp_path / 'data/engine.sqlite3').read_bytes() == before


def test_failed_migration_preserves_data_and_consistent_backup(tmp_path):
    migrations = tmp_path / 'migrations'
    migrations.mkdir()
    shutil.copy2(ROOT / 'forge/engine/migrations/001_initial.sql', migrations / '001_initial.sql')
    project = tmp_path / 'project'
    project.mkdir()
    with Store(tmp_path / 'data', migrations_dir=migrations) as original:
        workspace = original.register_workspace(project)
    (migrations / '002_broken.sql').write_text("DELETE FROM workspaces;\nTHIS IS NOT SQL;\n")
    with Store(tmp_path / 'data', migrations_dir=migrations) as failed:
        assert failed.read_only
        assert failed.connection.execute('SELECT id FROM workspaces').fetchone()[0] == workspace['id']
        backups = list((tmp_path / 'data/backups').glob('*.sqlite3'))
        assert backups
        with closing(sqlite3.connect(backups[-1])) as backup:
            assert backup.execute('SELECT id FROM workspaces').fetchone()[0] == workspace['id']


def test_artifact_published_atomically_with_hash_size_and_quota(tmp_path):
    with Store(tmp_path / 'data') as store:
        data = b'actual artifact\x00\xff'
        artifact = store.publish_artifact(data, origin='trusted_engine', classification='metadata', max_bytes=1024)
        assert store.read_artifact(artifact['id']) == data
        assert artifact['sha256'] == sha256(data).hexdigest() and artifact['size'] == len(data)
        assert not list((tmp_path / 'data/artifacts').glob('*.tmp'))
        with pytest.raises(ContractError) as caught:
            store.publish_artifact(data, origin='trusted_engine', classification='metadata', max_bytes=2)
        assert caught.value.kind == 'ARTIFACT_LIMIT'
        assert store.connection.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0] == 1


def test_workspace_alias_identity_and_stale_revision_reject_before_acceptance(tmp_path):
    store, workspace, session = initialize(tmp_path)
    with store:
        alias = store.register_workspace(tmp_path / 'project' / '..' / 'project')
        assert alias['id'] == workspace['id']
        with pytest.raises(ContractError) as caught:
            store.accept_turn('desktop', request(session, revision=99), {})
        assert caught.value.kind == 'STALE_REVISION'
        assert store.connection.execute('SELECT COUNT(*) FROM actions').fetchone()[0] == 0


def test_duplicate_event_is_idempotent_and_conflict_is_quarantined(tmp_path):
    with Store(tmp_path / 'data') as store:
        producer = new_id('producer')
        body = store.event_body('completion.rejected', producer, 1, {'reason': 'stale', 'workspace_revision': 2, 'evidence_revision': 1})
        first = store.append_event(body, producer, 1)
        assert store.append_event(body, producer, 1) == first
        with pytest.raises(ContractError) as caught:
            store.append_event({**body, 'attributes': {**body['attributes'], 'reason': 'changed'}}, producer, 1)
        assert caught.value.kind == 'EVENT_CONFLICT'
        assert store.events() == [first]
        assert store.connection.execute('SELECT COUNT(*) FROM event_conflicts').fetchone()[0] == 1


def test_scheduler_compare_and_swap_single_active_item_and_old_epoch(tmp_path):
    store, _, session = initialize(tmp_path)
    store.accept_turn('desktop', request(session), {})
    second = store.accept_turn('desktop', request(session), {})
    items = [row['id'] for row in store.connection.execute('SELECT id FROM work_items ORDER BY rowid')]
    store.claim_work_item(items[0], expected_version=0)
    with pytest.raises(ContractError):
        store.claim_work_item(items[0], expected_version=0)
    with pytest.raises(sqlite3.IntegrityError):
        store.claim_work_item(items[1], expected_version=0)
    old_epoch = store.epoch
    store.close()
    with Store(tmp_path / 'data') as restarted:
        state = restarted.connection.execute('SELECT * FROM work_items WHERE id=?', (items[0],)).fetchone()
        assert state['state'] == 'reconciling' and state['version'] == 2
        with pytest.raises(ContractError) as caught:
            restarted.finish_work_item(items[0], expected_version=2, owner_epoch=old_epoch, outcome='completed')
        assert caught.value.kind == 'INDETERMINATE'
        with pytest.raises(ContractError) as blocked:
            restarted.claim_work_item(items[1], expected_version=0)
        assert blocked.value.kind == 'INDETERMINATE'
        restarted.mark_indeterminate(items[0], expected_version=2)
        next_item = restarted.claim_work_item(items[1], expected_version=0)
        restarted.finish_work_item(items[1], expected_version=next_item['version'], owner_epoch=restarted.epoch, outcome='completed')
        terminal = restarted.connection.execute('SELECT state,outcome FROM turns WHERE id=?', (second['turn_id'],)).fetchone()
        assert tuple(terminal) == ('finished', 'completed')


def test_foreign_keys_immutable_inputs_and_migration_checksums_are_enforced(tmp_path):
    store, _, session = initialize(tmp_path)
    with store:
        accepted = store.accept_turn('desktop', request(session), {'max_model_calls': 3})
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            store.connection.execute("UPDATE turns SET input_json='[]' WHERE id=?", (accepted['turn_id'],))
        with pytest.raises(sqlite3.IntegrityError):
            store.create_session('unknown-workspace')
        store.connection.execute("UPDATE schema_migrations SET checksum='tampered'")
    with Store(tmp_path / 'data') as reopened:
        assert reopened.read_only
        assert 'checksum' in reopened.failure


def test_attempt_artifact_quota_uses_persisted_sizes_and_diagnostic_budget(tmp_path):
    with Store(tmp_path / 'data') as store:
        with store.transaction() as db:
            db.execute("INSERT INTO experiments VALUES('experiment','test','{}')")
            db.execute("INSERT INTO runs VALUES('run','experiment','hash','{}','created')")
            db.execute("INSERT INTO trials VALUES('trial','run','task','v1',0,NULL)")
            db.execute("INSERT INTO attempts VALUES('attempt','trial',1,'planned',NULL,'pending')")
        store.publish_artifact(b'12345', origin='trusted_engine', classification='metadata', attempt_id='attempt', max_attempt_bytes=8)
        with pytest.raises(ContractError) as caught:
            store.publish_artifact(b'6789', origin='trusted_engine', classification='metadata', attempt_id='attempt', max_attempt_bytes=8)
        assert caught.value.kind == 'ARTIFACT_LIMIT'
        store.publish_artifact(b'12345', origin='trusted_engine', classification='diagnostic', max_diagnostic_bytes=8)
        with pytest.raises(ContractError):
            store.publish_artifact(b'6789', origin='trusted_engine', classification='diagnostic', max_diagnostic_bytes=8)
        assert store.connection.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0] == 2


def test_modified_old_journal_record_conflicts_without_overwriting_projection(tmp_path):
    store, _, session = initialize(tmp_path)
    journal = SessionJournal(tmp_path / 'journal.jsonl', session_id=uuid4().hex, project_root=tmp_path)
    journal.append('session_started', {})
    with store:
        projector = JournalProjector(store)
        assert projector.project(journal.path, session['id']) == 1
        record = json.loads(journal.path.read_text())
        record['payload']['changed'] = True
        journal.path.write_text(json.dumps(record) + '\n')
        with pytest.raises(ContractError) as caught:
            projector.project(journal.path, session['id'])
        assert caught.value.kind == 'EVENT_CONFLICT'
        assert store.connection.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 1
        assert store.connection.execute('SELECT COUNT(*) FROM event_conflicts').fetchone()[0] == 1


def test_failed_real_journal_intent_prevents_command_and_retry_records_intent(tmp_path):
    from forge.permissions.policy import PermissionManager, PermissionRule
    from forge.runtime.executor import ToolExecutor
    from forge.runtime.state import ToolCall
    from forge.sessions.store import SessionError
    from forge.tools.base import ToolRegistry
    from forge.tools.shell import RunCommandTool
    marker = tmp_path / 'executed.txt'
    (tmp_path / 'proof.py').write_text("from pathlib import Path\nPath('executed.txt').write_text('executed')\n")
    journal = SessionJournal(tmp_path / 'journal.jsonl', session_id=uuid4().hex, project_root=tmp_path)
    journal.path.mkdir()  # Real filesystem obstruction, without changing system ACLs.
    permissions = PermissionManager(tmp_path, user_path=tmp_path / 'permissions.json')
    permissions.session_rules.append(PermissionRule('allow'))
    executor = ToolExecutor(ToolRegistry([RunCommandTool(tmp_path, allow_container_writes=True)]), permissions, session_journal=journal)
    call = ToolCall(0, 'call-one', 'run_command', {'command': f'"{sys.executable}" proof.py'})
    with pytest.raises(SessionError, match='append head'):
        asyncio.run(executor.execute(call))
    assert not marker.exists()
    journal.path.rmdir()
    outcome = asyncio.run(executor.execute(call))
    assert outcome.result.success and marker.read_text() == 'executed'
    records = [json.loads(line) for line in journal.path.read_text().splitlines()]
    assert [record['type'] for record in records] == ['tool_started', 'tool_completed']
    assert records[1]['payload']['success'] is True


def test_migration_checksum_is_stable_across_windows_and_linux_line_endings(tmp_path):
    migrations = tmp_path / 'crlf'
    migrations.mkdir()
    original = ROOT / 'forge/engine/migrations/001_initial.sql'
    (migrations / original.name).write_bytes(original.read_text(encoding='utf-8').replace('\n', '\r\n').encode('utf-8'))
    with Store(tmp_path / 'data', migrations_dir=migrations) as windows_format:
        assert not windows_format.read_only
    with Store(tmp_path / 'data') as linux_format:
        assert not linux_format.read_only
