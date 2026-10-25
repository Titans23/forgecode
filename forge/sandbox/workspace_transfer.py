"""Bounded workspace snapshots and conflict-checked application of untrusted output."""
import base64
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile

from forge.protocol import ContractError
from forge.sandbox.path_policy import SENSITIVE, validate_path_syntax


PRIVATE_NAMES = SENSITIVE | {'.ssh', '.aws', '.azure', '.gcloud', '.kube', '.gnupg', '.npmrc', '.pypirc', '.netrc'}


def rejected(message, kind='POLICY_DENIED'):
    return ContractError(message, kind=kind, code=-32010)


def relative_name(name):
    if not isinstance(name, str) or not name or len(name) > 1024 or '\\' in name or ':' in name:
        raise rejected('Invalid portable workspace path')
    path = PurePosixPath(name)
    if path.is_absolute() or str(path) != name or any(part in ('.', '..') for part in path.parts) or name == '.':
        raise rejected('Workspace path must be canonical and relative')
    validate_path_syntax(name, platform='windows')
    if any(part.casefold() in PRIVATE_NAMES or part.casefold().startswith('.env.') for part in path.parts):
        raise rejected('Protected workspace path')
    return path


@dataclass(frozen=True)
class TransferLimits:
    entries: int = 4000
    bytes: int = 16777216

    def __post_init__(self):
        if type(self.entries) is not int or type(self.bytes) is not int or self.entries < 1 or self.bytes < 1:
            raise ValueError('Transfer limits must be positive integers')


def decode_tree(value, limits):
    if not isinstance(value, dict) or set(value) != {'files', 'directories'}:
        raise rejected('Invalid workspace manifest')
    files, directories = value['files'], value['directories']
    if not isinstance(files, dict) or not isinstance(directories, list):
        raise rejected('Invalid workspace entries')
    if len(files) + len(directories) > limits.entries:
        raise rejected('Workspace entry quota exceeded', 'ARTIFACT_LIMIT')
    names, decoded, total = set(), {}, 0
    for name in [*directories, *files]:
        relative_name(name)
        if name.casefold() in names:
            raise rejected('Duplicate or case-ambiguous workspace entry')
        names.add(name.casefold())
    dirs = set(directories)
    for name in [*directories, *files]:
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.' and str(parent) not in dirs:
                raise rejected('Workspace manifest omits a parent directory')
    for name, record in files.items():
        if not isinstance(record, dict) or set(record) != {'content', 'executable'} or type(record['executable']) is not bool:
            raise rejected('Invalid workspace file record')
        encoded = record['content']
        if not isinstance(encoded, str) or len(encoded) > 4 * ((limits.bytes + 2) // 3):
            raise rejected('Workspace byte quota exceeded', 'ARTIFACT_LIMIT')
        try:
            content = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise rejected('Invalid workspace content encoding') from error
        total += len(content)
        if total > limits.bytes:
            raise rejected('Workspace byte quota exceeded', 'ARTIFACT_LIMIT')
        decoded[name] = (content, record['executable'])
    return decoded, dirs


class WorkspaceSnapshot:
    def __init__(self, policy, *, limits=None):
        self.policy = policy
        self.root = policy.workspace.path
        self.limits = limits or TransferLimits()
        self.files, self.directories, self.identities = self._capture()

    def _protected(self, path):
        relative = path.relative_to(self.root)
        return any(part.casefold() in PRIVATE_NAMES or part.casefold().startswith('.env.') for part in relative.parts) or any(
            path == parent or path.is_relative_to(parent) for parent in self.policy.protected_paths)

    def _capture(self):
        files, directories, identities, total = {}, set(), {}, 0
        visited = 0
        self.policy.authorize(self.root).assert_current()
        pending = [self.root]
        while pending:
            directory = pending.pop()
            for path in self._children(directory):
                visited += 1
                if visited > self.limits.entries:
                    raise rejected('Workspace entry quota exceeded', 'ARTIFACT_LIMIT')
                if self._protected(path):
                    continue
                name = path.relative_to(self.root).as_posix()
                relative_name(name)
                authorized = self.policy.authorize(path)
                info = path.lstat()
                if len(files) + len(directories) >= self.limits.entries:
                    raise rejected('Workspace entry quota exceeded', 'ARTIFACT_LIMIT')
                if stat.S_ISDIR(info.st_mode):
                    directories.add(name)
                    identities[name] = (info.st_dev, info.st_ino)
                    pending.append(path)
                else:
                    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
                    with os.fdopen(os.open(path, flags), 'rb') as stream:
                        opened = os.fstat(stream.fileno())
                        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                            raise rejected('Workspace file identity changed', 'STALE_FILE')
                        authorized.assert_current()
                        data = stream.read(self.limits.bytes - total + 1)
                        after = os.fstat(stream.fileno())
                    if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise rejected('Workspace file changed during capture', 'STALE_FILE')
                    total += len(data)
                    if total > self.limits.bytes:
                        raise rejected('Workspace byte quota exceeded', 'ARTIFACT_LIMIT')
                    files[name] = (data, bool(info.st_mode & 0o111))
                    identities[name] = self._file_identity(info)
                authorized.assert_current()
        # Check portable name collisions with the same decoder used for returns.
        decode_tree(self._encode(files, directories), self.limits)
        return files, directories, identities

    @staticmethod
    def _children(directory):
        # Avoid listdir/sorting an unbounded directory before checking quotas.
        with os.scandir(directory) as entries:
            for entry in entries:
                yield Path(entry.path)

    @staticmethod
    def _encode(files, directories):
        return {'files': {name: {'content': base64.b64encode(data).decode('ascii'), 'executable': executable}
                for name, (data, executable) in sorted(files.items())}, 'directories': sorted(directories)}

    def encode(self):
        return self._encode(self.files, self.directories)

    def assert_unchanged(self):
        if self._capture() != (self.files, self.directories, self.identities):
            raise rejected('Workspace changed while the sandbox was running; output was not applied', 'STALE_REVISION')

    @staticmethod
    def _file_identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, stat.S_IMODE(info.st_mode))

    def _assert_file_current(self, name):
        if self._file_identity((self.root / name).lstat()) != self.identities[name]:
            raise rejected('File changed during writeback', 'STALE_FILE')

    def apply(self, value):
        """Preflight the complete diff; each file replacement is atomic, not the whole tree."""
        files, directories = decode_tree(value, self.limits)
        changed = {name for name, content in files.items() if self.files.get(name) != content}
        removed_files = set(self.files) - files.keys()
        new_dirs, removed_dirs = directories - self.directories, self.directories - directories
        affected = changed | removed_files | new_dirs | removed_dirs
        for name in sorted(affected):
            self.policy.authorize(self.root / name, write=True).assert_current()
        self.assert_unchanged()
        for name in removed_dirs:
            if any(child.relative_to(self.root).as_posix() not in removed_files | removed_dirs for child in (self.root / name).iterdir()):
                raise rejected('Directory removal would affect excluded or protected entries')
        applied = []
        try:
            for name in sorted(removed_files, key=lambda name: (-name.count('/'), name)):
                self.policy.authorize(self.root / name, write=True).assert_current()
                self._assert_file_current(name)
                (self.root / name).unlink()
                applied.append(name)
            for name in sorted(removed_dirs, key=lambda name: (-name.count('/'), name)):
                self.policy.authorize(self.root / name, write=True).assert_current()
                (self.root / name).rmdir()
                applied.append(name)
            for name in sorted(new_dirs, key=lambda name: (name.count('/'), name)):
                self.policy.authorize(self.root / name, write=True).assert_current()
                (self.root / name).mkdir()
                applied.append(name)
            for name in sorted(changed):
                path = self.root / name
                authorized = self.policy.authorize(path, write=True)
                descriptor, temporary = tempfile.mkstemp(prefix='.forge-write-', dir=path.parent)
                try:
                    with os.fdopen(descriptor, 'wb') as stream:
                        stream.write(files[name][0])
                        stream.flush()
                        os.fsync(stream.fileno())
                    mode = self.identities[name][-1] & 0o777 if name in self.files else 0o644
                    mode = (mode | 0o100) if files[name][1] else (mode & ~0o111)
                    os.chmod(temporary, mode)
                    authorized.assert_current()
                    if name not in self.files:
                        # Exclusive publication must not clobber a new user file.
                        os.link(temporary, path)
                    else:
                        self._assert_file_current(name)
                        os.replace(temporary, path)
                    applied.append(name)
                finally:
                    Path(temporary).unlink(missing_ok=True)
        except (OSError, ContractError) as error:
            failure = rejected(f'Writeback incomplete after {len(applied)} entries; do not replay automatically', 'INDETERMINATE')
            failure.applied_paths = tuple(applied)
            raise failure from error
        return sorted(affected)
