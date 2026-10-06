"""Implemented handlers only. The protocol catalog is not a capability claim."""
from hashlib import sha256
import json
from pathlib import Path
import sys

from forge.application.models import ContractError, METHODS, validate
from forge.application.sessions import session_view
from forge.engine.event_stream import EventStream, accepted_view, workspace_view


def manifest_hash():
    source = Path(__file__).parents[1].joinpath('application/_generated_contracts.json').read_text(encoding='utf-8')
    return sha256(source.encode('utf-8')).hexdigest()


class EngineMethods:
    def __init__(self, service, *, profile):
        self.service = service
        self.store = service.store
        self.profile = profile
        self.events = EventStream(service)
        self.initialized = False
        self.stopping = False
        self.shutdown_mode = None
        self.handlers = {
            'system.initialize': self.initialize, 'system.health': self.health, 'system.capabilities': self.capabilities,
            'system.shutdown': self.shutdown, 'workspace.register': self.register_workspace,
            'workspace.list': self.list_workspaces, 'workspace.inspect': self.inspect_workspace,
            'session.create': self.service.create_session, 'session.start_turn': self.start_turn,
            'session.cancel_turn': self.service.cancel_turn, 'session.list': self.list_sessions,
            'session.get': self.get_session, 'action.get': self.get_action,
            'events.subscribe': self.events.subscribe, 'events.ack': self.events.ack,
            'events.unsubscribe': self.events.unsubscribe, 'observability.events': self.query_events,
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
                self.store.connection.execute("SELECT COUNT(*) FROM work_items WHERE state IN ('running','cancel_requested','reconciling')").fetchone()[0]}

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
        self.stopping = True
        self.shutdown_mode = params['mode']
        if params['mode'] == 'cancel':
            from forge.engine.persistence import new_id
            for row in self.store.connection.execute("SELECT id FROM turns WHERE state IN ('queued','running','awaiting_approval')").fetchall():
                self.service.cancel_turn({'turn_id': row[0], 'client_action_id': new_id('act'), 'reason': 'Engine shutdown'})
        return result

    def register_workspace(self, params):
        return self._mutate('workspace.register', params,
                            lambda: workspace_view(self.store._register_workspace(params['path'])))

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

    def get_action(self, params):
        row = self.store.connection.execute('SELECT * FROM actions WHERE profile_id=? AND method=? AND client_action_id=?',
            (self.service.profile_id, params['method'], params['client_action_id'])).fetchone()
        if row is None:
            raise ContractError('Action not found', kind='NOT_FOUND', code=-32010)
        result = json.loads(row['result_json'])
        state = 'accepted'
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

    def query_events(self, params):
        scope = params['scope']
        self.events.check_scope(scope)
        sequence = self.events.decode_cursor(params['cursor'], scope) if 'cursor' in params else 0
        high = self.store.connection.execute('SELECT COALESCE(MAX(store_seq),0) FROM events').fetchone()[0]
        if sequence > high:
            raise ContractError('Cursor is ahead of stored events', kind='INVALID_CURSOR', code=-32010)
        items = []
        for event in self.store.events(after=sequence):
            sequence = int(event['store_seq'])
            if scope['kind'] == 'all' or event[scope['kind'] + '_id'] == scope['id']:
                items.append(event)
            if len(items) >= params.get('limit', 100):
                break
        return {'items': items, 'next_cursor': self.events.cursor(scope, sequence) if sequence < high else None, 'history_gap': False}
