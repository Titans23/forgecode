"""Read-only business views preserve the native Harness terminal explanation."""
import json

from forge.application.models import ContractError, validate


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
