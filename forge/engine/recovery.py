"""Recovery rebuilds committed projections. It never restores a historical process handle."""
from pathlib import Path
from datetime import datetime, timezone

from forge.application.models import ContractError, validate
from forge.engine.journal_projection import JournalProjector, journal_facts
from forge.engine.persistence import encoded, new_id, utc_now
from forge.sessions.store import SessionStore


def deadline_status(value):
    if not value:
        return 'unverified'
    try:
        expiry = datetime.fromisoformat(value.replace('Z','+00:00'))
        if expiry.tzinfo is None:
            return 'unverified'
        return 'expired' if expiry <= datetime.now(timezone.utc) else 'unverified'
    except (ValueError, TypeError):
        return 'unverified'


class RecoveryService:
    def __init__(self, methods):
        self.methods, self.store, self.service = methods, methods.store, methods.service

    def target(self, params):
        if 'turn_id' in params:
            row = self.store.connection.execute('SELECT w.*,t.session_id,t.native_ref,p.profile_id,ws.canonical_path,'
                'l.cancel_state,l.cleanup_state,l.agent_deadline_utc FROM work_items w JOIN turns t ON t.id=w.business_id '
                'JOIN sessions s ON s.id=t.session_id JOIN workspaces ws ON ws.id=s.workspace_id '
                'LEFT JOIN turn_profiles p ON p.turn_id=t.id LEFT JOIN turn_lifecycle l ON l.turn_id=t.id '
                "WHERE w.kind='turn' AND t.id=?", (params['turn_id'],)).fetchone()
        else:
            row = self.store.connection.execute('SELECT w.*,d.profile_id,a.cleanup_state,ad.deadline_at AS agent_deadline_utc '
                'FROM work_items w JOIN attempts a ON a.id=w.business_id JOIN attempt_details ad ON ad.attempt_id=a.id '
                'JOIN trials t ON t.id=a.trial_id JOIN run_details d ON d.run_id=t.run_id '
                "WHERE w.kind='attempt' AND a.id=?", (params['attempt_id'],)).fetchone()
        if row is None:
            raise ContractError('Recovery target not found', kind='NOT_FOUND', code=-32010)
        if row['profile_id'] != self.service.profile_id:
            raise ContractError('Recovery target has no proven profile ownership', kind='UNAUTHORIZED', code=-32010)
        return dict(row)

    def journal(self, row):
        empty = {'state': 'unavailable','records':0,'projected_records':0,'intent_count':0,
            'unmatched_intents':[],'history_gap':False,'basis_scope':'journal'}
        if row['kind'] != 'turn' or not row.get('native_ref'):
            if row['state'] == 'queued':
                empty['state'] = 'not_started'
            return empty, None
        reader = SessionStore(Path(row['canonical_path']), data_root=self.store.data_dir/'harness')
        path = reader.directory / (row['native_ref']+'.jsonl')
        if path.absolute().resolve(strict=False) != path.absolute() or not path.absolute().is_relative_to(self.store.data_dir/'harness'):
            return {**empty,'state':'invalid'}, None
        value = journal_facts(path,native_id=row['native_ref'],project_root=row['canonical_path'],engine_turn_id=row['business_id'],store=self.store)
        offset = self.store.connection.execute('SELECT last_applied_seq FROM projection_offsets WHERE source_id=?',
            ('journal:'+row['native_ref'],)).fetchone()
        value['projected_records'] = offset[0] if offset else 0
        if value['projected_records'] > value['records'] and value['state'] not in ('unavailable','invalid'):
            value['state'] = 'invalid'
        return value, path

    def artifacts(self, row):
        if row['kind'] == 'attempt':
            query = 'SELECT a.* FROM artifacts a JOIN artifact_attempts p ON p.artifact_id=a.id WHERE p.attempt_id=?'
            binding, scope = row['business_id'], 'attempt'
        else:
            query = 'SELECT a.* FROM artifacts a JOIN artifact_profiles p ON p.artifact_id=a.id WHERE p.profile_id=?'
            binding, scope = self.service.profile_id, 'profile'
        rows = self.store.connection.execute(query+' ORDER BY a.rowid LIMIT 101',(binding,)).fetchall()
        items = []
        for artifact in rows[:100]:
            state = 'valid'
            try:
                self.store.read_artifact(artifact['id'])
            except FileNotFoundError:
                state = 'missing'
            except (OSError, ContractError):
                state = 'changed'
            items.append({'artifact_id':artifact['id'],'sha256':artifact['sha256'],'size':artifact['size'],'state':state})
        return {'scope':scope,'items':items,'history_gap':len(rows)>100}

    def inspect(self, params):
        row = self.target(params)
        journal, _ = self.journal(row)
        artifacts = self.artifacts(row)
        historical = row['owner_epoch'] is not None and row['owner_epoch'] != self.store.epoch
        cleanup = row.get('cleanup_state') or ('pending' if row['state']=='queued' else 'unknown')
        cancel = row.get('cancel_state') or ('indeterminate' if historical else 'none')
        blockers = []
        if row['state']=='reconciling': blockers.append('previous_execution_requires_reconciliation')
        if historical: blockers.append('historical_process_handle_unverified')
        if cleanup != 'clean' and row['state']!='queued': blockers.append('cleanup_unconfirmed')
        if journal['state'] not in ('complete','not_started'): blockers.append('journal_'+journal['state'])
        if journal['unmatched_intents'] or journal['history_gap']: blockers.append('tool_result_unconfirmed')
        if journal['projected_records'] != journal['records']: blockers.append('projection_incomplete')
        if any(a['state']!='valid' for a in artifacts['items']): blockers.append('artifact_integrity_unconfirmed')
        if artifacts['history_gap']: blockers.append('artifact_scan_incomplete')
        observation = self.store.connection.execute('SELECT id FROM recovery_observations WHERE work_item_id=? AND owner_epoch=?',
            (row['id'],self.store.epoch)).fetchone()
        return {'target':{'kind':row['kind'],'id':row['business_id']},'work_item_id':row['id'],'state':row['state'],
            'engine_epoch':self.store.epoch,'owner_epoch':row['owner_epoch'],'observed_at_utc':utc_now(),
            'startup_observation_id':observation[0] if observation else None,
            'unknown_side_effects':row['state']=='reconciling' or (historical and cleanup!='clean') or bool(journal['unmatched_intents']),
            'process_ownership':'cleanup_confirmed' if cleanup=='clean' else 'unverified_historical' if historical else 'not_started' if row['state']=='queued' else 'live_engine_only',
            'cleanup_state':cleanup,'cancel_state':cancel,'deadline_status':deadline_status(row.get('agent_deadline_utc')),
            'journal':journal,'artifacts':artifacts,'blockers':blockers}

    def reconcile_startup(self):
        if self.store.read_only:
            return
        # Recovery has no scheduler/ToolExecutor/model client and never changes a result.
        for work in self.store.connection.execute("SELECT id,kind,business_id FROM work_items WHERE state='reconciling' ORDER BY rowid").fetchall():
            if self.store.connection.execute('SELECT 1 FROM recovery_observations WHERE work_item_id=? AND owner_epoch=?',(work['id'],self.store.epoch)).fetchone():
                continue
            params = {work['kind']+'_id':work['business_id']}
            try:
                row = self.target(params)
            except ContractError:
                continue
            journal, path = self.journal(row)
            projection_failed = False
            if path and journal['state'] in ('complete','partial_tail'):
                try:
                    JournalProjector(self.store).project(path,row['session_id'],trusted=True)
                except Exception:
                    projection_failed = True
            report = self.inspect(params)
            if projection_failed:
                report['blockers'].append('projection_conflict_or_unavailable')
            identifier = new_id('recovery')
            report['startup_observation_id'] = identifier
            validate('recovery.inspect.result',report)
            with self.store.transaction():
                self.store.connection.execute('INSERT INTO recovery_observations VALUES(?,?,?,?,?)',
                    (identifier,work['id'],self.store.epoch,report['observed_at_utc'],encoded(report)))
