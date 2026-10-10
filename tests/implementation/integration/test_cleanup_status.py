"""Read real persisted lifecycle facts; incomplete observations stay unknown."""
import asyncio
import json

from forge.engine.methods import EngineMethods
from forge.application.models import validate
from test_application import setup


def test_cleanup_status_preserves_unknown_residual_and_actual_owned_process_counts(tmp_path):
    service, store, _, clients, _, turn = setup(tmp_path)
    methods = EngineMethods(service, profile='test')
    params = {'session_id': turn['session_id']}
    try:
        assert methods.cleanup_status(params)['state'] == 'complete'  # No dispatched resources.
        accepted = service.start_turn(turn)
        assert methods.cleanup_status(params)['state'] == 'pending'
        asyncio.run(service.execute_turn(accepted['turn_id']))
        assert methods.cleanup_status(params)['state'] == 'complete'
        calls = sum(len(client.calls) for client in clients)
        for stored, expected in [('unknown', 'unknown'), ('residual', 'failed')]:
            with store.transaction():
                store.connection.execute('UPDATE turn_lifecycle SET cleanup_state=?,cleanup_json=? WHERE turn_id=?',
                    (stored, json.dumps({'actual_job': {'remaining_processes': 2}}), accepted['turn_id']))
            value = methods.cleanup_status(params)
            validate('sandbox.cleanup_status.result', value)
            assert value['state'] == expected and value['remaining_processes'] == 2
        assert sum(len(client.calls) for client in clients) == calls
        with store.transaction():
            store.connection.execute('DELETE FROM turn_lifecycle WHERE turn_id=?', (accepted['turn_id'],))
        assert methods.cleanup_status(params)['state'] == 'unknown'
    finally:
        store.close()
