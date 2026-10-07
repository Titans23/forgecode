"""Implemented handlers only. The protocol catalog is not a capability claim."""
from hashlib import sha256
import json
from pathlib import Path
import sys

from forge.application.models import ContractError, METHODS, canonical_hash, validate
from forge.application.approvals import ApprovalService, ConfirmationNonces
from forge.application.connections import ConnectionService
from forge.application.session_views import create_default_session, submit, snapshot
from forge.engine.persistence import utc_now
from forge.application.sessions import session_view
from forge.engine.event_stream import EventStream, accepted_view, workspace_view


def manifest_hash():
    source = Path(__file__).parents[1].joinpath('application/_generated_contracts.json').read_text(encoding='utf-8')
    return sha256(source.encode('utf-8')).hexdigest()


class EngineMethods:
    def __init__(self, service, *, profile, interactive_approvals=False):
        self.service = service
        self.store = service.store
        self.profile = profile
        self.events = EventStream(service)
        from forge.observability.query_views import ObservationViews
        self.observations=ObservationViews(self)
        from forge.application.evaluations import EvaluationService
        self.evaluations = EvaluationService(service, executor=getattr(service, 'evaluation_executor', None))
        from forge.application.artifacts import ArtifactService
        self.artifacts=ArtifactService(service,self.evaluations)
        self.workspaces = service.workspaces
        self.workspaces.cursors = self.events
        self.approvals = ApprovalService(service)
        self.confirmations = ConfirmationNonces(self.store)
        self.connections = ConnectionService(service, self.confirmations)
        from forge.application.evaluation_client import EvaluationClient
        self.evaluation_client=EvaluationClient(self)
        from forge.application.annotations import AnnotationService
        self.annotations=AnnotationService(self)
        self.artifacts.annotations=self.annotations
        if profile == 'desktop' or interactive_approvals:
            service.approvals = self.approvals
        self.initialized = False
        self.stopping = False
        self.shutdown_mode = None
        self.handlers = {
            'system.initialize': self.initialize, 'system.health': self.health, 'system.capabilities': self.capabilities,
            'system.shutdown': self.shutdown, 'workspace.register': self.register_workspace,
            'workspace.list': self.list_workspaces, 'workspace.inspect': self.inspect_workspace,
            'workspace.files': self.workspaces.files, 'workspace.read_file': self.workspaces.read_file,
            'workspace.changes': self.workspaces.changes, 'workspace.diff': self.workspaces.diff,
            'workspace.diff_file': self.workspaces.diff_file,
            'session.create_default': lambda p: create_default_session(service, p),
            'session.submit': self.submit, 'session.snapshot': lambda p: snapshot(service, p),
            'session.create': self.service.create_session, 'session.start_turn': self.start_turn,
            'session.cancel_turn': self.service.cancel_turn, 'session.list': self.list_sessions,
            'session.get': self.get_session, 'action.get': self.get_action,
            'events.subscribe': self.events.subscribe, 'events.ack': self.events.ack,
            'events.unsubscribe': self.events.unsubscribe, 'observability.events': self.query_events,
            'observability.spans':self.observations.spans,'observability.context':self.observations.context,
            'observability.evidence':self.observations.evidence,'observability.usage':self.observations.usage,
            'observability.output':self.observations.output,'observability.timings':self.observations.timings,
            'sandbox.cleanup_status': self.cleanup_status,
            'approval.get': self.approvals.get, 'approval.list': self.list_approvals,
            'approval.prepare_decision': self.approvals.prepare, 'approval.decide': self.approvals.decide,
            'workspace.prepare_authorization': self.prepare_workspace_authorization,
            'workspace.authorize': self.authorize_workspace,
            'connection.list': self.list_connections, 'connection.prepare_set': self.connections.prepare_set,
            'connection.set': self.connections.set, 'connection.delete': self.connections.delete,
            'connection.prepare_test': self.connections.prepare_test, 'connection.test': self.test_connection,
            'credentials.inject': self.connections.inject, 'credentials.clear': self.connections.clear,
            'evaluation.validate': self.evaluations.validate, 'evaluation.create_run': self.evaluations.create_run,
            'evaluation.start': lambda p: self.evaluation_mutation('evaluation.start', p), 'evaluation.cancel': self.evaluations.cancel,
            'evaluation.retry': lambda p: self.evaluation_mutation('evaluation.retry', p), 'evaluation.report': self.evaluations.report,
            'evaluation.compare': self.evaluations.compare,
            'evaluation.templates':self.evaluation_client.templates,'evaluation.template':self.evaluation_client.template,
            'evaluation.draft':self.evaluation_client.draft,'evaluation.list':self.evaluation_client.list_runs,
            'evaluation.snapshot':self.evaluation_client.snapshot,'evaluation.comparison':self.evaluation_client.comparison,
            'evaluation.import_plan':self.evaluation_client.import_plan,'evaluation.plan_export':self.evaluation_client.export_plan,
            'failure.list':self.annotations.list,
            'failure.get':self.annotations.get,
            'failure.annotate':self.annotations.annotate,
            'failure.save_candidate':self.annotations.save_candidate,
            'failure.check_reproduction':self.annotations.check_reproduction,
            'failure.candidate':self.annotations.candidate,
            'bundle.export':self.artifacts.export,'bundle.import':self.artifacts.import_bundle,
            'artifact.describe':self.artifacts.describe,'artifact.read_chunk':self.artifacts.read_chunk,
            'bundle.prepare_import':self.artifacts.prepare_import,'bundle.prepare_export':self.artifacts.prepare_export,
        }

    def readiness(self):
        if self.store.read_only:
            return {'status': 'blocked', 'reasons': ['database_read_only']}
        if self.store.connection.execute("SELECT 1 FROM work_items WHERE state='reconciling' LIMIT 1").fetchone():
            return {'status': 'blocked', 'reasons': ['previous_execution_requires_reconciliation']}
        if self.service.mode == 'local-trusted':
            return {'status': 'degraded', 'reasons': ['local-trusted has no OS sandbox isolation',
                *(['scripted model test profile; no benchmark grades'] if self.profile == 'test' else [])]}
        return {'status': 'blocked', 'reasons': ['strict_sandbox_backend_not_ready']}

    def capabilities(self, params):
        platform = 'windows-native' if sys.platform == 'win32' and sys.getwindowsversion().build >= 22000 else (
            'linux-native' if sys.platform.startswith('linux') else 'unsupported')
        return {'platform': platform, 'sandbox': 'unavailable', 'supported_methods': sorted(self.handlers),
            'features': ['durable-actions', 'acked-event-replay', 'snapshot-high-watermark', 'batch-limit-32',
                         'scripted-model' if self.profile == 'test' and self.service.model_client_factory else 'provider-model'],
            'limits': {'frame_bytes': 1048576, 'json_depth': 32, 'artifact_chunk_bytes': 262144,
                       'event_batch_count': 100, 'event_batch_bytes': 65536}}

    def initialize(self, params):
        if params['protocol']['major'] != 1 or params['expected_manifest_hash'] != manifest_hash() or params['profile'] != self.profile:
            raise ContractError('Protocol, contract manifest or profile mismatch', kind='INCOMPATIBLE_PROTOCOL', code=-32010)
        self.initialized = True
        return {'engine_build': 'forgecode-v4-development', 'engine_epoch': self.store.epoch, 'event_schema': 'forge.events.v1',
            'db_schema': self.store.diagnostics()['schema_version'], 'protocol': {'major': 1, 'minor': 0},
            'manifest_hash': manifest_hash(), 'capabilities': self.capabilities({}), 'readiness': self.readiness()}

    def health(self, params):
        return {'engine_epoch': self.store.epoch, 'readiness': self.readiness(), 'active_work_items':
                self.store.connection.execute("SELECT COUNT(*) FROM work_items WHERE state IN ('queued','running','cancel_requested','reconciling')").fetchone()[0]}

    def cleanup_status(self, params):
        session_view(self.store, params['session_id'])
        rows = self.store.connection.execute('SELECT t.state,l.cleanup_state,l.cleanup_json FROM turns t '
            'LEFT JOIN turn_lifecycle l ON l.turn_id=t.id WHERE t.session_id=?', (params['session_id'],)).fetchall()
        states = {row['cleanup_state'] or ('pending' if row['state'] != 'finished' else 'unknown') for row in rows}
        state = 'unknown' if 'unknown' in states else 'failed' if 'residual' in states else 'pending' if states & {'pending', 'running'} else 'complete'
        counts = []
        def observe(value, depth=0):
            if depth > 8:
                return
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in ('remaining_processes', 'active_processes') and type(item) is int and 0 <= item <= 2**53-1:
                        counts.append(item)
                    elif isinstance(item, (dict, list)):
                        observe(item, depth + 1)
            elif isinstance(value, list):
                for item in value[:1000]:
                    observe(item, depth + 1)
        for row in rows:
            if row['cleanup_json']:
                observe(json.loads(row['cleanup_json']))
        return {'session_id': params['session_id'], 'state': state,
            'remaining_processes': max(counts, default=0), 'diagnostic_id': None}

    def _mutate(self, method, params, callback):
        with self.store.transaction():
            existing = self.service._existing_action(method, params)
            if existing is not None:
                return existing
            result = callback()
            validate(METHODS[method]['result_schema'], result)
            self.service._record_action(method, params, result)
            return result

    def shutdown(self, params):
        result = self._mutate('system.shutdown', params, lambda: {'state': 'draining', 'reused_existing_action': False})
        self.begin_shutdown(params['mode'], 'Engine shutdown')
        return result

    def begin_shutdown(self, mode, reason):
        self.stopping = True
        self.shutdown_mode = mode
        if mode == 'cancel':
            from forge.engine.persistence import new_id
            for row in self.store.connection.execute("SELECT id FROM runs WHERE state IN ('created','queued','running','cancel_requested')").fetchall():
                self.evaluations.cancel({'run_id': row[0], 'client_action_id': new_id('act'), 'reason': reason})
            for row in self.store.connection.execute("SELECT id FROM turns WHERE state IN ('queued','running','awaiting_approval')").fetchall():
                self.service.cancel_turn({'turn_id': row[0], 'client_action_id': new_id('act'), 'reason': reason})

    def register_workspace(self, params):
        def register():
            digest = sha256(params['selection_nonce'].encode()).hexdigest()
            if self.store.connection.execute('SELECT 1 FROM consumed_nonces WHERE token_hash=?', (digest,)).fetchone():
                raise ContractError('Directory selection was already consumed', kind='UNAUTHORIZED', code=-32010)
            result = workspace_view(self.store._register_workspace(params['path']))
            self.store.connection.execute('INSERT INTO consumed_nonces VALUES(?,?,?,?,?,?)',
                (digest, 'directory_selection', result['workspace_id'], canonical_hash({'path': params['path']}), self.store.epoch, utc_now()))
            return result
        return self._mutate('workspace.register', params, register)

    def _workspace_binding(self, params):
        row = self.service._workspace(params['workspace_id'], expected_revision=params['expected_revision'])
        return canonical_hash({'workspace': row, 'profile_id': self.service.profile_id, 'engine_epoch': self.store.epoch})

    def prepare_workspace_authorization(self, params):
        binding = self._workspace_binding(params)
        return {**self.confirmations.issue('workspace_authorization', params['workspace_id'], binding), 'binding_hash': binding}

    def authorize_workspace(self, params):
        def authorize():
            binding = self._workspace_binding(params)
            if binding != params['binding_hash']:
                raise ContractError('Workspace authorization binding changed', kind='STALE_REVISION', code=-32010)
            self.confirmations.consume(params['confirmation_token'], 'workspace_authorization', params['workspace_id'], binding)
            changed = self.store.connection.execute('UPDATE workspaces SET trust=?,revision=revision+1 WHERE id=? AND revision=?',
                ('execution_allowed' if params['allow'] else 'inspect_only', params['workspace_id'], params['expected_revision']))
            if changed.rowcount != 1:
                raise ContractError('Workspace revision changed', kind='STALE_REVISION', code=-32010)
            return workspace_view(self.service._workspace(params['workspace_id']))
        return self._mutate('workspace.authorize', params, authorize)

    def list_approvals(self, params):
        scope = params['scope']
        self.events.check_scope(scope)
        clauses = {'all': ('1=1', ()), 'workspace': ('t.session_id IN (SELECT id FROM sessions WHERE workspace_id=?)', (scope.get('id'),)),
            'session': ('t.session_id=?', (scope.get('id'),)), 'turn': ('a.turn_id=?', (scope.get('id'),))}
        if scope['kind'] not in clauses:
            raise ContractError('Approval scope is not interactive', kind='INVALID_PARAMS', code=-32010)
        where, bindings = clauses[scope['kind']]
        cursor_scope = {'collection': 'approval.list', 'scope': scope}
        after = self.events.decode_cursor(params['cursor'], cursor_scope) if 'cursor' in params else 0
        limit = params.get('limit', 100)
        rows = self.store.connection.execute('SELECT a.rowid AS position,a.id FROM approvals a JOIN turns t ON t.id=a.turn_id '
            f'WHERE ({where}) AND a.rowid>? ORDER BY a.rowid LIMIT ?', (*bindings, after, limit+1)).fetchall()
        return {'items': [self.approvals.get({'approval_id': row['id']}) for row in rows[:limit]], 'next_cursor':
            self.events.cursor(cursor_scope, rows[limit-1]['position']) if len(rows)>limit else None, 'history_gap': False}

    def inspect_workspace(self, params):
        return workspace_view(self.service._workspace(params['workspace_id']))

    def _page(self, table, params, scope, view, *, where='1=1', bindings=()):
        after = self.events.decode_cursor(params['cursor'], scope) if 'cursor' in params else 0
        limit = params.get('limit', 100)
        rows = self.store.connection.execute(f'SELECT rowid AS position,* FROM {table} WHERE ({where}) AND rowid>? ORDER BY rowid LIMIT ?',
            (*bindings, after, limit + 1)).fetchall()
        return {'items': [view(r) for r in rows[:limit]], 'next_cursor':
                self.events.cursor(scope, rows[limit-1]['position']) if len(rows) > limit else None, 'history_gap': False}

    def list_workspaces(self, params):
        return self._page('workspaces', params, {'collection': 'workspace.list'}, workspace_view)

    def list_connections(self, params):
        return self._page('connections', params, {'collection': 'connection.list'}, self.connections.view)

    def list_sessions(self, params):
        self.service._workspace(params['workspace_id'])
        return self._page('sessions', params, {'collection': 'session.list', 'workspace_id': params['workspace_id']},
            lambda r: session_view(self.store, r['id']), where='workspace_id=?', bindings=(params['workspace_id'],))

    def get_session(self, params):
        session = session_view(self.store, params['session_id'])
        page = self._page('turns', params, {'collection': 'session.get', 'session_id': params['session_id']},
            accepted_view, where='session_id=?', bindings=(params['session_id'],))
        return {'session': session, 'turns': page['items'], 'next_cursor': page['next_cursor']}

    def start_turn(self, params):
        if self.stopping:
            # Durable retries remain queryable while the Engine drains.
            existing = self.service._existing_action('session.start_turn', params)
            if existing is not None:
                return {**existing, 'reused_existing_action': True}
            raise ContractError('Engine is draining', kind='INDETERMINATE', code=-32010)
        return self.service.start_turn(params)

    def submit(self, params):
        if self.stopping and self.service._existing_action('session.submit', params) is None:
            raise ContractError('Engine is draining', kind='INDETERMINATE', code=-32010)
        return submit(self.service, params)

    def get_action(self, params):
        row = self.store.connection.execute('SELECT * FROM actions WHERE profile_id=? AND method=? AND client_action_id=?',
            (self.service.profile_id, params['method'], params['client_action_id'])).fetchone()
        if row is None:
            raise ContractError('Action not found', kind='NOT_FOUND', code=-32010)
        result = json.loads(row['result_json'])
        state = 'accepted'
        if params['method'].startswith('evaluation.'):
            if 'run_id' in result:
                current = self.evaluations.run(result['run_id'])['state']
                state = current if current in ('completed', 'failed', 'cancelled', 'indeterminate') else 'running' if current in ('running','cancel_requested') else 'accepted'
            elif 'attempt_id' in result:
                current = self.store.connection.execute('SELECT w.state,a.execution_state FROM work_items w JOIN attempts a ON a.id=w.business_id WHERE a.id=?', (result['attempt_id'],)).fetchone()
                state = 'indeterminate' if current['state']=='reconciling' else 'cancelled' if current['execution_state']=='cancelled' else 'completed' if current['execution_state']=='finished' else 'failed' if current['execution_state'] in ('error','blocked') else 'running' if current['state'] in ('running','cancel_requested') else 'accepted'
        if params['method'] == 'connection.test':
            observation = self.store.connection.execute('SELECT status,finished_at,owner_epoch FROM connection_test_results WHERE diagnostic_id=?',
                (result['diagnostic_id'],)).fetchone()
            if observation:
                result = {**result, 'status': observation['status']}
                state = ('completed' if observation['status'] == 'pass' else 'failed') if observation['finished_at'] else (
                    'running' if observation['owner_epoch'] == self.store.epoch else 'indeterminate')
        if 'turn_id' in result:
            turn = self.store.connection.execute('SELECT state,outcome FROM turns WHERE id=?', (result['turn_id'],)).fetchone()
            if turn['state'] == 'finished':
                state = turn['outcome'] if turn['outcome'] in ('completed', 'cancelled', 'indeterminate') else 'failed'
            elif turn['state'] in ('running', 'awaiting_approval', 'cancel_requested'):
                state = 'running'
            elif turn['state'] == 'reconciling':
                state = 'indeterminate'
        return {'client_action_id': params['client_action_id'], 'method': params['method'], 'state': state,
                'result_schema': METHODS[params['method']]['result_schema'], 'result': result, 'payload_hash': row['params_hash']}

    async def test_connection(self, params):
        if self.stopping and self.service._existing_action('connection.test', params) is None:
            raise ContractError('Engine is draining; new network tests are disabled', kind='INDETERMINATE', code=-32010)
        return await self.connections.test(params)

    def evaluation_mutation(self, method, params):
        if self.stopping and self.service._existing_action(method, params) is None:
            raise ContractError('Engine is draining; new evaluation work is disabled', kind='INDETERMINATE', code=-32010)
        return (self.evaluations.start if method == 'evaluation.start' else self.evaluations.retry)(params)

    def query_events(self, params):
        return self.observations.events_page(params)
