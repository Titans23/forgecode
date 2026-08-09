'''Git-backed working tree tracking for completion evidence.'''

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath

@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    '''Content state for paths currently changed relative to Git HEAD.'''

    files: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkspaceChange:
    '''One observed transition between working tree revisions.'''

    revision: int
    paths: tuple[str, ...]


class WorkspaceTracker:
    '''Track task-local changes without treating prior user edits as Agent work.'''

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.baseline = WorkspaceSnapshot()
        self.current = WorkspaceSnapshot()
        self.revision = 0
        self.available = False
        self._watched_paths: set[str] = set()
        self._carried_paths: set[str] = set()

    async def begin_turn(self) -> None:
        '''Use the current working tree as the immutable baseline for one turn.'''
        self._watched_paths.clear()
        self._carried_paths.clear()
        snapshot = await self._capture()
        self.available = snapshot is not None
        resolved = snapshot or WorkspaceSnapshot()
        self.baseline = resolved
        self.current = resolved
        self.revision = 0

    def carry_existing_changes(self, paths: tuple[str, ...]) -> None:
        '''Restore only persisted task paths still dirty relative to Git HEAD.'''
        self._carried_paths = {
            normalize_path(path)
            for path in paths
            if normalize_path(path) in self.baseline.files
        }

    def watch_paths(self, paths: tuple[str, ...]) -> None:
        '''Capture task baselines for tool targets, including ignored files.'''
        for raw_path in paths:
            candidate = Path(raw_path)
            if candidate.is_absolute():
                continue
            resolved = (self.root / candidate).resolve(strict=False)
            try:
                relative = resolved.relative_to(self.root)
            except ValueError:
                continue
            normalized = normalize_path(str(relative))
            if normalized in self._watched_paths:
                continue
            fingerprint = fingerprint_path(self.root, normalized)
            self._watched_paths.add(normalized)
            self.baseline = WorkspaceSnapshot(
                files={**self.baseline.files, normalized: fingerprint}
            )
            self.current = WorkspaceSnapshot(
                files={**self.current.files, normalized: fingerprint}
            )

    async def refresh(self) -> WorkspaceChange | None:
        '''Capture tool-caused changes and advance the revision when needed.'''
        snapshot = await self._capture()
        if snapshot is None:
            self.available = False
            return None
        self.available = True
        self._carried_paths.intersection_update(snapshot.files)
        paths = changed_paths(self.current, snapshot)
        if not paths:
            return None
        self.current = snapshot
        self.revision += 1
        return WorkspaceChange(revision=self.revision, paths=paths)

    @property
    def changed_paths(self) -> tuple[str, ...]:
        '''Return only paths whose content differs from the turn baseline.'''
        return tuple(
            sorted(
                set(changed_paths(self.baseline, self.current))
                | self._carried_paths
            )
        )

    async def _capture(self) -> WorkspaceSnapshot | None:
        # Import lazily so WorkspaceTracker can be imported independently;
        # forge.tools exports VerifyTool, which itself references this class.
        from forge.tools.shell import run_process

        result = await run_process(
            [
                'git',
                'status',
                '--porcelain=v1',
                '-z',
                '--untracked-files=all',
                '--ignored=no',
            ],
            cwd=self.root,
            timeout_seconds=30,
        )
        if result.exit_code != 0:
            return None

        files = {
            path: fingerprint_path(self.root, path)
            for path in parse_porcelain_paths(result.stdout)
            if not is_runtime_state_path(path)
        }
        for path in self._watched_paths:
            files[path] = fingerprint_path(self.root, path)
        return WorkspaceSnapshot(files=files)


def changed_paths(
    before: WorkspaceSnapshot,
    after: WorkspaceSnapshot,
) -> tuple[str, ...]:
    '''Return deterministic paths whose content state differs.'''
    paths = set(before.files) | set(after.files)
    return tuple(
        sorted(
            path
            for path in paths
            if before.files.get(path) != after.files.get(path)
        )
    )


def parse_porcelain_paths(output: str) -> tuple[str, ...]:
    '''Extract paths from ``git status --porcelain=v1 -z`` output.'''
    records = output.split('\0')
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record or len(record) < 4:
            continue
        status = record[:2]
        paths.append(normalize_path(record[3:]))
        if 'R' in status or 'C' in status:
            if index < len(records) and records[index]:
                paths.append(normalize_path(records[index]))
                index += 1
    return tuple(dict.fromkeys(paths))


def is_runtime_state_path(path: str) -> bool:
    '''Exclude ForgeCode-generated control-plane state from task progress.'''
    normalized = normalize_path(path)
    prefixes = (
        '.forge/context/',
        '.forge/memory/',
        '.forge/tasks/',
        '.forge/trajectories/',
    )
    generated_directories = {
        '.forge-data',
        '__pycache__',
        '.pytest_cache',
        '.mypy_cache',
        '.ruff_cache',
    }
    parts = PurePosixPath(normalized).parts
    return (
        any(normalized.startswith(prefix) for prefix in prefixes)
        or any(part in generated_directories for part in parts)
        or normalized.endswith(('.pyc', '.pyo'))
    )


def normalize_path(path: str) -> str:
    return PurePosixPath(path.replace('\\', '/')).as_posix()


def fingerprint_path(root: Path, relative_path: str) -> str:
    '''Hash file content without following a repository symlink.'''
    path = root / Path(relative_path)
    if path.is_symlink():
        return f'symlink:{os.readlink(path)}'
    if not path.exists():
        return 'missing'
    if path.is_dir():
        digest = sha256()
        for current, directories, files in os.walk(path, followlinks=False):
            current_path = Path(current)
            relative = current_path.relative_to(path)
            for name in sorted(directories):
                child = current_path / name
                kind = 'link' if child.is_symlink() else 'directory'
                digest.update(
                    f'{kind}:{(relative / name).as_posix()}\0'.encode()
                )
            for name in sorted(files):
                child = current_path / name
                kind = 'link' if child.is_symlink() else 'file'
                digest.update(
                    f'{kind}:{(relative / name).as_posix()}\0'.encode()
                )
        return f'directory:{digest.hexdigest()}'
    digest = sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return f'file:{digest.hexdigest()}'
