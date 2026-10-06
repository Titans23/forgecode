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
    for relative in ('pyproject.toml', 'README.md', 'forge', 'benchmark/__init__.py', 'benchmark/harbor', 'benchmark/core'):
        target = source / relative
        if not target.exists():
            raise ValueError(f'Missing source package path: {relative}')
        if target.is_symlink() or getattr(target.lstat(),'st_file_attributes',0)&0x400:
            raise ValueError('Source snapshot root does not follow links')
        candidates = target.rglob('*') if target.is_dir() else (target,)
        for path in candidates:
            parts = path.relative_to(source).parts
            if any(part in _IGNORED or part.endswith('.egg-info') for part in parts):
                continue
            if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:
                raise ValueError(f'Source snapshot does not follow symlinks: {path}')
            if path.is_file() and path.suffix not in {'.pyc', '.pyo'}:
                paths.append(path)
    # Optional for legacy minimal source fixtures; required by the V4 official adapter.
    for relative in ('uv.lock','benchmark/adapters'):
        target=source/relative
        if target.exists():
            if target.is_symlink() or getattr(target.lstat(),'st_file_attributes',0)&0x400:
                raise ValueError('Optional source root does not follow links')
            for path in target.rglob('*') if target.is_dir() else (target,):
                if any(part in _IGNORED for part in path.relative_to(source).parts):
                    continue
                if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:
                    raise ValueError('Source snapshot does not follow optional package links')
                if path.is_file() and path.suffix not in {'.pyc','.pyo'}:
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
    diff = subprocess.run(['git','-c','core.fsmonitor=false','diff','--no-ext-diff','--no-textconv','--binary','HEAD'],
        cwd=source,capture_output=True,check=False,timeout=15)
    manifest = {
        'schema_version': 1,
        'git_revision': revision.stdout.strip() if revision.returncode == 0 else None,
        'dirty_diff_sha256':sha256(diff.stdout).hexdigest() if diff.returncode==0 and diff.stdout else None,
        # The content digest, not HEAD alone, identifies a dirty candidate.
        'content_sha256': sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        'files': before,
    }
    (capture / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return package
