import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from benchmark.harbor.controller import run_controller, controller_status


@pytest.mark.parametrize('failure', ['temporary-sharing', 'persistent-windows-denial', 'permission-denied'])
def test_atomic_state_update_preserves_old_json_until_replace_succeeds(tmp_path, monkeypatch, failure):
    from benchmark.harbor.controller import _write
    path = tmp_path / 'state.json'
    _write(path, {'status': 'old'})
    original, attempts = Path.replace, []
    def replace(source, target):
        attempts.append(None)
        assert json.loads(path.read_text()) == {'status': 'old'}
        if failure == 'temporary-sharing' and len(attempts) > 2:
            return original(source, target)
        error = PermissionError('synthetic filesystem denial')
        if failure != 'permission-denied': error.winerror = 32 if failure == 'temporary-sharing' else 5
        raise error
    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr('benchmark.harbor.controller.time.sleep', lambda _: None)
    if failure == 'temporary-sharing':
        _write(path, {'status': 'new'})
        assert json.loads(path.read_text()) == {'status': 'new'}
    else:
        with pytest.raises(PermissionError): _write(path, {'status': 'new'})
        assert json.loads(path.read_text()) == {'status': 'old'}
        assert len(attempts) < 100
        if failure == 'permission-denied': assert len(attempts) == 1


@pytest.mark.parametrize('interrupt', [False, True])
def test_owned_controller_cancels_real_child_and_records_unknown_cleanup(tmp_path, interrupt):
    from benchmark.harbor.controller import cancel_run
    import signal
    observed = []
    def cancel():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = controller_status(tmp_path)
            if state['status'] == 'running':
                if interrupt:
                    if os.name == 'nt':
                        _thread.interrupt_main()
                    else:
                        os.kill(os.getpid(), signal.SIGINT)
                else:
                    observed.append(cancel_run(tmp_path))
                return
            time.sleep(.02)
    import _thread
    worker = threading.Thread(target=cancel)
    worker.start()
    code = run_controller([sys.executable, '-c', 'import time; time.sleep(60)'],
        cwd=tmp_path, env=os.environ.copy(), output_dir=tmp_path, interval=.05)
    worker.join(timeout=10)
    state = controller_status(tmp_path)
    assert code == 130 and state['status'] == 'cancelled' and state['child_stopped']
    assert state['cleanup_state'] == 'unknown'
    if not interrupt:
        assert observed[0]['cancel_requested']
    assert not cancel_run(tmp_path)['cancel_requested']


def test_real_child_exit_is_recorded_without_job_result(tmp_path):
    code = run_controller([sys.executable, '-c', 'import time; time.sleep(.1); raise SystemExit(7)'],
                          cwd=tmp_path, env=os.environ.copy(), output_dir=tmp_path, interval=.02)
    state = controller_status(tmp_path)
    assert code == state['exit_code'] == 7
    assert state['status'] == 'exited'
    assert state['child_pid'] != state['controller_pid']
    assert state['started_at'] < state['heartbeat_at'] < state['finished_at']


def test_expired_heartbeat_cannot_be_reported_as_running(tmp_path):
    (tmp_path / 'controller-state.json').write_text(json.dumps({
        'status': 'running', 'heartbeat_at': '2000-01-01T00:00:00+00:00', 'child_pid': 999999,
    }))
    assert controller_status(tmp_path)['status'] == 'unresponsive'
