'''Own one Linux command, including detached descendants and inherited pipes.

Standard-library-only entrypoint, run by shell.run_process. Successful commands
may leave fully redirected services alive; commands with open output remain
owned until EOF. Cancellation reaps this command's descendants, never peers.
'''
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time


def children():
    return [int(pid) for pid in Path(
        f'/proc/self/task/{os.getpid()}/children'
    ).read_text().split()]


def cleanup():
    deadline = time.monotonic() + 1.5
    while children() and time.monotonic() < deadline:
        for pid in children():
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
        time.sleep(0.005)
    return not children()


def run(command, shell):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0):  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'Cannot establish command ownership')
    interrupted = 0

    def stop(number, frame):
        nonlocal interrupted
        interrupted = number

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    normal_exit = False
    # 正常完成的命令可留下已重定向输出的服务；取消时只回收本命令拥有的子树。
    process = None
    try:
        process = subprocess.Popen(command, shell=shell, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        # Relay without blocking on a full output pipe. Holding the worker alive
        # until EOF keeps daemonized pipe holders inside its cancellation scope.
        with selectors.PollSelector() as selector:
            streams = []
            for source, target in ((process.stdout, 1), (process.stderr, 2)):
                os.set_blocking(source.fileno(), False)
                os.set_blocking(target, False)
                streams.append({'source': source, 'target': target, 'buffer': bytearray(), 'eof': False})
            while not interrupted:
                code = process.poll()
                if code is not None and all(s['eof'] and not s['buffer'] for s in streams):
                    normal_exit = True
                    return code if code >= 0 else 128 - code
                for key in list(selector.get_map().values()):
                    selector.unregister(key.fd)
                for stream in streams:
                    if not stream['eof'] and len(stream['buffer']) < 65536:
                        selector.register(stream['source'], selectors.EVENT_READ, (stream, 'read'))
                    if stream['buffer']:
                        selector.register(stream['target'], selectors.EVENT_WRITE, (stream, 'write'))
                for key, _ in selector.select(0.02):
                    stream, operation = key.data
                    try:
                        if operation == 'read':
                            data = os.read(key.fd, 65536 - len(stream['buffer']))
                            if data:
                                stream['buffer'].extend(data)
                            else:
                                stream['eof'] = True
                        else:
                            size = os.write(key.fd, stream['buffer'])
                            del stream['buffer'][:size]
                    except BlockingIOError:
                        continue
            return 128 + interrupted
    finally:
        if not normal_exit and not cleanup():
            # stderr itself may be blocked. The parent has its own bounded
            # teardown and records incomplete collection in that situation.
            try:
                os.write(2, b'FORGECODE_COMMAND_CLEANUP_INCOMPLETE\n')
            except OSError:
                pass
        if process is not None:
            for stream in (process.stdout, process.stderr):
                stream.close()


if __name__ == '__main__':
    raise SystemExit(run(json.loads(sys.argv[1]), sys.argv[2] == '1'))
