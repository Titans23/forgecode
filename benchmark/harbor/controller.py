'''Persist controller lifecycle independently of Harbor's job-result writer.'''

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def controller_status(output_dir: Path) -> dict:
    path = output_dir / 'controller-state.json'
    if not path.exists():
        return {'status': 'unknown'}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if value['status'] == 'running':
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(value['heartbeat_at'])).total_seconds()
            if age > 60:
                value = {**value, 'status': 'unresponsive', 'reason': 'controller heartbeat expired; inspect processes before recovery'}
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return {'status': 'unknown', 'reason': 'controller state unreadable'}


def run_controller(command, *, cwd, env, output_dir: Path, interval=5.0, metadata=None):
    '''Wait for one child; no retries, service restart, or orphan adoption.'''
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / 'controller-state.json'
    # Never overwrite an earlier run's audit trail or leak env/argv credentials.
    with path.open('x', encoding='utf-8') as stream:
        stream.write('{}')
    state = {'status': 'starting', 'controller_pid': os.getpid(), 'started_at': _now(),
             'metadata': metadata or {}}
    _write(path, state)
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env)
        state.update(status='running', child_pid=process.pid)
        while True:
            state['heartbeat_at'] = _now()
            _write(path, state)
            try:
                code = process.wait(timeout=interval)
                break
            except subprocess.TimeoutExpired:
                continue
        state.update(status='exited', exit_code=code, finished_at=_now())
        _write(path, state)
        return code
    except BaseException as error:
        # Interruption does not imply the child was stopped. Preserve identity
        # for explicit recovery instead of silently replaying paid work.
        state.update(status='interrupted', error_type=type(error).__name__, finished_at=_now())
        _write(path, state)
        raise
