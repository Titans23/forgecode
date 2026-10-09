"""Bind a selected regular file without traversing links or reparse points."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat

from forge.application.models import ContractError


def file_identity(path):
    """No link/reparse traversal, including ancestors; bind the selected real file."""
    path=Path(os.path.abspath(path))
    for parent in reversed((path,*path.parents)):
        info=parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ContractError('File selection contains a link or reparse point')
    info=path.stat()
    if not path.is_file() or info.st_nlink!=1: raise ContractError('Selection must be one regular, unlinked file')
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)


@contextmanager
def selected_file(path,expected=None):
    identity=file_identity(path)
    if expected is not None and tuple(expected)!=identity: raise ContractError('Selected file changed',kind='STALE_REVISION',code=-32010)
    with Path(path).open('rb') as file:
        info=os.fstat(file.fileno())
        if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=identity:
            raise ContractError('Selected file changed before opening',kind='STALE_REVISION',code=-32010)
        yield file
        if file_identity(path)!=identity: raise ContractError('Selected file changed while reading',kind='STALE_REVISION',code=-32010)


