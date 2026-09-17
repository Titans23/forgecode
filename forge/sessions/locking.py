'''OS-owned locks released automatically when a writer crashes.'''

from contextlib import contextmanager
from pathlib import Path
import os


@contextmanager
def exclusive_append(path: Path):
    '''Serialize head validation and append across threads and processes.'''
    # 锁覆盖“检查日志尾部 + 追加”整个操作，由操作系统在进程退出时释放。
    lock_path = path.with_suffix(path.suffix + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except PermissionError as error:
                raise BlockingIOError('Session is already being written') from error
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
