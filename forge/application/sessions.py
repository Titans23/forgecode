"""Session operations and durable display views over the native Harness."""
import json

from forge.protocol import ContractError, validate
from forge.runtime.state import ModelTextDelta, ToolExecutionStarted, ToolExecutionCompleted, TurnCompleted


def session_view(store, session_id):
    row = store.connection.execute('SELECT s.*,c.snapshot_id,cs.hash FROM sessions s '
        'JOIN session_configurations c ON c.session_id=s.id JOIN configuration_snapshots cs ON cs.id=c.snapshot_id '
        'WHERE s.id=?', (session_id,)).fetchone()
    if row is None:
        raise ContractError('Session not found', kind='NOT_FOUND', code=-32010)
    active = store.connection.execute("SELECT id,state FROM turns WHERE session_id=? AND state!='finished' ORDER BY rowid LIMIT 1", (session_id,)).fetchone()
    value = {'session_id': row['id'], 'workspace_id': row['workspace_id'], 'state': active['state'] if active else 'idle',
             'created_at_utc': row['created_at'], 'configuration': {'snapshot_id': row['snapshot_id'], 'sha256': row['hash']},
             'active_turn_id': active['id'] if active else None}
    # The session schema has fewer states than turns: expose reconciling as indeterminate.
    if value['state'] in ('reconciling', 'awaiting_approval'):
        value['state'] = 'indeterminate' if value['state'] == 'reconciling' else 'running'
    return validate('session', value)


def turn_view(store, turn_id):
    row = store.connection.execute('SELECT * FROM turns WHERE id=?', (turn_id,)).fetchone()
    if row is None:
        raise ContractError('Turn not found', kind='NOT_FOUND', code=-32010)
    native = store.connection.execute('SELECT native_outcome_json FROM turn_results WHERE turn_id=?', (turn_id,)).fetchone()
    return {'turn_id': row['id'], 'state': row['state'], 'outcome': row['outcome'],
            'configuration': json.loads(row['config_json']), 'native_outcome': json.loads(native[0]) if native else None}


def outcome_for(result):
    if result.stop_reason.endswith('budget_exhausted'):
        return 'budget_exhausted'
    if result.status in ('completed', 'blocked', 'failed'):
        return result.status
    # partial/stuck are native failure details; they never imply successful completion.
    return 'failed'


def accepted_view(row):
    return {'turn_id': row['id'], 'state': row['state'], 'outcome': row['outcome'],
            'accepted': True, 'reused_existing_action': False}


def message_usage(store, turn_id):
    # Python len preserves the existing character quota, including embedded NULs.
    sequence = count = used = 0
    for sequence, text in store.connection.execute(
            'SELECT sequence,text FROM turn_messages WHERE turn_id=? ORDER BY sequence', (turn_id,)):
        count += 1
        used += len(text)
    return sequence, count, used


class TurnMessages:
    def __init__(self, store, turn_id, credential):
        self.store, self.turn_id = store, turn_id
        self.secret = credential or ''
        self.pending = ''

    def append(self, kind, text, *, tool_name=None, status=None, execution_id=None):
        if self.secret:
            text = text.replace(self.secret, '[credential redacted]')
        with self.store.transaction():
            sequence, count, used = message_usage(self.store, self.turn_id)
            if count >= 10000 or used >= 1048576:
                return
            text = text[:min(2048, 1048576-used)]
            sequence += 1
            self.store.connection.execute('INSERT INTO turn_messages(turn_id,sequence,kind,text,tool_name,status,execution_id) VALUES(?,?,?,?,?,?,?)',
                (self.turn_id, sequence, kind, text, tool_name, status, execution_id))
            self.store._turn_event('session.message', self.turn_id, {'sequence': sequence, 'kind': kind})

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
            from forge.observability.events import active
            boundary=active.get()
            scope=boundary.recorder.tools.get((boundary.branch.span_id,call.id)) if boundary and isinstance(event,ToolExecutionCompleted) else None
            self.append('tool', call.name, tool_name=call.name, status=status,execution_id=scope.execution_id if scope else None)
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
        messages = store.connection.execute('SELECT sequence,kind,text,tool_name,status,execution_id FROM turn_messages WHERE turn_id=? AND sequence>? '
            'ORDER BY sequence LIMIT 101', (turn_id, after)).fetchall() if turn_id else []
        configuration = json.loads(store.connection.execute('SELECT normalized_json FROM configuration_snapshots WHERE id=?',
            (session['configuration']['snapshot_id'],)).fetchone()[0])
        cursor = store.connection.execute('SELECT COALESCE(MAX(store_seq),0) FROM events').fetchone()[0]
        _, count, used = message_usage(store, turn_id)
        return {'session': session, 'turns': [accepted_view(r) for r in rows[:100]], 'has_more_turns': len(rows)>100,
            'turn_id': turn_id, 'messages': [dict(r) for r in messages[:100]], 'has_more_messages': len(messages)>100,
            'message_limit_reached': count >= 10000 or used >= 1048576,
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
            'filesystem': {'read_mode': 'host_default' if service.mode == 'workspace-write' else 'backend_default_with_protected_paths', 'read_roots': [root], 'write_roots': [root],
                'protected_paths': [], 'deny_overrides_allow': True, 'reject_unsafe_links': True},
            'network': {'mode': 'inherit' if service.mode == 'workspace-write' else 'deny_direct', 'allowed_domains': [], 'dns_isolation_required': False},
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
