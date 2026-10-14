'''Persist controller lifecycle independently of Harbor's job-result writer.'''

from datetime import datetime, timezone
import asyncio
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


def cancel_run(output_dir: Path) -> dict:
    """Request cancellation through the owning controller; never signal a saved PID."""
    state = controller_status(output_dir)
    if state['status'] not in {'starting', 'running'}:
        return {**state, 'cancel_requested': False,
                'reason': 'No responsive owning controller; inspect resources before recovery.'}
    path = output_dir / 'cancel-request.json'
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump({'requested_at': _now()}, stream)
            stream.flush(); os.fsync(stream.fileno())
    except FileExistsError:
        pass
    return {**state, 'cancel_requested': True}


def _cleanup(output_dir):
    from benchmark.adapters.docker import reconcile_resources
    try:
        return asyncio.run(reconcile_resources(output_dir / 'resources.jsonl'))
    except Exception:
        return 'unknown'


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
    process = None
    try:
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        process = subprocess.Popen(command, cwd=cwd, env=env, **options)
        state.update(status='running', child_pid=process.pid)
        while True:
            state['heartbeat_at'] = _now()
            _write(path, state)
            if (output_dir / 'cancel-request.json').exists():
                raise KeyboardInterrupt
            try:
                code = process.wait(timeout=interval)
                break
            except subprocess.TimeoutExpired:
                continue
        state.update(status='exited', exit_code=code, finished_at=_now())
        _write(path, state)
        return code
    except KeyboardInterrupt:
        from benchmark.adapters.process import stop_process_group
        stopped = False
        if process is not None:
            try:
                stopped = stop_process_group(process)
            except (OSError, subprocess.TimeoutExpired):
                pass
        state.update(status='cancelled', termination_reason='cancelled', child_stopped=stopped,
                     cleanup_state=_cleanup(output_dir) if stopped else 'unknown', finished_at=_now())
        _write(path, state)
        return 130
    except BaseException as error:
        from benchmark.adapters.process import stop_process_group
        if process is not None:
            try:
                stop_process_group(process)
            except (OSError, subprocess.TimeoutExpired):
                pass
        state.update(status='interrupted', error_type=type(error).__name__, cleanup_state='unknown', finished_at=_now())
        _write(path, state)
        raise
