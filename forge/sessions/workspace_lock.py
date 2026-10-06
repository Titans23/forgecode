"""Share one OS-owned workspace execution lock across CLI and services."""
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from pathlib import Path
import os

from forge.sessions.locking import exclusive_append


_owned = ContextVar('forge_owned_workspaces', default=frozenset())


@contextmanager
def workspace_execution(root):
    root = Path(root).resolve(strict=True)
    identity = root.stat()
    key = f'{identity.st_dev}:{identity.st_ino}'
    if key in _owned.get():
        # A child Explore turn inherits its parent's ownership and budget.
        from forge.runtime.turn_state import parent_budget
        if parent_budget.get() is not None:
            yield
            return
    directory = Path(os.environ.get('FORGE_WORKSPACE_LOCK_DIR', Path.home() / '.forge' / 'workspace-locks'))
    path = directory / sha256(key.encode()).hexdigest()
    with exclusive_append(path):
        token = _owned.set(_owned.get() | {key})
        try:
            yield
        finally:
            _owned.reset(token)
