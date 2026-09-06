import json
import os
from pathlib import Path
import sys

from benchmark.harbor.controller import run_controller, controller_status


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
