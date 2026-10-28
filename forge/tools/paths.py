"""Path resolution, access guards and target extraction for tools and dispatch."""
from __future__ import annotations
from contextvars import ContextVar
from pathlib import Path
from forge.tool_types import ToolExecutionError
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.runtime.state import ToolCall

def mutation_target_paths(
    tool_call: ToolCall,
    *,
    maximum: int | None = 5,
) -> tuple[str, ...]:
    '''Extract only path evidence, never the potentially large write body.'''
    paths: list[str] = []
    direct_path = tool_call.arguments.get('path')
    if isinstance(direct_path, str) and direct_path.strip():
        direct_path = direct_path.strip().replace('\\', '/')
        paths.append(direct_path)
    patch = tool_call.arguments.get('patch')
    if isinstance(patch, str):
        prefixes = (
            '*** Update File:',
            '*** Add File:',
            '*** Delete File:',
            '*** Move to:',
            '+++ b/',
            '--- a/',
        )
        for line in patch.splitlines():
            stripped = line.strip()
            prefix = next(
                (
                    candidate
                    for candidate in prefixes
                    if stripped.startswith(candidate)
                ),
                None,
            )
            if prefix is None:
                continue
            path = stripped[len(prefix):].strip().replace('\\', '/')
            if path and path != '/dev/null':
                paths.append(path)
    unique = tuple(dict.fromkeys(paths))
    return unique if maximum is None else unique[:maximum]


def checkpoint_mutation_paths(
    root: Path,
    tool_call: ToolCall,
) -> tuple[str, ...]:
    '''Expand recursive directory deletion into checkpoint-restorable entries.'''
    targets = mutation_target_paths(tool_call, maximum=None)
    if tool_call.name != 'remove_directory' or not targets:
        return targets
    raw_path = targets[0]
    candidate = (root.resolve() / raw_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return targets
    if not candidate.is_dir():
        return targets
    descendants = sorted(
        (
            path.relative_to(root.resolve()).as_posix()
            for path in candidate.rglob('*')
            if not path.is_symlink()
        ),
        key=lambda path: (path.count('/'), path),
    )
    return tuple(dict.fromkeys((raw_path, *descendants)))



file_access_guard = ContextVar('forge_file_access_guard', default=None)


def resolve_repository_path(
    root: Path,
    raw_path: str,
    *,
    must_exist: bool = True,
) -> Path:
    '''Resolve a path from the working directory or the wider filesystem.'''
    guard = file_access_guard.get()
    if guard is not None:
        resolved = guard(raw_path)
        if must_exist and not resolved.exists():
            raise ToolExecutionError('path_not_found', f'Path does not exist: {raw_path}')
        return resolved
    candidate = Path(raw_path)
    resolved = (
        candidate.resolve(strict=False)
        if candidate.is_absolute()
        else (root / candidate).resolve(strict=False)
    )
    if is_repository_path_protected(resolved):
        raise ToolExecutionError(
            'protected_path',
            f'Path is protected from repository tools: {raw_path}',
            details={'path': raw_path},
        )
    if must_exist and not resolved.exists():
        raise ToolExecutionError(
            'path_not_found',
            f'Path does not exist: {raw_path}',
        )
    return resolved


def display_path(root: Path, path: Path) -> str:
    '''Return a relative path in the workspace and an absolute path outside it.'''
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path.as_posix()
    return relative.as_posix() or '.'


CONTROL_PLANE_DIRECTORIES = frozenset({'.forge'})
PUBLIC_ENV_FILES = frozenset({'.env.example'})


def is_repository_path_protected(path: Path) -> bool:
    '''Return whether a repository-relative path is control or secret state.'''
    guard = file_access_guard.get()
    if guard is not None:
        try:
            guard(path)
        except Exception:
            return True
    for part in path.parts:
        name = part.casefold()
        if name in CONTROL_PLANE_DIRECTORIES:
            return True
        if name == '.env':
            return True
        if name.startswith('.env.') and name not in PUBLIC_ENV_FILES:
            return True
    return False

