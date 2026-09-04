'''Linux benchmark child ownership, including descendants that call setsid.

This supervisor is the sole parent of the agent command. It becomes a Linux
subreaper so detached grandchildren are adopted rather than leaked to PID 1.
No unrelated process, container, or workspace is targeted.
'''

import ctypes
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


def supervise(argv):
    if not sys.platform.startswith('linux'):
        raise RuntimeError('Benchmark process supervision requires Linux')
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
    try:
        process = subprocess.Popen(argv, start_new_session=True)
        while process.poll() is None and not interrupted:
            time.sleep(0.02)
        exit_code = 128 + interrupted if interrupted else process.returncode
    finally:
        # Killing an adopted parent causes its own children to be adopted by
        # us. Repeat until the owned tree is empty, bounded below GNU timeout's
        # ten-second kill-after grace. Do this on success as well as timeout.
        deadline = time.monotonic() + 5
        while _children() and time.monotonic() < deadline:
            for pid in _children():
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
            time.sleep(0.01)
        if _children():
            print('FORGECODE_SUPERVISOR_CLEANUP_INCOMPLETE', file=sys.stderr)
            exit_code = 125
        if process is not None:
            process.poll()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    return exit_code if exit_code >= 0 else 128 - exit_code


if __name__ == '__main__':
    args = sys.argv[1:]
    if args[:1] == ['--']:
        args = args[1:]
    if not args:
        raise SystemExit('Missing supervised command')
    raise SystemExit(supervise(args))
