"""Metadata projection of the existing Journal; replay never executes tools."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from forge.application.models import ContractError
from forge.engine.persistence import encoded, new_id, utc_now
from forge.sessions.store import SessionStore


class JournalProjector:
    def __init__(self, store):
        self.store = store

    def project(self, path: Path, session_id: str, *, trusted=False):
        self.current_event = None
        try:
            return self._project(path, session_id, trusted=trusted)
        except ContractError as error:
            if error.kind == 'EVENT_CONFLICT' and self.current_event:
                self.store.quarantine_event(*self.current_event)
            raise

    def _project(self, path: Path, session_id: str, *, trusted):
        path = Path(path).resolve(strict=True)
        if trusted and not path.is_relative_to((self.store.data_dir/'harness').resolve()):
            raise ContractError('Trusted Journal must be in the private Harness directory',kind='POLICY_DENIED',code=-32010)
        session = self.store.connection.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
        if session is None:
            raise ContractError('Projection session not found', kind='NOT_FOUND', code=-32010)
        reader = SessionStore(path.parent, data_root=self.store.data_dir)
        records = reader._read_records(path)
        if not records:
            return 0
        source = 'journal:' + records[0]['session_id']
        applied = 0
        with self.store.transaction():
            self.store.connection.execute('INSERT OR IGNORE INTO producers VALUES(?,?)', (source, new_id('producer')))
            producer = self.store.connection.execute('SELECT producer_id FROM producers WHERE source_id=?', (source,)).fetchone()[0]
            offset = self.store.connection.execute('SELECT last_applied_seq FROM projection_offsets WHERE source_id=?', (source,)).fetchone()
            last = offset[0] if offset else 0
            for record in records:
                sequence = record['sequence']
                # Validate referenced payload integrity even when replaying old metadata.
                payload = reader._payload(record, path)
                digest = sha256(encoded({'record': record, 'payload': payload}).encode('utf-8')).hexdigest()
                attributes = {'native_type': record['type'], 'native_uuid': record['uuid'],
                    'legacy_session_id': record['session_id'], 'record_hash': digest}
                body = self.store.event_body('journal.projected', producer, sequence, attributes,
                    workspace_id=session['workspace_id'], session_id=session_id)
                body['origin']='trusted_engine' if trusted else 'imported'
                if trusted and record['type']=='observation':
                    observed=payload.get('event')
                    if not isinstance(observed,dict) or observed.get('origin') not in ('trusted_engine','trusted_bridge','grader_adapter'):
                        raise ContractError('Private observation has invalid provenance',kind='POLICY_DENIED',code=-32010)
                    if observed.get('session_id')!=session_id or observed.get('workspace_id')!=session['workspace_id']:
                        raise ContractError('Observation session ownership mismatch',kind='POLICY_DENIED',code=-32010)
                    turn=self.store.connection.execute('SELECT session_id FROM turns WHERE id=?',(observed.get('turn_id'),)).fetchone()
                    if not turn or turn[0]!=session_id:
                        raise ContractError('Observation turn ownership mismatch',kind='POLICY_DENIED',code=-32010)
                    body={**observed,'producer_id':producer,'producer_seq':str(sequence)}
                body['event_id'] = 'evt-' + record['uuid']
                body['occurred_at_utc'] = datetime.fromisoformat(record['timestamp']).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
                self.current_event = (body, source, sequence)
                if sequence <= last:
                    self.store._insert_event(body, source, sequence)
                    continue
                if sequence != last + 1:
                    raise ContractError('Journal projection sequence has a gap', kind='EVENT_CONFLICT', code=-32010)
                self.store._insert_event(body, source, sequence)
                self.store.connection.execute('INSERT INTO event_provenance VALUES(?,?,?,?,?)',
                    (body['event_id'],source,sequence,digest,'internal' if trusted else 'imported'))
                last = sequence
                applied += 1
            self.store.connection.execute('INSERT INTO projection_offsets VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET last_applied_seq=excluded.last_applied_seq', (source, last))
        return applied
