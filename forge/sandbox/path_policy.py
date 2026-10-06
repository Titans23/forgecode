"""Local path identity checks. Execution workers must also enforce safe OS access."""
from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath
import re
import stat

from forge.application.models import ContractError


SENSITIVE = {'.git', '.forge', '.env', 'id_rsa', 'id_ed25519', 'credentials', 'credentials.json'}
RESERVED = re.compile(r'^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(?:\.|$)', re.I)


def denied(message):
    raise ContractError(message, kind='POLICY_DENIED', code=-32010)


def validate_path_syntax(value, *, platform=None, absolute=False):
    value = str(value)
    platform = platform or ('windows' if os.name == 'nt' else 'linux')
    if not value or '\x00' in value or any(ord(char) < 32 for char in value):
        denied('Control characters and empty paths are unsupported')
    if value.startswith(('\\\\', '//')):
        denied('UNC and device paths are unsupported')
    if platform == 'windows':
        value = value.replace('\\', '/')
        windows = PureWindowsPath(value)
        if windows.drive and not windows.root or value.startswith('/'):
            denied('Drive-relative and current-drive rooted paths are unsupported')
        if absolute and not re.match(r'^[A-Za-z]:/', value):
            denied('An absolute local drive path is required')
        tail = value[2:] if re.match(r'^[A-Za-z]:', value) else value
        for part in tail.split('/'):
            if part not in ('', '.', '..') and (':' in part or part.endswith((' ', '.')) or RESERVED.match(part) or any(char in part for char in '<>"|?*')):
                denied('ADS, reserved names and ambiguous Windows paths are unsupported')
        if re.match(r'^[A-Za-z]:', value):
            value = value[0].upper() + value[1:]
    elif absolute and not value.startswith('/'):
        denied('An absolute local path is required')
    return value


def _stat_identity(value):
    if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
        denied('Symlink or reparse link paths are unsupported')
    if not stat.S_ISREG(value.st_mode) and not stat.S_ISDIR(value.st_mode):
        denied('Special filesystem objects are unsupported')
    if stat.S_ISREG(value.st_mode) and value.st_nlink > 1:
        denied('Existing multi-hardlink files are unsupported in strict paths')
    if not value.st_ino:
        denied('Filesystem does not expose a stable file identity')
    return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode))


def _identity(path):
    try:
        return _stat_identity(path.lstat())
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ContractError('Path identity cannot be inspected', kind='POLICY_DENIED', code=-32010) from error


@dataclass(frozen=True)
class AuthorizedPath:
    path: Path
    anchors: tuple

    def assert_current(self):
        for path, identity in self.anchors:
            if _identity(path) != identity:
                raise ContractError('Authorized path identity changed', kind='STALE_FILE', code=-32010)


def inspect_path(value, *, absolute=False):
    raw = validate_path_syntax(value, absolute=absolute)
    path = Path(os.path.abspath(raw))
    anchors = tuple((parent, _identity(parent)) for parent in reversed((path, *path.parents)))
    return AuthorizedPath(path, anchors)


@dataclass(frozen=True)
class PathPolicy:
    workspace: AuthorizedPath
    read_mode: str
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]
    protected_paths: tuple[Path, ...]

    def authorize(self, raw, *, write=False):
        candidate = Path(raw)
        candidate = candidate if candidate.is_absolute() else self.workspace.path / candidate
        self.workspace.assert_current()
        observed = inspect_path(candidate)
        target = observed.path
        if any(part.casefold() in SENSITIVE or part.casefold().startswith('.env.') for part in target.parts):
            denied('Protected control or sensitive path')
        if any(target == protected or target.is_relative_to(protected) for protected in self.protected_paths):
            denied('Protected path deny overrides allow')
        roots = self.write_roots if write else self.read_roots if self.read_mode == 'strict_allowlist_required' else None
        if roots is not None and not any(target == root or target.is_relative_to(root) for root in roots):
            denied('Path is outside the authorized roots')
        return observed


def audit_workspace_links(root):
    def failed(error):
        raise ContractError('Workspace link audit is unavailable', kind='POLICY_DENIED', code=-32010) from error
    for directory, children, files in os.walk(root, followlinks=False, onerror=failed):
        for name in children + files:
            _identity(Path(directory) / name)
        children[:] = [name for name in children if name.casefold() not in ('.git', '.forge')]


def _read_marker(path):
    authorized = inspect_path(path)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as stream:
        if _stat_identity(os.fstat(stream.fileno())) != authorized.anchors[-1][1]:
            raise ContractError('Git metadata identity changed before read', kind='STALE_FILE', code=-32010)
        authorized.assert_current()
        data = stream.read(4097)
    if len(data) > 4096:
        denied('Unsupported git metadata marker size')
    try:
        return data.decode('utf-8').strip()
    except UnicodeError as error:
        raise ContractError('Unsupported git metadata encoding', kind='POLICY_DENIED', code=-32010) from error


def git_metadata_paths(root):
    marker = root / '.git'
    observed = inspect_path(marker)
    if not marker.is_file():
        return [observed.path]
    text = _read_marker(marker)
    if not text.startswith('gitdir: ') or '\n' in text:
        denied('Unsupported gitdir marker')
    gitdir = Path(text[8:])
    gitdir = inspect_path(gitdir if gitdir.is_absolute() else root / gitdir).path
    if not gitdir.is_dir():
        denied('Git metadata directory is unavailable')
    protected = [observed.path, gitdir]
    common = gitdir / 'commondir'
    inspect_path(common)
    if common.is_file():
        relative = _read_marker(common)
        if not relative or '\n' in relative:
            denied('Unsupported common gitdir marker')
        shared = inspect_path(gitdir / relative).path
        if not shared.is_dir():
            denied('Common git metadata is unavailable')
        protected.append(shared)
    return protected
