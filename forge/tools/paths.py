"""Tool target extraction shared by authorization, checkpoints and dispatch."""
from __future__ import annotations
from pathlib import Path
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

