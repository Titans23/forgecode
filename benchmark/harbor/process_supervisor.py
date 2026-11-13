'''Linux benchmark child ownership, including descendants that call setsid.

This supervisor is the sole parent of the agent command. It becomes a Linux
subreaper so detached grandchildren are adopted rather than leaked to PID 1.
No unrelated process, container, or workspace is targeted.
'''

import ctypes
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def _children():
    return [int(pid) for pid in Path(
        f'/proc/self/task/{os.getpid()}/children'
    ).read_text().split()]


def supervise(argv, *, preserve_on_success=False, timeout_seconds=None, service_receipts=None):
    if not sys.platform.startswith('linux'):
        raise RuntimeError('Benchmark process supervision requires Linux')
    if timeout_seconds is not None and (not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError('Timeout must be finite and positive')
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'Cannot enable child subreaper')
    interrupted = 0

    def on_signal(number, frame):
        nonlocal interrupted
        interrupted = number

    handlers = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
    process = None
    exit_code = 125
    timed_out = False
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    try:
        environment = dict(os.environ)
        if deadline is not None:
            environment['FORGE_AGENT_DEADLINE_MONOTONIC'] = str(deadline)
        else:
            environment.pop('FORGE_AGENT_DEADLINE_MONOTONIC', None)
        process = subprocess.Popen(argv, start_new_session=True, env=environment)
        while process.poll() is None and not interrupted:
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.02)
        exit_code = 124 if timed_out else 128 + interrupted if interrupted else process.returncode
    finally:
        # Killing an adopted parent causes its own children to be adopted by
        # us. Repeat until the owned tree is empty within the cleanup reserve.
        # Only receipted services can pass to the evaluation environment.
        preserve = preserve_on_success and service_receipts is None and not interrupted and exit_code == 0
        transferred = {}
        if service_receipts is not None and process is not None and not interrupted and (timed_out or process.returncode is not None):
            from forge.sandbox.process_owner import linux_process_identity
            for path in Path(service_receipts).glob('exec-*.json'):
                try:
                    receipt = json.loads(path.read_text())
                    pid = int(receipt['pid'])
                    if (receipt.get('state') in {'running', 'transferred'} and receipt.get('receiver') == 'evaluation-environment'
                            and receipt.get('start_time') and receipt['start_time'] == linux_process_identity(pid)):
                        transferred[pid] = receipt
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        # Explicit delivery services survive the official deadline, never user cancellation.
        deadline = time.monotonic() + 5
        handed_off = set()
        while not preserve and _children() and time.monotonic() < deadline:
            for pid in _children():
                if pid in transferred:
                    # Only our directly adopted children may cross this boundary.
                    # A receipt cannot nominate an unrelated process for handoff.
                    handed_off.add(pid)
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            while True:
                try:
                    pid, _ = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    break
                if not pid:
                    break
            if not set(_children()) - handed_off:
                break
            time.sleep(0.01)
        if not preserve and set(_children()) - handed_off:
            print('FORGECODE_SUPERVISOR_CLEANUP_INCOMPLETE', file=sys.stderr)
            exit_code = 125
        if service_receipts is not None:
            output = Path(service_receipts)
            output.mkdir(parents=True, exist_ok=True)
            (output / 'supervisor-result.json').write_text(json.dumps({'exit_code': exit_code,
                'timed_out': timed_out, 'cancelled': bool(interrupted),
                'handed_off': [transferred[pid] for pid in sorted(handed_off)],
                'receiver': 'evaluation-environment'}))
        if process is not None:
            process.poll()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    return exit_code if exit_code >= 0 else 128 - exit_code


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--preserve-on-success', action='store_true')
    parser.add_argument('--timeout-seconds', type=float)
    parser.add_argument('--service-receipts', type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        raise SystemExit('Missing supervised command')
    raise SystemExit(supervise(command, preserve_on_success=args.preserve_on_success,
        timeout_seconds=args.timeout_seconds, service_receipts=args.service_receipts))
