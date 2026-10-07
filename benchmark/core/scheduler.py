"""Single local evaluation worker sharing the Engine's persisted execution mutex."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from time import monotonic_ns
from uuid import uuid4

from forge.application.models import ContractError
from forge.engine.persistence import new_id, utc_now
from benchmark.core.spec import INFRASTRUCTURE_ERRORS, freeze_spec


def emit(store, event_type, attributes, *, run_id, trial_id, attempt_id=None, trace_id=None, span_id=None):
    producer = store.connection.execute("SELECT value FROM store_meta WHERE key='producer_id'").fetchone()[0]
    sequence = store.connection.execute('SELECT COALESCE(MAX(source_seq),0)+1 FROM events WHERE source_id=?', (producer,)).fetchone()[0]
    body = store.event_body(event_type, producer, sequence, attributes, run_id=run_id, trial_id=trial_id,
        attempt_id=attempt_id, trace_id=trace_id, span_id=span_id)
    body['monotonic_ns'] = str(monotonic_ns())
    return store._insert_event(body, producer, sequence)


class EvaluationScheduler:
    def __init__(self, evaluations):
        self.evaluations = evaluations
        self.store = evaluations.store
        self.active = {}

    def queue_attempt(self, trial_id):
        """Caller owns the acceptance transaction; retries are never automatic."""
        connection = self.store.connection
        number = connection.execute('SELECT COALESCE(MAX(attempt_no),0)+1 FROM attempts WHERE trial_id=?', (trial_id,)).fetchone()[0]
        attempt_id, work_id = new_id('attempt'), new_id('work')
        connection.execute('INSERT INTO attempts VALUES(?,?,?,\'queued\',NULL,\'pending\')', (attempt_id, trial_id, number))
        connection.execute('INSERT INTO work_items VALUES(?,?,?,\'queued\',NULL,NULL,0)', (work_id, 'attempt', attempt_id))
        connection.execute('INSERT INTO attempt_details(attempt_id,work_item_id) VALUES(?,?)', (attempt_id, work_id))
        connection.execute('UPDATE trials SET selected_attempt_id=? WHERE id=?', (attempt_id, trial_id))
        return {'attempt_id': attempt_id, 'work_item_id': work_id, 'state': 'queued', 'reused_existing_action': False}

    def _row(self, work_id):
        row = self.store.connection.execute('SELECT w.*,a.trial_id,a.attempt_no,t.run_id,d.trace_id,d.span_id,d.start_monotonic_ns '
            'FROM work_items w JOIN attempts a ON a.id=w.business_id JOIN trials t ON t.id=a.trial_id '
            'JOIN attempt_details d ON d.attempt_id=a.id WHERE w.id=? AND w.kind=\'attempt\'', (work_id,)).fetchone()
        if row is None:
            raise ContractError('Evaluation work item not found', kind='NOT_FOUND', code=-32010)
        return dict(row)

    def claim(self, work_id, *, expected_version):
        with self.store.transaction():
            if self.store.cleanup_blocked():
                raise ContractError('Previous cleanup remains unconfirmed', kind='INDETERMINATE', code=-32010)
            if self.store.connection.execute("SELECT 1 FROM work_items WHERE state IN ('running','reconciling') LIMIT 1").fetchone():
                raise ContractError('Another execution owns the local worker', kind='INDETERMINATE', code=-32010)
            row = self._row(work_id)
            run = self.evaluations.run(row['run_id'])
            spec = json.loads(run['spec_json'])
            if row['state'] != 'queued' or row['version'] != expected_version or run['state'] not in ('queued', 'running'):
                raise ContractError('Evaluation claim changed', kind='STALE_REVISION', code=-32010)
            spent = sum(int(a[0] or '0') for a in self.store.connection.execute(
                'SELECT d.elapsed_ns FROM attempt_details d JOIN attempts a ON a.id=d.attempt_id WHERE a.trial_id=?', (row['trial_id'],)))
            remaining = spec['budget']['trial_wall_seconds'] - spent / 1_000_000_000
            duration = min(spec['budget']['attempt_wall_seconds'], remaining)
            if duration <= 0:
                self._cancel_queued(row, reason='trial_wall_budget_exhausted', outcome='budget_exhausted')
                self.settle_run(row['run_id'])
                return None
            deadline = (datetime.now(timezone.utc) + timedelta(seconds=duration)).isoformat().replace('+00:00', 'Z')
            trace, span, now = uuid4().hex, uuid4().hex[:16], utc_now()
            self.store.connection.execute("UPDATE work_items SET state='running',owner_epoch=?,deadline=?,version=version+1 WHERE id=?",
                (self.store.epoch, deadline, work_id))
            self.store.connection.execute("UPDATE attempts SET execution_state='running' WHERE id=?", (row['business_id'],))
            self.store.connection.execute('UPDATE attempt_details SET owner_epoch=?,heartbeat_at=?,started_at=?,trace_id=?,span_id=?,deadline_at=?,start_monotonic_ns=? WHERE attempt_id=?',
                (self.store.epoch, now, now, trace, span, deadline, str(monotonic_ns()),row['business_id']))
            self.store.connection.execute("UPDATE runs SET state='running' WHERE id=?", (row['run_id'],))
            ref = self.evaluations.configuration(row['run_id'])
            emit(self.store, 'attempt.started', {'attempt_id': row['business_id'], 'attempt_no': row['attempt_no'], 'configuration': ref},
                run_id=row['run_id'], trial_id=row['trial_id'], attempt_id=row['business_id'], trace_id=trace, span_id=span)
        return {**self._row(work_id), 'duration_seconds': duration, 'spec': spec}

    def _owned(self, work_id, owner_epoch, version):
        row = self._row(work_id)
        if owner_epoch != self.store.epoch or row['owner_epoch'] != owner_epoch or row['version'] != version or row['state'] not in ('running', 'cancel_requested'):
            raise ContractError('Evaluation owner epoch or version expired', kind='INDETERMINATE', code=-32010)
        return row

    def heartbeat(self, work_id, *, owner_epoch, expected_version):
        with self.store.transaction():
            row = self._owned(work_id, owner_epoch, expected_version)
            now = utc_now()
            self.store.connection.execute('UPDATE attempt_details SET heartbeat_at=? WHERE attempt_id=?', (now, row['business_id']))
            emit(self.store, 'attempt.heartbeat', {'attempt_id': row['business_id'], 'owner_epoch': owner_epoch, 'heartbeat_at_utc': now},
                run_id=row['run_id'], trial_id=row['trial_id'], attempt_id=row['business_id'], trace_id=row['trace_id'], span_id=row['span_id'])

    def finish(self, work_id, *, owner_epoch, expected_version, execution_state, cleanup_state,
               agent_outcome=None, error_origin=None, reason=None, grade_id=None):
        if execution_state not in ('finished', 'cancelled', 'error', 'blocked') or cleanup_state not in ('clean', 'residual', 'unknown'):
            raise ContractError('Attempt terminal execution or cleanup state is invalid')
        if agent_outcome not in (None, 'completed', 'failed', 'cancelled', 'timed_out', 'budget_exhausted', 'blocked', 'indeterminate'):
            raise ContractError('Agent outcome is invalid')
        if error_origin not in (None, 'task_logic', *INFRASTRUCTURE_ERRORS):
            raise ContractError('Attempt error origin is invalid')
        if execution_state == 'cancelled' and cleanup_state != 'clean':
            execution_state,agent_outcome,error_origin = 'error','indeterminate','runner_crash'
            reason = 'Cancellation cleanup is unconfirmed; result retained without replay'
        with self.store.transaction():
            row = self._owned(work_id, owner_epoch, expected_version)
            grade = self.store.connection.execute('SELECT * FROM grades WHERE id=? AND attempt_id=?', (grade_id, row['business_id'])).fetchone() if grade_id else None
            if grade_id and grade is None:
                raise ContractError('Authoritative grade belongs to another attempt', kind='UNAUTHORIZED', code=-32010)
            if grade and grade['state'] == 'graded' and grade['result'] not in ('pass', 'fail'):
                raise ContractError('A graded result requires an independently normalized pass or fail')
            if grade and (grade['state']=='grading' or grade['state']=='grader_error' and grade['result'] is not None):
                raise ContractError('Terminal grade state or result is inconsistent')
            elapsed = str(max(0, monotonic_ns() - int(row['start_monotonic_ns'])))
            self.store.connection.execute('UPDATE attempts SET execution_state=?,error_origin=?,cleanup_state=? WHERE id=?',
                (execution_state, error_origin, cleanup_state, row['business_id']))
            self.store.connection.execute('UPDATE attempt_details SET ended_at=?,elapsed_ns=?,agent_outcome=?,authoritative_grade_id=?,terminal_reason=? WHERE attempt_id=?',
                (utc_now(), elapsed, agent_outcome, grade_id, reason, row['business_id']))
            self.store.connection.execute('UPDATE work_items SET state=?,version=version+1 WHERE id=?',
                ('finished' if cleanup_state == 'clean' else 'reconciling', work_id))
            emit(self.store, 'attempt.finished', {'attempt_id': row['business_id'], 'execution_state': execution_state,
                'cleanup_state': cleanup_state, 'agent_outcome': agent_outcome, 'error_origin': error_origin,
                'grade_state': grade['state'] if grade else 'unscored', 'grade_id': grade_id, 'reason': reason},
                run_id=row['run_id'], trial_id=row['trial_id'], attempt_id=row['business_id'], trace_id=row['trace_id'], span_id=row['span_id'])
            self.settle_run(row['run_id'])

    def _cancel_queued(self, row, *, reason, outcome='cancelled'):
        self.store.connection.execute("UPDATE attempts SET execution_state='cancelled',cleanup_state='clean' WHERE id=?", (row['business_id'],))
        self.store.connection.execute('UPDATE attempt_details SET ended_at=?,elapsed_ns=\'0\',agent_outcome=?,terminal_reason=? WHERE attempt_id=?',
            (utc_now(), outcome, reason, row['business_id']))
        self.store.connection.execute("UPDATE work_items SET state='finished',version=version+1 WHERE id=?", (row['id'],))

    def cancel(self, run_id, reason):
        for row in self.store.connection.execute('SELECT w.id FROM work_items w JOIN attempts a ON a.id=w.business_id '
                'JOIN trials t ON t.id=a.trial_id WHERE t.run_id=? AND w.state IN (\'queued\',\'running\')', (run_id,)).fetchall():
            work = self._row(row[0])
            if work['state'] == 'queued':
                self._cancel_queued(work, reason=reason)
            else:
                self.store.connection.execute("UPDATE work_items SET state='cancel_requested' WHERE id=?", (work['id'],))
                task = self.active.get(work['id'])
                if task:
                    task.cancel()
        self.settle_run(run_id)

    def settle_run(self, run_id):
        states = {r[0] for r in self.store.connection.execute('SELECT w.state FROM work_items w JOIN attempts a ON a.id=w.business_id JOIN trials t ON t.id=a.trial_id WHERE t.run_id=?', (run_id,))}
        run = self.evaluations.run(run_id)
        if 'reconciling' in states:
            state = 'indeterminate'
        elif states & {'queued', 'running', 'cancel_requested'}:
            return
        elif run['state'] == 'cancel_requested':
            state = 'cancelled'
        elif states:
            errors = self.store.connection.execute('SELECT 1 FROM trials t JOIN attempts a ON a.id=t.selected_attempt_id '
                'LEFT JOIN attempt_details d ON d.attempt_id=a.id LEFT JOIN grades g ON g.id=d.authoritative_grade_id '
                "WHERE t.run_id=? AND (a.execution_state IN ('blocked','error') OR g.state='grader_error') LIMIT 1", (run_id,)).fetchone()
            state = 'failed' if errors else 'completed'
        else:
            state = 'cancelled' if run['state'] == 'cancel_requested' else run['state']
        self.store.connection.execute('UPDATE runs SET state=? WHERE id=?', (state, run_id))

    def reconcile(self, work_id, *, expected_version, cleanup_observation):
        """Trusted native/runner cleanup callback; deliberately unavailable as an RPC."""
        with self.store.transaction():
            row = self._row(work_id)
            if row['state'] != 'reconciling' or row['version'] != expected_version:
                raise ContractError('Reconciliation version changed', kind='STALE_REVISION', code=-32010)
            if cleanup_observation.get('owner_epoch') != row['owner_epoch'] or cleanup_observation.get('work_item_id') != work_id or cleanup_observation.get('state') != 'clean' or type(cleanup_observation.get('remaining_processes')) is not int or cleanup_observation['remaining_processes'] != 0:
                raise ContractError('Owned cleanup is unconfirmed', kind='INDETERMINATE', code=-32010)
            self.store.connection.execute("UPDATE attempts SET execution_state='error',error_origin='runner_crash',cleanup_state='clean' WHERE id=?", (row['business_id'],))
            self.store.connection.execute("UPDATE attempt_details SET ended_at=?,agent_outcome='indeterminate',terminal_reason='interrupted_result_retained_without_replay' WHERE attempt_id=?", (utc_now(),row['business_id']))
            self.store.connection.execute("UPDATE work_items SET state='finished',version=version+1 WHERE id=?", (work_id,))
            recovery = self.store.connection.execute('SELECT * FROM attempt_recovery WHERE attempt_id=? AND owner_epoch=?', (row['business_id'],self.store.epoch)).fetchone()
            if recovery:
                emit(self.store,'recovery.finished',{'original_turn_id':None,'original_attempt_id':row['business_id'],
                    'unknown_side_effects':True,'reason':'owned_cleanup_confirmed_unknown_result_retained','cleanup_state':'clean',
                    'trace_links':[{'trace_id':row['trace_id'],'span_id':row['span_id']}] if row['trace_id'] else []},
                    run_id=row['run_id'],trial_id=row['trial_id'],attempt_id=row['business_id'],trace_id=recovery['trace_id'],span_id=recovery['span_id'])
            self.settle_run(row['run_id'])

    async def execute(self, attempt_id):
        row = self.store.connection.execute('SELECT w.id,w.version FROM work_items w WHERE w.kind=\'attempt\' AND w.business_id=?', (attempt_id,)).fetchone()
        work = self.claim(row['id'], expected_version=row['version'])
        if work is None:
            return
        executor = self.evaluations.executor
        if executor is None:
            self.finish(work['id'], owner_epoch=self.store.epoch, expected_version=work['version'],
                execution_state='blocked', cleanup_state='clean', agent_outcome='blocked',
                error_origin='runner_unavailable', reason='No compatible official evaluation executor is installed')
            return
        try:
            _, values, _ = freeze_spec(self.store, work['spec'])
            issues = self.evaluations.compatibility(work['spec'], values)
            if issues:
                raise ContractError('Executor compatibility changed',kind='UNSUPPORTED_PLATFORM',code=-32010)
        except ContractError as error:
            self.finish(work['id'],owner_epoch=self.store.epoch,expected_version=work['version'],execution_state='blocked',
                cleanup_state='clean',agent_outcome='blocked',error_origin='environment_setup',reason=error.kind)
            return
        # Trusted executor owns actual runner termination, artifacts and grader; no RPC-supplied command or grade.
        task = asyncio.create_task(executor.execute(work, self))
        self.active[work['id']] = task
        from forge.engine.lifecycle import Deadline
        from time import monotonic
        deadline = Deadline(work['duration_seconds'],monotonic(),datetime.fromisoformat(work['deadline'].replace('Z','+00:00')))
        try:
            while not task.done():
                remaining = deadline.remaining()
                if remaining <= 0:
                    raise TimeoutError('Attempt wall budget exhausted')
                await asyncio.wait((task,), timeout=min(5, remaining))
                if not task.done() and self._row(work['id'])['state'] in ('running','cancel_requested'):
                    self.heartbeat(work['id'],owner_epoch=self.store.epoch,expected_version=work['version'])
            task.result()
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.wait((task,),timeout=2)
            state = self._row(work['id'])['state']
            if state in ('running', 'cancel_requested'):
                self.finish(work['id'], owner_epoch=self.store.epoch, expected_version=work['version'],
                    execution_state='cancelled', cleanup_state='unknown', agent_outcome='cancelled', reason='Executor cancellation cleanup is unconfirmed')
        except Exception as error:
            task.cancel()
            await asyncio.wait((task,),timeout=2)
            state = self._row(work['id'])['state']
            if state in ('running', 'cancel_requested'):
                self.finish(work['id'], owner_epoch=self.store.epoch, expected_version=work['version'],
                    execution_state='error', cleanup_state='unknown', agent_outcome='timed_out' if isinstance(error, TimeoutError) else 'failed',
                    error_origin='runner_crash', reason=type(error).__name__)
        finally:
            self.active.pop(work['id'], None)
            if self._row(work['id'])['state'] in ('running', 'cancel_requested'):
                self.finish(work['id'], owner_epoch=self.store.epoch, expected_version=work['version'], execution_state='error',
                    cleanup_state='unknown', error_origin='runner_crash', reason='Executor returned without terminal evidence')
