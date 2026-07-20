'''Unified-diff and Codex-envelope patch application tool.'''

from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path
import re
import shlex

from pydantic import Field

from forge.tools.base import (
    Tool,
    ToolExecutionError,
    ToolInput,
    ToolResult,
    display_path,
    resolve_repository_path,
)
from forge.tools.filesystem import (
    dominant_newline,
    read_text_preserving_newlines,
)
from forge.tools.shell import (
    process_metadata,
    render_process_output,
    run_process,
)


class ApplyPatchInput(ToolInput):
    patch: str = Field(min_length=1, max_length=8_000)


@dataclass(frozen=True, slots=True)
class _EnvelopeOperation:
    kind: str
    path: str
    body: tuple[str, ...]


class _EnvelopeError(ValueError):
    '''A deterministic validation failure in a Codex patch envelope.'''


class ApplyPatchTool(Tool[ApplyPatchInput]):
    name = 'apply_patch'
    description = (
        'Create, modify, or delete repository text files with one focused '
        'patch after validating the complete patch. Accept either a standard '
        'unified diff (--- a/path, +++ b/path, and numbered @@ hunks) or a '
        'Codex envelope (*** Begin Patch with *** Update File, *** Add File, '
        'or *** Delete File sections; Update sections may use bare @@ hunks). '
        'The patch is limited to 8000 characters; split large HTML, CSS, '
        'JavaScript, or source files across multiple calls. Use '
        'repository-relative paths. On patch_rejected, inspect the error and '
        'relevant current lines instead of retrying the same patch. Prefer '
        'replace_text for one exact replacement. Use write_file only for '
        'small full-file content.'
    )
    input_model = ApplyPatchInput
    effect = 'workspace_write'

    async def execute(self, arguments: ApplyPatchInput) -> ToolResult:
        patch_format = 'unified_diff'
        normalized_patch = arguments.patch
        if is_codex_envelope(arguments.patch):
            patch_format = 'codex_envelope'
            try:
                operations = parse_codex_envelope(arguments.patch)
                normalized_patch = build_unified_patch(self.root, operations)
            except (_EnvelopeError, ToolExecutionError) as error:
                return ToolResult.fail(
                    'patch_rejected',
                    'Patch validation failed.',
                    content=str(error),
                    details={'format': patch_format},
                    metadata={'format': patch_format},
                )
        try:
            target_paths = validate_unified_patch_paths(
                self.root,
                normalized_patch,
            )
        except (_EnvelopeError, ToolExecutionError) as error:
            return ToolResult.fail(
                'patch_rejected',
                'Patch validation failed.',
                content=str(error),
                details={'format': patch_format},
                metadata={'format': patch_format},
            )

        check = await run_process(
            ['git', 'apply', '--check', '--whitespace=nowarn', '-'],
            cwd=self.root,
            timeout_seconds=30,
            input_text=normalized_patch,
        )
        if check.exit_code != 0:
            return ToolResult.fail(
                'patch_rejected',
                'Patch validation failed.',
                content=render_process_output(check),
                details={'format': patch_format},
                metadata={
                    'format': patch_format,
                    'check': process_metadata(check),
                },
            )

        applied = await run_process(
            ['git', 'apply', '--whitespace=nowarn', '-'],
            cwd=self.root,
            timeout_seconds=30,
            input_text=normalized_patch,
        )
        if applied.exit_code != 0:
            return ToolResult.fail(
                'patch_apply_failed',
                'Patch could not be applied after validation.',
                content=render_process_output(applied),
                details={'format': patch_format},
                metadata={
                    'format': patch_format,
                    'check': process_metadata(check),
                    'apply': process_metadata(applied),
                },
            )

        status = await run_process(
            ['git', 'status', '--short', '--', *target_paths],
            cwd=self.root,
            timeout_seconds=30,
        )
        status_text = status.stdout.rstrip()
        changed_files = [
            line[3:].strip()
            for line in status.stdout.splitlines()
            if len(line) >= 4
        ]
        return ToolResult.ok(
            f'Applied patch to {len(changed_files)} target path(s).',
            content=status_text,
            metadata={
                'format': patch_format,
                'target_paths': list(target_paths),
                'changed_files': changed_files,
                'check': process_metadata(check),
                'apply': process_metadata(applied),
                'status': process_metadata(status),
            },
        )


def validate_unified_patch_paths(
    root: Path,
    patch: str,
) -> tuple[str, ...]:
    '''Validate every source and destination named by a unified Diff.'''
    targets: list[str] = []
    for raw_path in unified_patch_paths(patch):
        normalized = raw_path.replace('\\', '/')
        if normalized == '/dev/null':
            continue
        if normalized.startswith(('a/', 'b/')):
            normalized = normalized[2:]
        if not normalized:
            raise _EnvelopeError('Unified Diff contains an empty file path.')
        resolved = resolve_repository_path(
            root,
            normalized,
            must_exist=False,
        )
        targets.append(display_path(root, resolved))
    normalized_targets = tuple(dict.fromkeys(targets))
    if not normalized_targets:
        raise _EnvelopeError(
            'Patch does not declare any repository file targets.'
        )
    return normalized_targets


def unified_patch_paths(patch: str) -> tuple[str, ...]:
    '''Extract paths from diff --git and ---/+++ file headers.'''
    paths: list[str] = []
    for line_number, line in enumerate(patch.splitlines(), start=1):
        if line.startswith('diff --git '):
            paths.extend(parse_diff_git_header(line, line_number))
        elif line.startswith(('--- ', '+++ ')):
            paths.append(parse_unified_file_header(line, line_number))
    return tuple(dict.fromkeys(paths))


def parse_diff_git_header(
    line: str,
    line_number: int,
) -> tuple[str, str]:
    try:
        fields = shlex.split(line)
    except ValueError as error:
        raise _EnvelopeError(
            f'Invalid diff --git header at patch line {line_number}.'
        ) from error
    if len(fields) != 4:
        raise _EnvelopeError(
            f'Invalid diff --git header at patch line {line_number}.'
        )
    return fields[2], fields[3]


def parse_unified_file_header(line: str, line_number: int) -> str:
    payload = line[4:]
    if payload.startswith(chr(34)):
        try:
            fields = shlex.split(payload)
        except ValueError as error:
            raise _EnvelopeError(
                f'Invalid quoted file header at patch line {line_number}.'
            ) from error
        if not fields:
            raise _EnvelopeError(
                f'Empty file header at patch line {line_number}.'
            )
        return fields[0]
    path = payload.split('\t', 1)[0].rstrip()
    if not path:
        raise _EnvelopeError(
            f'Empty file header at patch line {line_number}.'
        )
    return path


def is_codex_envelope(patch: str) -> bool:
    return patch.lstrip('\ufeff\r\n').startswith('*** Begin Patch')


def parse_codex_envelope(patch: str) -> tuple[_EnvelopeOperation, ...]:
    '''Parse the small, text-only Codex patch envelope deterministically.'''
    lines = patch.lstrip('\ufeff\r\n').splitlines()
    if not lines or lines[0] != '*** Begin Patch':
        raise _EnvelopeError('Codex patch must start with *** Begin Patch.')
    if lines[-1] != '*** End Patch':
        raise _EnvelopeError('Codex patch must end with *** End Patch.')

    header = re.compile(r'^\*\*\* (Update|Add|Delete) File: (.+)$')
    operations: list[_EnvelopeOperation] = []
    seen_paths: set[str] = set()
    index = 1
    while index < len(lines) - 1:
        match = header.fullmatch(lines[index])
        if match is None:
            raise _EnvelopeError(
                f'Expected an Update/Add/Delete File header at envelope line '
                f'{index + 1}: {lines[index]!r}.'
            )
        kind = match.group(1).casefold()
        path = match.group(2).strip().replace('\\', '/')
        if not path or '\n' in path or '\r' in path:
            raise _EnvelopeError('Patch file path must not be empty.')
        if path in seen_paths:
            raise _EnvelopeError(
                f'Codex patch contains multiple operations for {path!r}.'
            )
        seen_paths.add(path)

        index += 1
        body: list[str] = []
        while index < len(lines) - 1 and header.fullmatch(lines[index]) is None:
            if lines[index].startswith('*** ') and lines[index] != '*** End of File':
                raise _EnvelopeError(
                    f'Unsupported Codex patch directive: {lines[index]!r}.'
                )
            body.append(lines[index])
            index += 1
        operations.append(_EnvelopeOperation(kind, path, tuple(body)))

    if not operations:
        raise _EnvelopeError('Codex patch does not contain any file operation.')
    return tuple(operations)


def build_unified_patch(
    root: Path,
    operations: tuple[_EnvelopeOperation, ...],
) -> str:
    '''Validate all envelope operations in memory, then create one Git patch.'''
    changes: list[tuple[str, str | None, str | None]] = []
    for operation in operations:
        if operation.kind == 'add':
            path = resolve_repository_path(
                root,
                operation.path,
                must_exist=False,
            )
            if path.exists():
                raise _EnvelopeError(
                    f'Cannot add {operation.path!r}: path already exists.'
                )
            if not path.parent.is_dir():
                raise _EnvelopeError(
                    f'Cannot add {operation.path!r}: parent directory does '
                    'not exist.'
                )
            after = parse_added_file(operation)
            changes.append((display_path(root, path), None, after))
            continue

        path = resolve_repository_path(root, operation.path)
        if not path.is_file():
            raise _EnvelopeError(
                f'Patch target is not a file: {operation.path!r}.'
            )
        try:
            before = read_text_preserving_newlines(path)
        except UnicodeDecodeError as error:
            raise _EnvelopeError(
                f'Patch target is not UTF-8 text: {operation.path!r}.'
            ) from error

        if operation.kind == 'delete':
            meaningful = [
                line for line in operation.body if line != '*** End of File'
            ]
            if meaningful:
                raise _EnvelopeError(
                    'Delete File sections must not contain patch hunks.'
                )
            changes.append((display_path(root, path), before, None))
            continue

        after = apply_update_hunks(before, operation)
        if after == before:
            raise _EnvelopeError(
                f'Update for {operation.path!r} does not change the file.'
            )
        changes.append((display_path(root, path), before, after))

    patch_parts = [render_unified_change(*change) for change in changes]
    normalized = ''.join(patch_parts)
    if not normalized:
        raise _EnvelopeError('Codex patch does not produce any changes.')
    return normalized


def parse_added_file(operation: _EnvelopeOperation) -> str:
    lines: list[str] = []
    for line in operation.body:
        if line == '*** End of File':
            continue
        if not line.startswith('+'):
            raise _EnvelopeError(
                f'Add File line must start with + for {operation.path!r}: '
                f'{line!r}.'
            )
        lines.append(line[1:])
    if not lines:
        raise _EnvelopeError(
            f'Add File section for {operation.path!r} is empty.'
        )
    return '\n'.join(lines) + '\n'


def apply_update_hunks(before: str, operation: _EnvelopeOperation) -> str:
    hunks = split_update_hunks(operation)
    newline = dominant_newline(before)
    final_newline = before.endswith(('\n', '\r'))
    current = before.splitlines()
    cursor = 0
    for hunk_number, hunk in enumerate(hunks, start=1):
        old_lines: list[str] = []
        new_lines: list[str] = []
        for line in hunk:
            if not line or line[0] not in {' ', '+', '-'}:
                raise _EnvelopeError(
                    f'Invalid line in hunk {hunk_number} for '
                    f'{operation.path!r}: {line!r}.'
                )
            if line[0] in {' ', '-'}:
                old_lines.append(line[1:])
            if line[0] in {' ', '+'}:
                new_lines.append(line[1:])
        if not old_lines:
            raise _EnvelopeError(
                f'Update hunk {hunk_number} for {operation.path!r} needs '
                'at least one context or removed line.'
            )
        position = find_unique_sequence(
            current,
            old_lines,
            start=cursor,
            path=operation.path,
            hunk_number=hunk_number,
        )
        current[position:position + len(old_lines)] = new_lines
        cursor = position + len(new_lines)

    result = newline.join(current)
    if final_newline:
        result += newline
    return result


def split_update_hunks(
    operation: _EnvelopeOperation,
) -> tuple[tuple[str, ...], ...]:
    hunks: list[tuple[str, ...]] = []
    current: list[str] | None = None
    for line in operation.body:
        if line == '*** End of File':
            continue
        if line.startswith('@@'):
            if current is not None:
                if not current:
                    raise _EnvelopeError(
                        f'Empty update hunk for {operation.path!r}.'
                    )
                hunks.append(tuple(current))
            current = []
            continue
        if current is None:
            raise _EnvelopeError(
                f'Update File section for {operation.path!r} must begin '
                'with an @@ hunk.'
            )
        current.append(line)
    if current is not None:
        if not current:
            raise _EnvelopeError(f'Empty update hunk for {operation.path!r}.')
        hunks.append(tuple(current))
    if not hunks:
        raise _EnvelopeError(
            f'Update File section for {operation.path!r} has no hunks.'
        )
    return tuple(hunks)


def find_unique_sequence(
    lines: list[str],
    needle: list[str],
    *,
    start: int,
    path: str,
    hunk_number: int,
) -> int:
    candidates = [
        index
        for index in range(start, len(lines) - len(needle) + 1)
        if lines[index:index + len(needle)] == needle
    ]
    if not candidates:
        raise _EnvelopeError(
            f'Update hunk {hunk_number} does not match current content in '
            f'{path!r}.'
        )
    if len(candidates) > 1:
        raise _EnvelopeError(
            f'Update hunk {hunk_number} is ambiguous in {path!r}; include '
            'more unchanged context lines.'
        )
    return candidates[0]


def render_unified_change(
    path: str,
    before: str | None,
    after: str | None,
) -> str:
    from_file = '/dev/null' if before is None else f'a/{path}'
    to_file = '/dev/null' if after is None else f'b/{path}'
    return ''.join(
        difflib.unified_diff(
            [] if before is None else before.splitlines(keepends=True),
            [] if after is None else after.splitlines(keepends=True),
            fromfile=from_file,
            tofile=to_file,
        )
    )
