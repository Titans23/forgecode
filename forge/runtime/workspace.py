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

    async def begin_turn(self) -> None:
        '''Use the current working tree as the immutable baseline for one turn.'''
        snapshot = await self._capture()
        self.available = snapshot is not None
        resolved = snapshot or WorkspaceSnapshot()
        self.baseline = resolved
        self.current = resolved
        self.revision = 0

    async def refresh(self) -> WorkspaceChange | None:
        '''Capture tool-caused changes and advance the revision when needed.'''
        snapshot = await self._capture()
        if snapshot is None:
            self.available = False
            return None
        self.available = True
        paths = changed_paths(self.current, snapshot)
        if not paths:
            return None
        self.current = snapshot
        self.revision += 1
        return WorkspaceChange(revision=self.revision, paths=paths)

    @property
    def changed_paths(self) -> tuple[str, ...]:
        '''Return only paths whose content differs from the turn baseline.'''
        return changed_paths(self.baseline, self.current)

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
        }
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
        return 'directory'
    digest = sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return f'file:{digest.hexdigest()}'
