"""Run acceptance and reports over actual immutable plans and independent grades."""
from datetime import datetime, timedelta, timezone
import json

from benchmark.core.metrics import compare_specs, compute_metrics
from benchmark.core.scheduler import EvaluationScheduler, emit
from benchmark.core.spec import freeze_spec, INFRASTRUCTURE_ERRORS
from forge.application.models import ContractError, METHODS, canonical_hash, validate
from forge.engine.persistence import encoded, new_id, utc_now


def artifact_view(row):
    return {'artifact_id': row['id'], 'sha256': row['sha256'], 'size_bytes': row['size'],
        'media_type': 'application/json', 'redaction_version': 'metadata-v1', 'available': True}


class EvaluationService:
    def __init__(self, service, *, executor=None):
        self.service, self.store, self.executor = service, service.store, executor
        self.scheduler = EvaluationScheduler(self)

    def run(self, run_id):
        row = self.store.connection.execute('SELECT r.* FROM runs r JOIN run_details d ON d.run_id=r.id '
            'WHERE r.id=? AND d.profile_id=?', (run_id, self.service.profile_id)).fetchone()
        if row is None:
            raise ContractError('Evaluation run not found in this profile', kind='NOT_FOUND', code=-32010)
        return dict(row)

    def configuration(self, run_id):
        row = self.store.connection.execute('SELECT s.id,s.hash FROM run_details d JOIN configuration_snapshots s ON s.id=d.configuration_id WHERE d.run_id=?', (run_id,)).fetchone()
        return {'snapshot_id': row['id'], 'sha256': row['hash']}

    def validate(self, params):
        spec, values, digest = freeze_spec(self.store, params['spec'])
        issues = self.executor.validate(spec, values) if self.executor else [
            {'task_id': None, 'kind': 'runner_unavailable', 'message': 'Plan may be saved/exported; no compatible official executor is installed.'}]
        ticket = new_id('ticket')
        expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat().replace('+00:00', 'Z')
        result = {'validation_ticket': ticket, 'spec_hash': digest, 'compatible': not issues, 'issues': issues}
        validate('evaluation.validate.result', result)
        with self.store.transaction():
            self.store.connection.execute('INSERT INTO validation_tickets VALUES(?,?,?,?,NULL)', (ticket, self.service.profile_id, digest, expires))
        return result

    def _mutate(self, method, params, callback):
        validate(METHODS[method]['request_schema'], params)
        with self.store.transaction():
            existing = self.service._existing_action(method, params)
            if existing is not None:
                return {**existing, 'reused_existing_action': True}
            result = callback()
            validate(METHODS[method]['result_schema'], result)
            self.service._record_action(method, params, result)
            return result

    def receipt(self, run_id):
        run = self.run(run_id)
        return {'run_id': run_id, 'spec_hash': run['spec_hash'], 'state': run['state'], 'reused_existing_action': False,
            'trial_ids': [r[0] for r in self.store.connection.execute('SELECT id FROM trials WHERE run_id=? ORDER BY rowid', (run_id,))]}

    def create_run(self, params):
        def create():
            spec, _, digest = freeze_spec(self.store, params['spec'])
            ticket = self.store.connection.execute('SELECT * FROM validation_tickets WHERE id=? AND profile_id=?',
                (params['validation_ticket'], self.service.profile_id)).fetchone()
            if digest != params['spec_hash'] or not ticket or ticket['spec_hash'] != digest or ticket['expires_at'] <= utc_now() or ticket['consumed_run_id']:
                raise ContractError('Validation ticket is stale, consumed or bound to a different plan', kind='STALE_REVISION', code=-32010)
            run_id = new_id('run')
            self.store.connection.execute('INSERT OR IGNORE INTO experiments VALUES(?,?,?)',
                (spec['experiment_id'], 'Explicit immutable RunSpec experiment', 'paired_task_set'))
            self.store.connection.execute("INSERT INTO runs VALUES(?,?,?,?,'created')", (run_id, spec['experiment_id'], digest, encoded(spec)))
            ref = self.store._configuration_snapshot(spec, digest)
            self.store.connection.execute('INSERT INTO run_details VALUES(?,?,?,?)', (run_id, self.service.profile_id, ref['snapshot_id'], utc_now()))
            for task_id in spec['dataset']['task_ids']:
                for repeat in range(spec['protocol']['repeats']):
                    trial_id = new_id('trial')
                    self.store.connection.execute('INSERT INTO trials VALUES(?,?,?,?,?,NULL)',
                        (trial_id, run_id, task_id, spec['dataset']['task_revisions'][task_id], repeat))
                    emit(self.store, 'trial.planned', {'trial_id': trial_id, 'configuration': ref, 'task_id': task_id, 'repeat_index': repeat},
                        run_id=run_id, trial_id=trial_id)
            self.store.connection.execute('UPDATE validation_tickets SET consumed_run_id=? WHERE id=?', (run_id, ticket['id']))
            return self.receipt(run_id)
        return self._mutate('evaluation.create_run', params, create)

    def start(self, params):
        def start():
            run = self.run(params['run_id'])
            if run['state'] != 'created':
                if run['state'] in ('queued', 'running'):
                    return self.receipt(run['id'])
                raise ContractError('Run cannot restart; use an explicitly authorized infrastructure retry', kind='STALE_REVISION', code=-32010)
            # Revision checks are repeated; accepting a run never authorizes paid requests.
            freeze_spec(self.store, json.loads(run['spec_json']))
            for trial in self.store.connection.execute('SELECT id FROM trials WHERE run_id=? ORDER BY rowid', (run['id'],)).fetchall():
                self.scheduler.queue_attempt(trial[0])
            self.store.connection.execute("UPDATE runs SET state='queued' WHERE id=?", (run['id'],))
            return self.receipt(run['id'])
        return self._mutate('evaluation.start', params, start)

    def cancel(self, params):
        def cancel():
            run = self.run(params['run_id'])
            if run['state'] not in ('completed', 'failed', 'cancelled'):
                self.store.connection.execute("UPDATE runs SET state='cancel_requested' WHERE id=?", (run['id'],))
                self.scheduler.cancel(run['id'], params['reason'])
            return self.receipt(run['id'])
        return self._mutate('evaluation.cancel', params, cancel)

    def retry(self, params):
        def retry():
            trial = self.store.connection.execute('SELECT * FROM trials WHERE id=?', (params['trial_id'],)).fetchone()
            if trial is None:
                raise ContractError('Trial not found', kind='NOT_FOUND', code=-32010)
            run = self.run(trial['run_id'])
            spec = json.loads(run['spec_json'])
            previous = self.store.connection.execute('SELECT a.*,w.state AS work_state,d.terminal_reason FROM attempts a '
                'JOIN attempt_details d ON d.attempt_id=a.id JOIN work_items w ON w.id=d.work_item_id '
                'WHERE a.id=?', (trial['selected_attempt_id'],)).fetchone()
            if run['state'] in ('cancel_requested', 'cancelled', 'indeterminate') or previous is None or previous['work_state'] != 'finished' or previous['cleanup_state'] != 'clean':
                raise ContractError('Attempt must be terminal and cleanup confirmed before retry', kind='INDETERMINATE', code=-32010)
            allowed = spec['protocol'].get('infrastructure_retry_categories', [])
            if previous['error_origin'] not in allowed or previous['error_origin'] not in INFRASTRUCTURE_ERRORS:
                raise ContractError('This failure category was not authorized in the immutable RunSpec', kind='UNAUTHORIZED', code=-32010)
            if previous['attempt_no'] >= spec['protocol']['max_infrastructure_attempts']:
                raise ContractError('Maximum infrastructure attempts reached', kind='UNAUTHORIZED', code=-32010)
            freeze_spec(self.store, spec)
            result = self.scheduler.queue_attempt(trial['id'])
            self.store.connection.execute("UPDATE runs SET state='queued' WHERE id=?", (run['id'],))
            return result
        return self._mutate('evaluation.retry', params, retry)

    def report_data(self, run_id):
        run = self.run(run_id)
        trials = [dict(r) for r in self.store.connection.execute('SELECT * FROM trials WHERE run_id=? ORDER BY rowid', (run_id,))]
        attempts = [dict(r) for r in self.store.connection.execute('SELECT a.*,d.agent_outcome,d.owner_epoch,d.terminal_reason,d.trace_id,d.span_id,d.elapsed_ns,d.authoritative_grade_id,'
            'g.state AS grade_state,g.result AS grade_result,g.reward AS grade_reward '
            'FROM attempts a JOIN trials t ON t.id=a.trial_id JOIN attempt_details d ON d.attempt_id=a.id '
            'LEFT JOIN grades g ON g.id=d.authoritative_grade_id WHERE t.run_id=? ORDER BY a.rowid', (run_id,))]
        event_types, pending = {}, {}
        for row in self.store.connection.execute('SELECT body_json FROM events WHERE json_extract(body_json,\'$.run_id\')=?', (run_id,)):
            event = json.loads(row[0])
            if event['origin'] in ('trusted_engine', 'trusted_bridge', 'grader_adapter') and event['attempt_id']:
                event_types.setdefault(event['attempt_id'], set()).add(event['event_type'])
                boundaries = pending.setdefault(event['attempt_id'], set())
                kind = event['event_type']
                identity = event['attributes'].get('model_request_id') if kind.startswith('model.request.') else event.get('execution_id') if kind.startswith('tool.') else event['attributes'].get('evidence_id') if kind.startswith('verification.') else None
                if identity and kind.endswith('.started'):
                    boundaries.add(identity)
                elif identity and kind.endswith(('.finished','.failed')):
                    boundaries.discard(identity)
        for attempt in attempts:
            required = {'attempt.started', 'attempt.finished'}
            if attempt['grade_state'] in ('graded', 'grader_error'):
                required |= {'grade.finished'}
            attempt['trace_complete'] = required <= event_types.get(attempt['id'], set()) and not pending.get(attempt['id'])
        requests = (dict(r) for r in self.store.connection.execute('SELECT m.id,l.cost,l.cost_quality AS quality,l.currency '
            'FROM model_requests m JOIN usage_ledger l ON l.request_id=m.id JOIN attempt_requests ar ON ar.request_id=m.id '
            'JOIN attempts a ON a.id=ar.attempt_id JOIN trials t ON t.id=a.trial_id WHERE t.run_id=?', (run_id,)))
        missing = []
        for row in self.store.connection.execute('SELECT x.artifact_id FROM artifact_attempts x JOIN attempts a ON a.id=x.attempt_id JOIN trials t ON t.id=a.trial_id WHERE t.run_id=?', (run_id,)):
            try:
                self.store.read_artifact(row[0])
            except (OSError, ContractError):
                missing.append(row[0])
        return {'schema_version': 'forge.eval.report.v1', 'run_id': run_id, 'state': run['state'], 'spec_hash': run['spec_hash'],
            'model_mode': json.loads(run['spec_json'])['model_mode'], 'metrics': compute_metrics(trials, attempts, requests),
            'trials': trials, 'attempts': attempts, 'missing_evidence': missing,
            'score_authority': 'independent_grader_only', 'official_metrics_preserved_separately': True}

    def report(self, params):
        data = self.report_data(params['run_id'])
        artifact = self.store.publish_artifact(encoded(data).encode('utf-8'), origin='trusted_engine', classification='metadata', max_bytes=10485760)
        return {'run_id': params['run_id'], 'report_artifact': artifact_view(artifact), 'missing_evidence': data['missing_evidence'][:10000]}

    def compare(self, params):
        runs = [self.run(identity) for identity in params['run_ids']]
        result = compare_specs([json.loads(run['spec_json']) for run in runs])
        data = {'schema_version': 'forge.eval.comparison.v1', 'protocol': params['protocol'], **result,
            'runs': [self.report_data(run['id']) for run in runs], 'effect_estimate': None,
            'uncertainty': 'No statistical improvement inferred; repeated trials require task-clustered analysis.'}
        artifact = self.store.publish_artifact(encoded(data).encode('utf-8'), origin='trusted_engine', classification='metadata', max_bytes=10485760)
        return {**result, 'report_artifact': artifact_view(artifact)}
