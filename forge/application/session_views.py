"""Durable display messages from the real Harness stream, with bounded storage."""
import json

from forge.application.models import ContractError
from forge.application.sessions import session_view
from forge.engine.event_stream import accepted_view
from forge.runtime.state import ModelTextDelta, ToolExecutionStarted, ToolExecutionCompleted, TurnCompleted


class TurnMessages:
    def __init__(self, store, turn_id, credential):
        self.store, self.turn_id = store, turn_id
        self.secret = credential or ''
        self.pending = ''
        self.used = 0
        self.sequence = 0

    def append(self, kind, text, *, tool_name=None, status=None):
        if self.sequence >= 10000 or self.used >= 1048576:
            return
        if self.secret:
            text = text.replace(self.secret, '[credential redacted]')
        text = text[:min(2048, 1048576-self.used)]
        self.sequence += 1
        self.used += len(text)
        with self.store.transaction():
            self.store.connection.execute('INSERT INTO turn_messages VALUES(?,?,?,?,?,?)',
                (self.turn_id, self.sequence, kind, text, tool_name, status))
            self.store._turn_event('session.message', self.turn_id, {'sequence': self.sequence, 'kind': kind})

    def flush(self, final=False):
        # Retain enough characters to redact a key split across provider deltas.
        retain = len(self.secret) if not final else 0
        if len(self.pending) <= retain:
            return
        redacted = self.pending.replace(self.secret, '[credential redacted]') if self.secret else self.pending
        cut = len(redacted)-retain
        while cut > 0:
            chunk = redacted[:min(cut, 2048)]
            self.append('assistant', chunk)
            redacted = redacted[len(chunk):]
            cut -= len(chunk)
        self.pending = redacted

    def observe(self, event):
        if isinstance(event, ModelTextDelta):
            self.pending += event.text
            self.flush()
        elif isinstance(event, (ToolExecutionStarted, ToolExecutionCompleted)):
            self.flush(final=True)
            call = event.tool_call
            status = 'running' if isinstance(event, ToolExecutionStarted) else ('completed' if event.result.success else 'failed')
            self.append('tool', call.name, tool_name=call.name, status=status)
        elif isinstance(event, TurnCompleted):
            self.flush(final=True)
            # The final answer can exist without text deltas (finish_task).
            self.append('result', event.result.text, status=event.result.status)


def snapshot(service, params):
    store = service.store
    store.connection.execute('BEGIN')
    try:
        session = session_view(store, params['session_id'])
        rows = store.connection.execute('SELECT * FROM turns WHERE session_id=? ORDER BY rowid DESC LIMIT 101', (session['session_id'],)).fetchall()
        turn_id = params.get('turn_id') or (rows[0]['id'] if rows else None)
        if turn_id and not store.connection.execute('SELECT 1 FROM turns WHERE id=? AND session_id=?', (turn_id, session['session_id'])).fetchone():
            raise ContractError('Turn belongs to another session', kind='UNAUTHORIZED', code=-32010)
        after = params.get('after_sequence', 0)
        messages = store.connection.execute('SELECT sequence,kind,text,tool_name,status FROM turn_messages WHERE turn_id=? AND sequence>? '
            'ORDER BY sequence LIMIT 101', (turn_id, after)).fetchall() if turn_id else []
        configuration = json.loads(store.connection.execute('SELECT normalized_json FROM configuration_snapshots WHERE id=?',
            (session['configuration']['snapshot_id'],)).fetchone()[0])
        cursor = store.connection.execute('SELECT COALESCE(MAX(store_seq),0) FROM events').fetchone()[0]
        limits = store.connection.execute('SELECT COUNT(*),COALESCE(SUM(length(text)),0) FROM turn_messages WHERE turn_id=?', (turn_id,)).fetchone()
        return {'session': session, 'turns': [accepted_view(r) for r in rows[:100]], 'has_more_turns': len(rows)>100,
            'turn_id': turn_id, 'messages': [dict(r) for r in messages[:100]], 'has_more_messages': len(messages)>100,
            'message_limit_reached': limits[0] >= 10000 or limits[1] >= 1048576,
            'snapshot_cursor': str(cursor), 'event_cursor': service.workspaces.cursors.cursor({'kind': 'all'}, cursor),
            'connection_id': configuration['connection_id']}
    finally:
        store.connection.execute('ROLLBACK')


def create_default_session(service, params):
    existing = service._existing_action('session.create_default', params)
    if existing:
        return existing
    root = service._workspace(params['workspace_id'], expected_revision=params['expected_workspace_revision'])['canonical_path']
    identity = params['client_action_id'][4:]
    policy_id, budget_id = 'policy-'+identity, 'budget-'+identity
    if not service.store.connection.execute('SELECT 1 FROM policies WHERE id=?', (policy_id,)).fetchone():
        service.put_policy({'schema_version': 'forge.sandbox.policy.v1', 'policy_id': policy_id, 'workspace_id': params['workspace_id'],
            'filesystem': {'read_mode': 'backend_default_with_protected_paths', 'read_roots': [root], 'write_roots': [root],
                'protected_paths': [], 'deny_overrides_allow': True, 'reject_unsafe_links': True},
            'network': {'mode': 'deny_direct', 'allowed_domains': [], 'dns_isolation_required': False},
            'limits': {'memory_bytes': None, 'disk_bytes': None, 'pids': None, 'wall_time_seconds': 300,
                'command_output_bytes': 1048576, 'session_artifact_bytes': 104857600},
            'environment_keys': [], 'fallback': 'deny', 'session_mutation': 'replace_session'})
    with service.store.transaction():
        service.store.connection.execute('INSERT OR IGNORE INTO budget_profiles VALUES(?,?)',
            (budget_id, json.dumps({'max_model_calls': 16, 'max_tool_calls': 64, 'wall_seconds': 300})))
    return service.create_session({**params, 'policy_id': policy_id, 'budget_profile_id': budget_id},
        action_method='session.create_default', action_params=params)


def submit(service, params):
    existing = service._existing_action('session.submit', params)
    if existing:
        return {**existing, 'reused_existing_action': True}
    session = session_view(service.store, params['session_id'])
    config = json.loads(service.store.connection.execute('SELECT normalized_json FROM configuration_snapshots WHERE id=?',
        (session['configuration']['snapshot_id'],)).fetchone()[0])
    request = {**params, 'expected_workspace_revision': config['workspace_revision'],
        **{k: config[k] for k in ('connection_id', 'policy_id', 'budget_profile_id')}}
    if session['active_turn_id']:
        raise ContractError('Session has an active turn', kind='STALE_REVISION', code=-32010)
    return service.start_turn(request, action_method='session.submit', action_params=params)
