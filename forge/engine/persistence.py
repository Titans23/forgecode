"""Single-writer SQLite store. Acceptance never starts model or tool execution."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from uuid import uuid4

from forge.application.models import ContractError, canonical_hash, validate, validate_event


def new_id(prefix):
    return f'{prefix}-{uuid4()}'


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def migration_checksum(path):
    # Git checkouts and Windows editors can change line endings without changing SQL.
    return sha256(path.read_text(encoding='utf-8').encode('utf-8')).hexdigest()


def sync_directory(path):
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class DirectoryLock:
    """The OS handle is authoritative; a stale lock file does not own the directory."""
    def __init__(self, directory):
        self.file = (directory / 'owner.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self.file.seek(0, os.SEEK_END) == 0:
                    self.file.write(b'0')
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            raise ContractError('Data directory already has a writer', kind='DATA_DIR_IN_USE', code=-32010) from error

    def close(self):
        if not self.file.closed:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()


def validate_data_directory(directory):
    directory = directory.resolve()
    if str(directory).startswith(('\\\\', '//')) or any(part.casefold() in ('onedrive', 'dropbox', 'google drive') for part in directory.parts):
        raise ContractError('Use a supported local data directory', kind='UNSUPPORTED_PLATFORM', code=-32010)
    if os.name == 'nt':
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(directory.anchor)) != 3:
            raise ContractError('Data directory requires a fixed local drive', kind='UNSUPPORTED_PLATFORM', code=-32010)
    elif Path('/proc/self/mountinfo').is_file():
        matches = []
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            before, after = line.split(' - ', 1)
            mount = Path(before.split()[4].replace('\\040', ' '))
            if directory.is_relative_to(mount):
                matches.append((len(mount.parts), after.split()[0]))
        if matches and max(matches)[1] in ('nfs', 'nfs4', 'cifs', 'smb3', 'fuse.sshfs', '9p'):
            raise ContractError('Network filesystems are unsupported for control data', kind='UNSUPPORTED_PLATFORM', code=-32010)
    return directory


class Store:
    def __init__(self, data_dir: Path, *, migrations_dir: Path | None = None):
        self.data_dir = validate_data_directory(Path(data_dir))
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = DirectoryLock(self.data_dir)
        self.db_path = self.data_dir / 'engine.sqlite3'
        self.epoch = new_id('epoch')
        self.read_only = False
        self.failure = None
        self.connection = None
        try:
            self.connection = sqlite3.connect(self.db_path, isolation_level=None, timeout=5)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute('PRAGMA foreign_keys=ON')
            self.connection.execute('PRAGMA busy_timeout=5000')
            self._migrate(migrations_dir or Path(__file__).with_name('migrations'))
            if not self.read_only:
                if self.connection.execute('PRAGMA journal_mode=WAL').fetchone()[0] != 'wal':
                    raise RuntimeError('SQLite WAL is unavailable')
                self.connection.execute('PRAGMA synchronous=FULL')
                with self.transaction():
                    self.connection.execute('INSERT OR IGNORE INTO store_meta VALUES(?,?)', ('producer_id', new_id('producer')))
                    self.connection.execute('INSERT OR IGNORE INTO store_meta VALUES(?,?)', ('generation', new_id('generation')))
                    # Old ownership requires reconciliation, never automatic re-execution.
                    self.connection.execute("UPDATE work_items SET state='reconciling',version=version+1 WHERE state IN ('running','cancel_requested') AND owner_epoch!=?", (self.epoch,))
                    self.connection.execute("UPDATE turns SET state='reconciling' WHERE id IN (SELECT business_id FROM work_items WHERE state='reconciling' AND kind='turn') AND state!='finished'")
                    if self.connection.execute("SELECT 1 FROM sqlite_master WHERE name='turn_lifecycle'").fetchone():
                        self.connection.execute("UPDATE turn_lifecycle SET cleanup_state='unknown',cancel_state='indeterminate' "
                            "WHERE owner_epoch!=? AND cleanup_state IN ('pending','running')", (self.epoch,))
                    if self.connection.execute("SELECT 1 FROM sqlite_master WHERE name='recovery_traces'").fetchone():
                        for row in self.connection.execute("SELECT business_id FROM work_items WHERE kind='turn' AND state='reconciling'").fetchall():
                            original=self.connection.execute('SELECT trace_id,span_id FROM turn_traces WHERE turn_id=?',(row[0],)).fetchone()
                            self.connection.execute('INSERT INTO recovery_traces VALUES(?,?,?,?,?)',
                                (row[0],self.epoch,uuid4().hex,uuid4().hex[:16],original['trace_id'] if original else None))
                            self._recovery_event('recovery.started',row[0],'previous_engine_exited')
        except BaseException:
            self.close()
            raise

    def _diagnostic_mode(self, reason):
        self.failure = reason
        self.connection.close()
        self.connection = sqlite3.connect(self.db_path.as_uri() + '?mode=ro', uri=True, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA query_only=ON')
        self.read_only = True

    def _migrate(self, directory):
        files = sorted(directory.glob('[0-9][0-9][0-9]_*.sql'))
        migrations = {int(path.name.split('_')[0]): path for path in files}
        if not migrations or sorted(migrations) != list(range(1, max(migrations) + 1)) or len(migrations) != len(files):
            raise ValueError('Migration versions must be contiguous and unique')
        version = self.connection.execute('PRAGMA user_version').fetchone()[0]
        if version > max(migrations):
            self._diagnostic_mode('Database schema is newer than this Engine')
            return
        try:
            if version:
                applied = dict(self.connection.execute('SELECT version,checksum FROM schema_migrations').fetchall())
                if set(applied) != set(range(1, version + 1)):
                    raise ValueError('Migration history differs from database version')
                for number, checksum in applied.items():
                    if migration_checksum(migrations[number]) != checksum:
                        raise ValueError('Applied migration checksum changed')
            if version < max(migrations):
                if self.connection.execute('SELECT count(*) FROM sqlite_master WHERE type=\'table\'').fetchone()[0]:
                    self.backup(self.data_dir / 'backups' / (uuid4().hex + '.sqlite3'))
                self.connection.execute('BEGIN IMMEDIATE')
                for number in range(version + 1, max(migrations) + 1):
                    source = migrations[number].read_text(encoding='utf-8')
                    # executescript implicitly commits; execute complete statements within our transaction.
                    statement = ''
                    for line in source.splitlines(keepends=True):
                        statement += line
                        if sqlite3.complete_statement(statement):
                            self.connection.execute(statement)
                            statement = ''
                    if statement.strip():
                        raise ValueError('Migration contains an incomplete SQL statement')
                    self.connection.execute('INSERT INTO schema_migrations VALUES(?,?,?)',
                        (number, migration_checksum(migrations[number]), utc_now()))
                    self.connection.execute(f'PRAGMA user_version={number}')
                self.connection.execute('COMMIT')
        except (sqlite3.Error, ValueError) as error:
            if self.connection.in_transaction:
                self.connection.execute('ROLLBACK')
            self._diagnostic_mode('Migration failed: ' + str(error))
            path = self.data_dir / 'migration-failure.json'
            path.write_text(encoded({'status': 'blocked', 'reason': self.failure, 'at': utc_now()}), encoding='utf-8')

    @contextmanager
    def transaction(self):
        if self.read_only:
            raise ContractError('Database is in read-only diagnostic mode', kind='INCOMPATIBLE_PROTOCOL', code=-32010)
        if self.connection.in_transaction:
            raise RuntimeError('Nested write transactions are not supported')
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            yield self.connection
            self.connection.execute('COMMIT')
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute('ROLLBACK')
            raise

    def diagnostics(self):
        return {'read_only': self.read_only, 'reason': self.failure,
                'schema_version': self.connection.execute('PRAGMA user_version').fetchone()[0],
                'integrity': self.connection.execute('PRAGMA quick_check').fetchone()[0], 'engine_epoch': self.epoch}

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def register_workspace(self, path):
        with self.transaction():
            return self._register_workspace(path)

    def _register_workspace(self, path):
        if not self.connection.in_transaction:
            raise RuntimeError('Workspace registration needs a write transaction')
        canonical = Path(path).resolve(strict=True)
        if not canonical.is_dir():
            raise ContractError('Workspace must be a directory')
        identity = canonical.stat()
        if not identity.st_ino:
            raise ContractError('Filesystem does not expose workspace identity')
        file_identity = f'{identity.st_dev}:{identity.st_ino}'
        existing = self.connection.execute('SELECT * FROM workspaces WHERE file_identity=?', (file_identity,)).fetchone()
        if existing:
            return dict(existing)
        self.connection.execute('INSERT INTO workspaces VALUES(?,?,?,?,?)', (new_id('ws'), str(canonical), file_identity, 'inspect_only', 0))
        return dict(self.connection.execute('SELECT * FROM workspaces WHERE file_identity=?', (file_identity,)).fetchone())

    def create_session(self, workspace_id, *, legacy_ref=None):
        with self.transaction():
            if legacy_ref:
                existing = self.connection.execute('SELECT * FROM sessions WHERE legacy_ref=?', (legacy_ref,)).fetchone()
                if existing:
                    if existing['workspace_id'] != workspace_id:
                        raise ContractError('Legacy session belongs to another workspace', kind='IDEMPOTENCY_CONFLICT', code=-32010)
                    return dict(existing)
            session_id = new_id('ses')
            self.connection.execute('INSERT INTO sessions VALUES(?,?,?,?)', (session_id, workspace_id, legacy_ref, utc_now()))
            return dict(self.connection.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone())

    def accept_turn(self, profile_id, params, configuration, *, action_method='session.start_turn', action_params=None):
        validate('start-turn', params)
        params_hash = canonical_hash(params if action_params is None else action_params)
        with self.transaction():
            existing = self.connection.execute('SELECT * FROM actions WHERE profile_id=? AND method=? AND client_action_id=?',
                (profile_id, action_method, params['client_action_id'])).fetchone()
            if existing:
                if existing['params_hash'] != params_hash:
                    raise ContractError('Action ID has different parameters', kind='IDEMPOTENCY_CONFLICT', code=-32010)
                return {**json.loads(existing['result_json']), 'reused_existing_action': True}
            config_hash = canonical_hash(configuration)
            configuration_ref = self._configuration_snapshot(configuration, config_hash)
            session = self.connection.execute('SELECT s.*,w.revision FROM sessions s JOIN workspaces w ON s.workspace_id=w.id WHERE s.id=?', (params['session_id'],)).fetchone()
            if session is None:
                raise ContractError('Session not found', kind='NOT_FOUND', code=-32010)
            if session['revision'] != params['expected_workspace_revision']:
                raise ContractError('Workspace revision changed', kind='STALE_REVISION', code=-32010)
            turn_id = new_id('turn')
            result = {'turn_id': turn_id, 'state': 'queued', 'accepted': True, 'reused_existing_action': False}
            self.connection.execute('INSERT INTO turns VALUES(?,?,?,?,?,?,?)', (turn_id, params['session_id'], 'queued', None, encoded(configuration_ref), encoded(params['input']), None))
            self.connection.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?,?)', (new_id('work'), 'turn', turn_id, 'queued', None, None, 0))
            self.connection.execute('INSERT INTO actions VALUES(?,?,?,?,?,?)', (new_id('action'), profile_id, action_method, params['client_action_id'], params_hash, encoded(result)))
            producer = self.connection.execute("SELECT value FROM store_meta WHERE key='producer_id'").fetchone()[0]
            source_seq = self.connection.execute('SELECT COALESCE(MAX(source_seq),0)+1 FROM events WHERE source_id=?', (producer,)).fetchone()[0]
            self._insert_event(self.event_body('turn.accepted', producer, source_seq,
                {'configuration': configuration_ref, 'budget_summary': {
                    'model_calls_remaining': configuration.get('max_model_calls'), 'tool_calls_remaining': configuration.get('max_tool_calls'), 'wall_seconds_remaining': configuration.get('wall_seconds')}},
                workspace_id=session['workspace_id'], session_id=params['session_id'], turn_id=turn_id), producer, source_seq)
            return result

    def _configuration_snapshot(self, configuration, digest):
        existing = self.connection.execute('SELECT id FROM configuration_snapshots WHERE hash=?', (digest,)).fetchone()
        snapshot_id = existing[0] if existing else new_id('snap')
        if not existing:
            self.connection.execute('INSERT INTO configuration_snapshots VALUES(?,?,?)', (snapshot_id, digest, encoded(configuration)))
        return {'snapshot_id': snapshot_id, 'sha256': digest}

    def event_body(self, event_type, producer, sequence, attributes, **identities):
        turn_id=identities.get('turn_id')
        if turn_id and self.connection.execute("SELECT 1 FROM sqlite_master WHERE name='turn_traces'").fetchone():
            scope=self.trace_scope(turn_id,execution_id=identities.get('execution_id'))
            identities={**scope.as_dict(),**{k:v for k,v in identities.items() if v is not None}}
        body = {'schema_version': 'forge.events.v1', 'event_id': new_id('evt'), 'event_type': event_type,
            'origin': 'trusted_engine', 'producer_id': producer, 'producer_seq': str(sequence), 'occurred_at_utc': utc_now(),
            'monotonic_ns': None, 'attributes': attributes, 'artifact_refs': [], 'redaction_version': 'metadata-v1'}
        body.update({key: identities.get(key) for key in ('workspace_id', 'session_id', 'turn_id', 'run_id', 'trial_id', 'attempt_id', 'execution_id', 'trace_id', 'span_id', 'parent_span_id')})
        return body

    def trace_scope(self, turn_id, *, execution_id=None):
        from forge.observability.events import Scope
        row=self.connection.execute('SELECT * FROM turn_traces WHERE turn_id=?',(turn_id,)).fetchone()
        if row is None:
            if not self.connection.in_transaction:
                raise RuntimeError('Trace creation belongs in the acceptance transaction')
            self.connection.execute('INSERT INTO turn_traces VALUES(?,?,?,?)',(turn_id,uuid4().hex,uuid4().hex[:16],self.epoch))
            row=self.connection.execute('SELECT * FROM turn_traces WHERE turn_id=?',(turn_id,)).fetchone()
        turn=self.connection.execute('SELECT t.session_id,s.workspace_id FROM turns t JOIN sessions s ON s.id=t.session_id WHERE t.id=?',(turn_id,)).fetchone()
        execution=self.connection.execute('SELECT * FROM execution_spans WHERE execution_id=? AND turn_id=?',(execution_id,turn_id)).fetchone() if execution_id else None
        return Scope(trace_id=row['trace_id'],span_id=execution['span_id'] if execution else row['span_id'],
            parent_span_id=execution['parent_span_id'] if execution else None,execution_id=execution_id,
            identities={'turn_id':turn_id,'session_id':turn['session_id'],'workspace_id':turn['workspace_id']})

    def register_execution_scope(self, scope):
        turn_id=scope.identities.get('turn_id')
        if turn_id:
            with self.transaction():
                root=self.trace_scope(turn_id)
                if root.trace_id!=scope.trace_id:
                    raise ContractError('Execution trace identity changed',kind='EVENT_CONFLICT',code=-32010)
                self.connection.execute('INSERT INTO execution_spans VALUES(?,?,?,?,?)',
                    (scope.execution_id,turn_id,scope.trace_id,scope.span_id,scope.parent_span_id))

    def _recovery_event(self, event_type, turn_id, reason):
        row=self.connection.execute('SELECT * FROM recovery_traces WHERE turn_id=? AND owner_epoch=?',(turn_id,self.epoch)).fetchone()
        original=self.connection.execute('SELECT span_id FROM turn_traces WHERE turn_id=?',(turn_id,)).fetchone()
        links=[{'trace_id':row['original_trace_id'],'span_id':original[0]}] if row['original_trace_id'] and original else []
        producer=self.connection.execute("SELECT value FROM store_meta WHERE key='producer_id'").fetchone()[0]
        sequence=self.connection.execute('SELECT COALESCE(MAX(source_seq),0)+1 FROM events WHERE source_id=?',(producer,)).fetchone()[0]
        body=self.event_body(event_type,producer,sequence,{'original_turn_id':turn_id,'original_attempt_id':None,
            'unknown_side_effects':True,'reason':reason,'cleanup_state':'unknown','trace_links':links},
            turn_id=turn_id,trace_id=row['trace_id'],span_id=row['span_id'],parent_span_id=None)
        self._insert_event(body,producer,sequence)

    def _turn_event(self, event_type, turn_id, attributes):
        turn = self.connection.execute('SELECT t.*,s.workspace_id FROM turns t JOIN sessions s ON s.id=t.session_id WHERE t.id=?', (turn_id,)).fetchone()
        if turn is None:
            raise ContractError('Turn not found', kind='NOT_FOUND', code=-32010)
        producer = self.connection.execute("SELECT value FROM store_meta WHERE key='producer_id'").fetchone()[0]
        sequence = self.connection.execute('SELECT COALESCE(MAX(source_seq),0)+1 FROM events WHERE source_id=?', (producer,)).fetchone()[0]
        return self._insert_event(self.event_body(event_type, producer, sequence, attributes,
            workspace_id=turn['workspace_id'], session_id=turn['session_id'], turn_id=turn_id), producer, sequence)

    def _insert_event(self, body, source_id, source_seq):
        if not self.connection.in_transaction:
            raise RuntimeError('Events must be inserted within a write transaction')
        registered=self.connection.execute('SELECT producer_id FROM producers WHERE source_id=?',(source_id,)).fetchone()
        expected_producer=registered[0] if registered else source_id
        if body['producer_seq']!=str(source_seq) or body['producer_id']!=expected_producer:
            raise ContractError('Event producer identity differs from its assigned source',kind='EVENT_CONFLICT',code=-32010)
        candidate = {key: value for key, value in body.items() if key != 'store_seq'}
        validate_event({**candidate, 'store_seq': '0'})
        digest = canonical_hash(candidate)
        existing = self.connection.execute('SELECT * FROM events WHERE event_id=? OR (source_id=? AND source_seq=?)',
            (body['event_id'], source_id, source_seq)).fetchall()
        if existing:
            if len(existing) != 1 or existing[0]['hash'] != digest or existing[0]['source_id'] != source_id or existing[0]['source_seq'] != source_seq:
                raise ContractError('Event identity has conflicting content', kind='EVENT_CONFLICT', code=-32010)
            return {**json.loads(existing[0]['body_json']), 'store_seq': str(existing[0]['store_seq'])}
        cursor = self.connection.execute('INSERT INTO events(event_id,source_id,source_seq,body_json,hash) VALUES(?,?,?,?,?)',
            (body['event_id'], source_id, source_seq, encoded(candidate), digest))
        from forge.observability.projection import project
        try:
            project(self,candidate)
        except sqlite3.IntegrityError as error:
            raise ContractError('Derived event identity conflicts with an existing fact',kind='EVENT_CONFLICT',code=-32010) from error
        # store_seq is projected during reads, so immutable event bytes need no post-insert UPDATE.
        return {**candidate, 'store_seq': str(cursor.lastrowid)}

    def events(self, *, after=0, limit=100):
        if not isinstance(after, int) or after < 0 or not 1 <= limit <= 100:
            raise ContractError('Invalid event query bounds')
        return [{**json.loads(row['body_json']), 'store_seq': str(row['store_seq'])} for row in
                self.connection.execute('SELECT * FROM events WHERE store_seq>? ORDER BY store_seq LIMIT ?', (after, limit))]

    def backup(self, destination):
        destination = Path(destination).resolve()
        if destination == self.db_path or destination.exists() or self.connection.in_transaction:
            raise ValueError('Backup needs a new destination and no active write transaction')
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = destination.with_name(destination.name + '.' + uuid4().hex + '.tmp')
        try:
            with closing(sqlite3.connect(temporary)) as target:
                self.connection.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup integrity check failed')
            with temporary.open('r+b') as file:
                os.fsync(file.fileno())
            os.replace(temporary, destination)
            sync_directory(destination.parent)
            return destination
        finally:
            temporary.unlink(missing_ok=True)

    def append_event(self, body, source_id, source_seq):
        try:
            with self.transaction():
                return self._insert_event(body, source_id, source_seq)
        except ContractError as error:
            if error.kind == 'EVENT_CONFLICT':
                self.quarantine_event(body, source_id, source_seq)
            raise

    def import_observation(self, body, source_id, source_seq):
        # Explicitly demote externally supplied origin and producer ownership.
        if type(source_seq) is not int or not 1<=source_seq<2**63 or not isinstance(source_id,str) or not 1<=len(source_id)<=128:
            raise ContractError('Imported observation source bounds are invalid')
        source_id='import:'+source_id
        with self.transaction():
            self.connection.execute('INSERT OR IGNORE INTO producers VALUES(?,?)',(source_id,new_id('producer')))
            producer=self.connection.execute('SELECT producer_id FROM producers WHERE source_id=?',(source_id,)).fetchone()[0]
        return self.append_event({**body,'origin':'imported','producer_id':producer,'producer_seq':str(source_seq)},source_id,source_seq)

    def quarantine_event(self, body, source_id, source_seq):
        incoming = canonical_hash({key: value for key, value in body.items() if key != 'store_seq'})
        with self.transaction():
            row = self.connection.execute('SELECT hash FROM events WHERE event_id=? OR (source_id=? AND source_seq=?)',
                (body['event_id'], source_id, source_seq)).fetchone()
            self.connection.execute('INSERT INTO event_conflicts VALUES(?,?,?,?,?,?,?)',
                (new_id('conflict'), body['event_id'], source_id, source_seq, row[0] if row else 'missing', incoming, utc_now()))

    def claim_work_item(self, work_item_id, *, expected_version, emit_turn_event=False):
        with self.transaction():
            if self.connection.execute("SELECT 1 FROM work_items WHERE state='reconciling' LIMIT 1").fetchone():
                raise ContractError('Previous execution requires reconciliation', kind='INDETERMINATE', code=-32010)
            updated = self.connection.execute("UPDATE work_items SET state='running',owner_epoch=?,version=version+1 WHERE id=? AND state='queued' AND version=?",
                (self.epoch, work_item_id, expected_version))
            if updated.rowcount != 1:
                raise ContractError('Work item state or version changed', kind='STALE_REVISION', code=-32010)
            row = self.connection.execute('SELECT * FROM work_items WHERE id=?', (work_item_id,)).fetchone()
            if row['kind'] == 'turn':
                self.connection.execute("UPDATE turns SET state='running' WHERE id=? AND state='queued'", (row['business_id'],))
                if emit_turn_event:
                    reference = json.loads(self.connection.execute('SELECT config_json FROM turns WHERE id=?', (row['business_id'],)).fetchone()[0])
                    self._turn_event('turn.started', row['business_id'], {'configuration': reference, 'budget_summary': {
                        'model_calls_remaining': None, 'tool_calls_remaining': None, 'wall_seconds_remaining': None}})
            return dict(row)

    def mark_indeterminate(self, work_item_id, *, expected_version):
        with self.transaction():
            row = self.connection.execute('SELECT * FROM work_items WHERE id=?', (work_item_id,)).fetchone()
            if not row or row['state'] != 'reconciling' or row['version'] != expected_version:
                raise ContractError('Reconciliation state changed', kind='STALE_REVISION', code=-32010)
            if row['kind'] == 'turn':
                recovery=self.connection.execute('SELECT 1 FROM recovery_traces WHERE turn_id=? AND owner_epoch=?',(row['business_id'],self.epoch)).fetchone()
                if recovery:
                    self._recovery_event('recovery.finished',row['business_id'],'unknown_result_retained_without_replay')
                self.connection.execute("UPDATE turns SET state='finished',outcome='indeterminate' WHERE id=?", (row['business_id'],))
            self.connection.execute("UPDATE work_items SET state='finished',version=version+1 WHERE id=?", (work_item_id,))

    def finish_work_item(self, work_item_id, *, expected_version, owner_epoch, outcome):
        if owner_epoch != self.epoch:
            raise ContractError('Execution owner epoch expired', kind='INDETERMINATE', code=-32010)
        with self.transaction():
            row = self.connection.execute('SELECT * FROM work_items WHERE id=?', (work_item_id,)).fetchone()
            if not row or row['version'] != expected_version or row['owner_epoch'] != owner_epoch or row['state'] not in ('running', 'cancel_requested'):
                raise ContractError('Work item state or version changed', kind='STALE_REVISION', code=-32010)
            if row['kind'] == 'turn':
                self.connection.execute("UPDATE turns SET state='finished',outcome=? WHERE id=?", (outcome, row['business_id']))
            self.connection.execute("UPDATE work_items SET state='finished',version=version+1 WHERE id=?", (work_item_id,))

    def publish_artifact(self, content: bytes, *, origin, classification, max_bytes=104857600,
                         attempt_id=None, max_attempt_bytes=1073741824, max_diagnostic_bytes=209715200):
        if self.read_only:
            raise ContractError('Read-only diagnostic mode cannot publish artifacts', kind='INCOMPATIBLE_PROTOCOL', code=-32010)
        if len(content) > max_bytes:
            raise ContractError('Artifact quota exceeded', kind='ARTIFACT_LIMIT', code=-32010)
        artifact_id = new_id('art')
        directory = self.data_dir / 'artifacts'
        directory.mkdir(exist_ok=True, mode=0o700)
        if directory.resolve() != directory:
            raise ContractError('Artifact directory must not redirect storage')
        final = directory / artifact_id
        relative = final.relative_to(self.data_dir).as_posix()
        temporary = None
        try:
            with self.transaction():
                if attempt_id:
                    if not self.connection.execute('SELECT 1 FROM attempts WHERE id=?', (attempt_id,)).fetchone():
                        raise ContractError('Artifact attempt not found', kind='NOT_FOUND', code=-32010)
                    used = self.connection.execute('SELECT COALESCE(SUM(a.size),0) FROM artifacts a JOIN artifact_attempts s ON s.artifact_id=a.id WHERE s.attempt_id=?', (attempt_id,)).fetchone()[0]
                    if used + len(content) > max_attempt_bytes:
                        raise ContractError('Attempt artifact quota exceeded', kind='ARTIFACT_LIMIT', code=-32010)
                if classification == 'diagnostic':
                    used = self.connection.execute("SELECT COALESCE(SUM(size),0) FROM artifacts WHERE classification='diagnostic'").fetchone()[0]
                    if used + len(content) > max_diagnostic_bytes:
                        raise ContractError('Diagnostic storage budget exceeded', kind='ARTIFACT_LIMIT', code=-32010)
                with tempfile.NamedTemporaryFile(dir=directory, prefix=artifact_id, suffix='.tmp', delete=False) as file:
                    temporary = Path(file.name)
                    file.write(content)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, final)
                sync_directory(directory)
                self.connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?)',
                    (artifact_id, relative, sha256(content).hexdigest(), len(content), origin, classification))
                if attempt_id:
                    self.connection.execute('INSERT INTO artifact_attempts VALUES(?,?)', (artifact_id, attempt_id))
            return dict(self.connection.execute('SELECT * FROM artifacts WHERE id=?', (artifact_id,)).fetchone())
        except BaseException:
            # A failure after rename can leave an unreferenced object. Never delete a
            # potentially committed artifact; recovery can collect verified orphans.
            raise
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def read_artifact(self, artifact_id):
        row = self.connection.execute('SELECT * FROM artifacts WHERE id=?', (artifact_id,)).fetchone()
        if row is None:
            raise ContractError('Artifact not found', kind='NOT_FOUND', code=-32010)
        path = (self.data_dir / row['relative_storage_key']).resolve(strict=True)
        if not path.is_relative_to(self.data_dir / 'artifacts') or not path.is_file():
            raise ContractError('Artifact storage key escaped its controlled root')
        content = path.read_bytes()
        if len(content) != row['size'] or sha256(content).hexdigest() != row['sha256']:
            raise ContractError('Artifact content changed', kind='MANIFEST_MISMATCH', code=-32010)
        return content
