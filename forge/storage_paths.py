"""Filesystem spelling for private storage, after its location is validated."""
import os
from pathlib import Path


def private_storage_path(path: Path) -> Path:
    """Allow internal hash directories beyond MAX_PATH without changing policy."""
    if os.name != 'nt':
        return path
    absolute = os.path.abspath(path)
    if not absolute.startswith('\\\\?\\'):
        absolute = '\\\\?\\UNC\\' + absolute[2:] if absolute.startswith('\\\\') else '\\\\?\\' + absolute
    return Path(absolute)
