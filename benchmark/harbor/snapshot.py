'''Freeze the exact package bytes used by every trial and retry in a job.'''

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


_IGNORED = {'__pycache__', '.pytest_cache', '.git', '.forge', 'node_modules'}


def freeze_source(source: Path, destination: Path) -> Path:
    '''Create an auditable, isolated package snapshot without copying secrets.

    Only package roots are copied. A second hash pass detects concurrent edits
    during capture; a later workspace edit never changes a running job's source.
    Incomplete snapshots are preserved for diagnosis, not launched.
    '''
    source = source.resolve()
    paths: list[Path] = []
    for relative in ('pyproject.toml', 'README.md', 'forge', 'benchmark/__init__.py', 'benchmark/harbor'):
        target = source / relative
        if not target.exists():
            raise ValueError(f'Missing source package path: {relative}')
        candidates = target.rglob('*') if target.is_dir() else (target,)
        for path in candidates:
            parts = path.relative_to(source).parts
            if any(part in _IGNORED or part.endswith('.egg-info') for part in parts):
                continue
            if path.is_symlink():
                raise ValueError(f'Source snapshot does not follow symlinks: {path}')
            if path.is_file() and path.suffix not in {'.pyc', '.pyo'}:
                paths.append(path)
    paths.sort()
    before = {path.relative_to(source).as_posix(): sha256(path.read_bytes()).hexdigest() for path in paths}
    destination.mkdir(parents=True, exist_ok=True)
    capture = Path(tempfile.mkdtemp(prefix='candidate-', dir=destination))
    package = capture / 'source'
    package.mkdir()
    for path in paths:
        copied = package / path.relative_to(source)
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, copied)
    for relative, digest in before.items():
        if (
            sha256((source / relative).read_bytes()).hexdigest() != digest
            or sha256((package / relative).read_bytes()).hexdigest() != digest
        ):
            raise RuntimeError(f'Source changed during snapshot capture: {relative}')
    revision = subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=source, capture_output=True,
        text=True, check=False, timeout=15,
    )
    manifest = {
        'schema_version': 1,
        'git_revision': revision.stdout.strip() if revision.returncode == 0 else None,
        # The content digest, not HEAD alone, identifies a dirty candidate.
        'content_sha256': sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        'files': before,
    }
    (capture / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return package
