"""Trusted stdlib-only capsule entry, executed inside the isolated Linux container."""
import base64
import json
import os
from pathlib import Path
import signal
import shutil
import stat
import subprocess
import sys
import threading
import time


def stop_descendants():
    """The private PID namespace contains only init, this worker and its task."""
    deadline = time.monotonic() + 2
    while True:
        live = []
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit() or int(entry.name) in (1, os.getpid()):
                continue
            try:
                state = (entry / 'stat').read_text().rsplit(')', 1)[1].split()[0]
                if state != 'Z':
                    live.append(int(entry.name))
            except FileNotFoundError:
                continue
        if not live:
            return
        for sig in (signal.SIGSTOP, signal.SIGKILL):
            for pid in live:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
        if time.monotonic() >= deadline:
            raise RuntimeError('Task descendants did not stop; workspace was not exported')
        time.sleep(.01)


def execute(request):
    root = Path('/workspace')
    executables = set(request['executables'])

    def writable(name):
        return any(not parent or name.startswith(parent + '/') for parent in request['writable_dirs'])

    for name in request['writable_dirs']:
        # Only these paths are writable tmpfs mounts; all remaining project
        # files and reference roots are read-only kernel mounts.
        shutil.copytree(Path('/inputs/project') / name, root / name, dirs_exist_ok=True)
        for directory, _, names in os.walk(root / name):
            for filename in names:
                path = Path(directory) / filename
                path.chmod(0o700 if path.relative_to(root).as_posix() in executables else 0o600)
    captured = {'stdout': bytearray(), 'stderr': bytearray()}
    retained = discarded = 0
    lock = threading.Lock()

    def drain(stream, name):
        nonlocal retained, discarded
        while chunk := stream.read(8192):
            with lock:
                keep = max(0, min(len(chunk), request['output_limit'] - retained))
                captured[name].extend(chunk[:keep])
                retained += keep
                discarded += len(chunk) - keep

    child = subprocess.Popen(request['argv'], cwd=root, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    readers = [threading.Thread(target=drain, args=(getattr(child, name), name), daemon=True)
        for name in ('stdout', 'stderr')]
    for reader in readers:
        reader.start()
    try:
        try:
            child.stdin.write(base64.b64decode(request['stdin'], validate=True))
            child.stdin.close()
        except BrokenPipeError:
            pass
        code = child.wait()
    finally:
        stop_descendants()
    for reader in readers:
        reader.join(2)
        if reader.is_alive():
            raise RuntimeError('Task output stream did not close')
    files, directories, total = {}, [], 0
    for directory, subdirs, names in os.walk(root, followlinks=False):
        for name in sorted([*subdirs, *names]):
            path = Path(directory) / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if len(files) + len(directories) >= request['entries_limit']:
                raise RuntimeError('Workspace entry quota exceeded')
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise RuntimeError('Linked or special workspace output is forbidden')
            if stat.S_ISDIR(info.st_mode):
                directories.append(relative)
                continue
            if info.st_nlink != 1 or info.st_size > request['bytes_limit'] - total:
                raise RuntimeError('Workspace link or byte quota violation')
            with path.open('rb') as stream:
                data = stream.read(request['bytes_limit'] - total + 1)
            total += len(data)
            if total > request['bytes_limit']:
                raise RuntimeError('Workspace byte quota exceeded')
            # Desktop bind mounts do not preserve Windows execute bits. Read-only
            # files retain their original logical mode; writable copies use POSIX.
            executable = bool(info.st_mode & 0o111) if writable(relative) else relative in executables
            files[relative] = {'content': base64.b64encode(data).decode('ascii'), 'executable': executable}
    return {'tree': {'files': files, 'directories': directories}, 'exit_code': code,
        'stdout': base64.b64encode(captured['stdout']).decode('ascii'),
        'stderr': base64.b64encode(captured['stderr']).decode('ascii'), 'discarded_bytes': discarded}


if __name__ == '__main__':
    result = execute(json.load(sys.stdin))
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
