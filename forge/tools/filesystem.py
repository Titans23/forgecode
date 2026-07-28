'''Repository-scoped directory and file reading tools.'''

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import difflib
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile

from pydantic import Field, model_validator

from forge.permissions.policy import PermissionRequest
from forge.tools.base import (
    Tool,
    ToolExecutionError,
    ToolInput,
    ToolResult,
    display_path,
    is_repository_path_protected,
    resolve_repository_path,
)


MAX_EDIT_CHARACTERS = 30_000
MAX_READ_LINES = 400
MAX_CHUNKED_FILE_CHARACTERS = 1_000_000


class ListDirectoryInput(ToolInput):
    path: str = '.'
    max_results: int = Field(default=1_000, ge=1, le=1_000)


class ListDirectoryTool(Tool[ListDirectoryInput]):
    name = 'list_directory'
    description = (
        'List the direct children of one repository directory. Use it to '
        'discover immediate structure, not file contents or recursive trees. '
        'Do not repeat it unless that directory may have changed.'
    )
    input_model = ListDirectoryInput

    async def execute(self, arguments: ListDirectoryInput) -> ToolResult:
        return await asyncio.to_thread(self._execute_sync, arguments)

    def _execute_sync(self, arguments: ListDirectoryInput) -> ToolResult:
        directory = resolve_repository_path(self.root, arguments.path)
        if not directory.is_dir():
            raise ToolExecutionError(
                'not_a_directory',
                f'Path is not a directory: {arguments.path}',
            )

        entries = sorted(
            (
                entry
                for entry in directory.iterdir()
                if not is_repository_path_protected(
                    entry.relative_to(self.root)
                )
            ),
            key=lambda path: (not path.is_dir(), path.name.casefold()),
        )
        total = len(entries)
        entries = entries[: arguments.max_results]
        truncated = len(entries) < total
        lines = [
            f'{entry.name}/' if entry.is_dir() else entry.name
            for entry in entries
        ]
        shown_path = display_path(self.root, directory)
        return ToolResult.ok(
            f'Listed {len(entries)} entries in {shown_path}.',
            content='\n'.join(lines),
            metadata={
                'path': shown_path,
                'entry_count': len(entries),
                'total': total,
                'truncated': truncated,
            },
        )


class CreateDirectoryInput(ToolInput):
    path: str = Field(min_length=1)


class CreateDirectoryTool(Tool[CreateDirectoryInput]):
    name = 'create_directory'
    description = (
        'Create one repository directory, including missing parent directories. '
        'Workspace tracking records empty directories directly, so do not add '
        'a .gitkeep marker unless the repository explicitly requires one. Do not '
        'use run_command with mkdir or New-Item for this operation.'
    )
    input_model = CreateDirectoryInput
    effect = 'workspace_write'

    async def execute(self, arguments: CreateDirectoryInput) -> ToolResult:
        return await asyncio.to_thread(self._execute_sync, arguments)

    def _execute_sync(self, arguments: CreateDirectoryInput) -> ToolResult:
        directory = resolve_repository_path(
            self.root,
            arguments.path,
            must_exist=False,
        )
        if directory.exists():
            if not directory.is_dir():
                raise ToolExecutionError(
                    'not_a_directory',
                    f'Path is not a directory: {arguments.path}',
                )
            raise ToolExecutionError(
                'directory_already_exists',
                f'Directory already exists: {arguments.path}',
                details={
                    'path': arguments.path,
                    'recommended_tool': 'write_file',
                    'recovery': (
                        'Create or edit a concrete file inside this directory; '
                        'do not call create_directory for it again.'
                    ),
                },
            )
        directory.mkdir(parents=True, exist_ok=True)
        shown_path = display_path(self.root, directory)
        return ToolResult.ok(
            f'Created directory {shown_path}.',
            metadata={'path': shown_path},
        )


class RemoveDirectoryInput(ToolInput):
    path: str = Field(min_length=1)
    recursive: bool = False
    contents_only: bool = False

    @model_validator(mode='after')
    def validate_contents_only(self) -> RemoveDirectoryInput:
        if self.contents_only and not self.recursive:
            raise ValueError('contents_only=true requires recursive=true')
        return self


class RemoveDirectoryTool(Tool[RemoveDirectoryInput]):
    name = 'remove_directory'
    description = (
        'Delete one repository directory after the user explicitly authorizes '
        'deletion. To empty a directory while keeping that directory, make one '
        'call on the parent with recursive=true and contents_only=true; do not '
        'inspect or delete each child separately. Otherwise recursive=true '
        'removes the directory and all contents. Use apply_patch to delete '
        'individual files; do not pass a directory to apply_patch. Repository '
        'root, control-plane paths, and directories containing symbolic links '
        'are always rejected.'
    )
    input_model = RemoveDirectoryInput
    effect = 'workspace_write'

    def permission_request(
        self,
        arguments: Mapping[str, object],
    ) -> PermissionRequest | None:
        raw_path = str(arguments.get('path', '')).replace('\\', '/')
        return PermissionRequest(
            tool_name=self.name,
            capability='file.delete',
            risk='high',
            targets=(raw_path,) if raw_path else (),
            reason='Removing a directory can discard repository state.',
            preview=(
                f'remove_directory path={raw_path!r} '
                f'recursive={bool(arguments.get("recursive", False))} '
                f'contents_only={bool(arguments.get("contents_only", False))}'
            ),
        )

    async def execute(self, arguments: RemoveDirectoryInput) -> ToolResult:
        return await asyncio.to_thread(self._execute_sync, arguments)

    def _execute_sync(self, arguments: RemoveDirectoryInput) -> ToolResult:
        directory = resolve_repository_path(self.root, arguments.path)
        if directory == self.root:
            raise ToolExecutionError(
                'protected_path',
                'Repository root cannot be removed.',
            )
        lexical = self.root
        for part in Path(arguments.path).parts:
            lexical /= part
            if lexical.is_symlink():
                raise ToolExecutionError(
                    'symbolic_link_denied',
                    f'Directory path contains a symbolic link: {arguments.path}',
                )
        if not directory.is_dir():
            raise ToolExecutionError(
                'not_a_directory',
                f'Path is not a directory: {arguments.path}',
            )
        symbolic_links = [
            display_path(self.root, path)
            for path in directory.rglob('*')
            if path.is_symlink()
            or (
                hasattr(path, 'is_junction')
                and path.is_junction()
            )
        ]
        if symbolic_links:
            raise ToolExecutionError(
                'symbolic_link_denied',
                'Directory contains symbolic links or junctions and was not removed.',
                details={'paths': symbolic_links[:20]},
            )
        shown_path = display_path(self.root, directory)
        if arguments.recursive:
            entry_count = sum(1 for _ in directory.rglob('*'))
            if arguments.contents_only:
                for child in directory.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
            else:
                shutil.rmtree(directory)
        else:
            try:
                directory.rmdir()
            except OSError as error:
                raise ToolExecutionError(
                    'directory_not_empty',
                    f'Directory is not empty: {shown_path}',
                    details={
                        'path': shown_path,
                        'recovery': (
                            'Inspect the directory once, then retry '
                            'remove_directory with recursive=true only if every '
                            'entry should be deleted.'
                        ),
                    },
                ) from error
            entry_count = 0
        action = (
            'Cleared directory contents'
            if arguments.contents_only
            else 'Removed directory'
        )
        return ToolResult.ok(
            f'{action} {shown_path}.',
            metadata={
                'path': shown_path,
                'recursive': arguments.recursive,
                'contents_only': arguments.contents_only,
                'removed_entries': entry_count,
            },
        )


class ReadFileInput(ToolInput):
    path: str = Field(min_length=1)
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def validate_line_range(self) -> ReadFileInput:
        if self.end_line is not None and self.end_line < self.start_line:
            raise ValueError('end_line must be greater than or equal to start_line')
        return self


class ReadFileTool(Tool[ReadFileInput]):
    name = 'read_file'
    description = (
        'Read at most 400 numbered lines from one UTF-8 repository file. '
        'Requests covering more than 400 lines return the first 400 lines and '
        'continuation metadata instead of failing. Prefer a focused inclusive '
        'start_line/end_line range for large files. The '
        'runtime replays covered ranges from its cache, so re-read only after '
        'the file changes or when an uncovered range is needed.'
    )
    input_model = ReadFileInput

    async def execute(self, arguments: ReadFileInput) -> ToolResult:
        return await asyncio.to_thread(self._execute_sync, arguments)

    def _execute_sync(self, arguments: ReadFileInput) -> ToolResult:
        path = resolve_repository_path(self.root, arguments.path)
        if not path.is_file():
            raise ToolExecutionError(
                'not_a_file',
                f'Path is not a file: {arguments.path}',
                details={
                    'path': arguments.path,
                    'recommended_tool': 'list_directory',
                    'recovery': (
                        'Use list_directory to inspect this path. If the task '
                        'is to delete or clear the directory, call '
                        'remove_directory instead of read_file.'
                    ),
                },
            )
        try:
            content = read_text_preserving_newlines(path)
        except UnicodeDecodeError as error:
            raise ToolExecutionError(
                'not_utf8_text',
                f'File is not valid UTF-8 text: {arguments.path}',
            ) from error

        lines_with_endings = content.splitlines(keepends=True)
        lines = content.splitlines()
        total_lines = len(lines_with_endings)
        start_index = min(arguments.start_line - 1, total_lines)
        requested_end_line = (
            total_lines
            if arguments.end_line is None
            else min(arguments.end_line, total_lines)
        )
        end_line = min(
            requested_end_line,
            arguments.start_line + MAX_READ_LINES - 1,
        )
        truncated = end_line < requested_end_line
        selected = lines[start_index:end_line]
        numbered = [
            f'{line_number:>6} | {line}'
            for line_number, line in enumerate(
                selected,
                start=arguments.start_line,
            )
        ]
        shown_path = display_path(self.root, path)
        summary = f'Read {len(selected)} lines from {shown_path}.'
        if truncated:
            summary += (
                f' Truncated requested range at line {end_line}; continue '
                f'with start_line={end_line + 1}.'
            )
        return ToolResult.ok(
            summary,
            content='\n'.join(numbered),
            metadata={
                'path': shown_path,
                'start_line': arguments.start_line,
                'end_line': end_line,
                'total_lines': total_lines,
                **(
                    {
                        'requested_end_line': requested_end_line,
                        'truncated': True,
                        'next_start_line': end_line + 1,
                    }
                    if truncated
                    else {}
                ),
                'sha256': hashlib.sha256(
                    content.encode('utf-8')
                ).hexdigest(),
                'characters': len(content),
            },
        )


class WriteFileInput(ToolInput):
    path: str = Field(min_length=1)
    content: str = Field(max_length=MAX_EDIT_CHARACTERS)


class WriteFileTool(Tool[WriteFileInput]):
    name = 'write_file'
    description = (
        'Create one new UTF-8 repository text file, or initialize an existing '
        'empty/whitespace-only UTF-8 placeholder, atomically with content '
        'limited to 30000 characters. This tool never overwrites a non-empty '
        'file and safely creates missing repository-relative parent '
        'directories; use apply_patch for focused changes to non-empty files. '
        'For a larger new file, create a focused skeleton and extend it with '
        'apply_patch calls.'
    )
    input_model = WriteFileInput
    effect = 'workspace_write'

    async def execute(self, arguments: WriteFileInput) -> ToolResult:
        return await asyncio.to_thread(self._execute_sync, arguments)

    def _execute_sync(self, arguments: WriteFileInput) -> ToolResult:
        path = resolve_repository_path(
            self.root,
            arguments.path,
            must_exist=False,
        )
        if path.exists() and not path.is_file():
            raise ToolExecutionError(
                'not_a_file',
                f'Path is not a file: {arguments.path}',
            )
        existed = path.exists()
        initialized_placeholder = False
        if existed:
            try:
                current = read_text_preserving_newlines(path)
            except UnicodeDecodeError as error:
                raise ToolExecutionError(
                    'not_utf8_text',
                    f'File is not valid UTF-8 text: {arguments.path}',
                ) from error
            if current.strip():
                raise ToolExecutionError(
                    'file_already_exists',
                    f'write_file will not overwrite non-empty file '
                    f'{arguments.path}. Use apply_patch or replace_text for a '
                    'focused change. For a deliberate whole-file replacement '
                    'after failed focused edits, use write_file_chunk with '
                    'offset=0, truncate=true, final=false, then append the '
                    'remaining final chunk.',
                    details={
                        'path': arguments.path,
                        'existing_characters': len(current),
                        'recommended_tools': [
                            'apply_patch',
                            'replace_text',
                            'write_file_chunk',
                        ],
                    },
                )
            initialized_placeholder = True
        ensure_parent_directory(path, arguments.path)
        atomic_write_text(path, arguments.content)
        shown_path = display_path(self.root, path)
        action = 'Initialized' if initialized_placeholder else 'Created'
        return ToolResult.ok(
            f'{action} {shown_path} with {len(arguments.content)} characters.',
            metadata={
                'path': shown_path,
                'characters': len(arguments.content),
                'sha256': hashlib.sha256(
                    arguments.content.encode('utf-8')
                ).hexdigest(),
                'created': not existed,
                'initialized_placeholder': initialized_placeholder,
            },
        )


class WriteFileChunkInput(ToolInput):
    path: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=MAX_EDIT_CHARACTERS)
    offset: int = Field(ge=0)
    truncate: bool = False
    final: bool = False
    expected_sha256: str | None = Field(
        default=None,
        pattern=r'^[0-9a-fA-F]{64}$',
    )

    @model_validator(mode='after')
    def validate_chunk_protocol(self) -> WriteFileChunkInput:
        if self.truncate and self.offset != 0:
            raise ValueError('truncate=true requires offset=0')
        if self.expected_sha256 is not None and not self.final:
            raise ValueError('expected_sha256 requires final=true')
        return self


class WriteFileChunkTool(Tool[WriteFileChunkInput]):
    name = 'write_file_chunk'
    description = (
        'Create or extend one UTF-8 repository file in ordered chunks of at '
        'most 30000 characters. Start a new or multi-chunk replacement file '
        'with offset=0, truncate=true, and final=false. A single final chunk '
        'cannot replace an existing non-empty file; use apply_patch for that. '
        'For every later chunk, set offset to '
        'the exact next_offset returned by the previous call. Each chunk is '
        'applied atomically and an offset mismatch is rejected without '
        'writing. Set final=true on the last chunk and optionally provide '
        'expected_sha256 for whole-file integrity. Total file size is '
        'limited to 1000000 characters. A new file safely creates missing '
        'repository-relative parent directories.'
    )
    input_model = WriteFileChunkInput
    effect = 'workspace_write'

    async def execute(self, arguments: WriteFileChunkInput) -> ToolResult:
        return await asyncio.to_thread(self._execute_sync, arguments)

    def _execute_sync(self, arguments: WriteFileChunkInput) -> ToolResult:
        path = resolve_repository_path(
            self.root,
            arguments.path,
            must_exist=False,
        )
        if path.exists() and not path.is_file():
            raise ToolExecutionError(
                'not_a_file',
                f'Path is not a file: {arguments.path}',
            )
        existed = path.exists()
        if (
            existed
            and arguments.truncate
            and arguments.final
            and path.stat().st_size > 0
        ):
            raise ToolExecutionError(
                'single_chunk_overwrite_denied',
                'write_file_chunk cannot replace an existing non-empty file '
                'with one final chunk. Use apply_patch for a focused change, '
                'or start a genuine multi-chunk replacement with final=false.',
                details={'path': arguments.path},
            )
        if arguments.truncate:
            existing = ''
        elif existed:
            try:
                existing = read_text_preserving_newlines(path)
            except UnicodeDecodeError as error:
                raise ToolExecutionError(
                    'not_utf8_text',
                    f'File is not valid UTF-8 text: {arguments.path}',
                ) from error
        else:
            existing = ''

        actual_offset = len(existing)
        if actual_offset != arguments.offset:
            raise ToolExecutionError(
                'chunk_offset_mismatch',
                f'Chunk offset {arguments.offset} does not match current '
                f'file length {actual_offset} for {arguments.path}.',
                details={
                    'expected_offset': actual_offset,
                    'received_offset': arguments.offset,
                },
            )

        updated = existing + arguments.content
        if len(updated) > MAX_CHUNKED_FILE_CHARACTERS:
            raise ToolExecutionError(
                'chunked_file_too_large',
                f'Chunked file would contain {len(updated)} characters; '
                f'maximum is {MAX_CHUNKED_FILE_CHARACTERS}.',
            )
        digest = hashlib.sha256(updated.encode('utf-8')).hexdigest()
        if (
            arguments.expected_sha256 is not None
            and digest != arguments.expected_sha256.casefold()
        ):
            raise ToolExecutionError(
                'chunk_hash_mismatch',
                'Final chunk SHA-256 does not match the assembled file.',
                details={
                    'expected_sha256': arguments.expected_sha256.casefold(),
                    'actual_sha256': digest,
                },
            )

        ensure_parent_directory(path, arguments.path)
        atomic_write_text(path, updated)
        shown_path = display_path(self.root, path)
        return ToolResult.ok(
            f'Wrote chunk at offset {arguments.offset} to {shown_path}; '
            f'next offset is {len(updated)}.',
            metadata={
                'path': shown_path,
                'offset': arguments.offset,
                'chunk_characters': len(arguments.content),
                'next_offset': len(updated),
                'created': not existed,
                'truncated': arguments.truncate,
                'final': arguments.final,
                'sha256': digest,
            },
        )


class ReplaceTextInput(ToolInput):
    path: str = Field(min_length=1)
    old_text: str = Field(min_length=1, max_length=MAX_EDIT_CHARACTERS)
    new_text: str = Field(max_length=MAX_EDIT_CHARACTERS)

    @model_validator(mode='after')
    def validate_real_change(self) -> ReplaceTextInput:
        if self.old_text == self.new_text:
            raise ValueError('new_text must differ from old_text')
        return self


class ReplaceTextTool(Tool[ReplaceTextInput]):
    name = 'replace_text'
    description = (
        'Replace one exact, unique UTF-8 text fragment in an existing '
        'repository file. Both old_text and new_text are limited to 30000 '
        'characters. Use this for a focused edit after reading the relevant '
        'source. If old_text is missing, the error returns the closest exact '
        'current text when it is small enough; copy that text directly into '
        'the next old_text instead of re-reading or guessing whitespace. Do '
        'not use it to create files.'
    )
    input_model = ReplaceTextInput
    effect = 'workspace_write'

    async def execute(self, arguments: ReplaceTextInput) -> ToolResult:
        return await asyncio.to_thread(self._execute_sync, arguments)

    def _execute_sync(self, arguments: ReplaceTextInput) -> ToolResult:
        path = resolve_repository_path(self.root, arguments.path)
        if not path.is_file():
            if path.is_dir():
                raise ToolExecutionError(
                    'not_a_file',
                    f'Path is a directory, not a file: {arguments.path}',
                    details={
                        'path': arguments.path,
                        'recommended_tool': 'list_directory',
                        'recovery': (
                            'Use list_directory to inspect it, or '
                            'remove_directory to delete or clear it.'
                        ),
                    },
                )
            raise ToolExecutionError(
                'not_a_file',
                f'Path is not a file: {arguments.path}',
            )
        try:
            content = read_text_preserving_newlines(path)
        except UnicodeDecodeError as error:
            raise ToolExecutionError(
                'not_utf8_text',
                f'File is not valid UTF-8 text: {arguments.path}',
            ) from error
        newline = dominant_newline(content)
        old_text = convert_newlines(arguments.old_text, newline)
        new_text = convert_newlines(arguments.new_text, newline)
        if old_text == new_text:
            raise ToolExecutionError(
                'text_no_change',
                'new_text must differ from old_text after newline normalization.',
            )
        occurrences = content.count(old_text)
        if occurrences == 0:
            diagnostic = closest_text_diagnostic(content, old_text)
            closest_start = diagnostic.get('closest_start_line')
            location = ''
            if closest_start is not None:
                location = (
                    ' Closest candidate: lines {}-{} with similarity {:.2f}.'
                ).format(
                    closest_start,
                    diagnostic['closest_end_line'],
                    diagnostic['similarity'],
                )
            whitespace = (
                ' The difference appears to be whitespace-only.'
                if diagnostic['whitespace_only_mismatch']
                else ''
            )
            closest_text = diagnostic.get('closest_text')
            copy_hint = (
                '\nClosest current text (copy exactly as the next old_text):'
                f'\n---\n{closest_text}\n---'
                if isinstance(closest_text, str)
                else ''
            )
            raise ToolExecutionError(
                'text_not_found',
                f'old_text was not found in {arguments.path}.'
                f'{location}{whitespace}{copy_hint}',
                details=diagnostic,
            )
        if occurrences > 1:
            raise ToolExecutionError(
                'text_not_unique',
                'old_text must occur exactly once in '
                f'{arguments.path}; found {occurrences}.',
                details={
                    'occurrences': occurrences,
                    'recovery': (
                        'Include more unchanged surrounding context copied '
                        'from the current file.'
                    ),
                },
            )
        updated = content.replace(
            old_text,
            new_text,
            1,
        )
        atomic_write_text(path, updated)
        shown_path = display_path(self.root, path)
        return ToolResult.ok(
            f'Replaced one text fragment in {shown_path}.',
            metadata={
                'path': shown_path,
                'old_characters': len(arguments.old_text),
                'new_characters': len(arguments.new_text),
            },
        )


def closest_text_diagnostic(content: str, old_text: str) -> dict[str, object]:
    '''Describe a near miss without ever applying a fuzzy replacement.'''
    normalized_old = re.sub(r'\s+', '', old_text)
    normalized_content = re.sub(r'\s+', '', content)
    whitespace_only = bool(
        normalized_old and normalized_old in normalized_content
    )
    content_lines = content.splitlines()
    old_lines = old_text.splitlines() or [old_text]
    window_size = max(1, len(old_lines))
    positions = max(0, len(content_lines) - window_size + 1)
    if positions == 0:
        return {
            'occurrences': 0,
            'whitespace_only_mismatch': whitespace_only,
            'closest_start_line': None,
            'closest_end_line': None,
            'similarity': 0.0,
            'closest_text': None,
        }

    step = max(1, (positions + 1_999) // 2_000)
    sampled = list(range(0, positions, step))
    if sampled[-1] != positions - 1:
        sampled.append(positions - 1)
    comparison = convert_newlines(old_text, '\n')[:2_000]
    best_index = 0
    best_ratio = -1.0
    for index in sampled:
        candidate = '\n'.join(
            content_lines[index:index + window_size]
        )
        ratio = difflib.SequenceMatcher(
            None,
            comparison,
            candidate[:2_000],
            autojunk=False,
        ).ratio()
        if ratio > best_ratio:
            best_index = index
            best_ratio = ratio
    closest_text = '\n'.join(
        content_lines[best_index:best_index + window_size]
    )
    return {
        'occurrences': 0,
        'whitespace_only_mismatch': whitespace_only,
        'closest_start_line': best_index + 1,
        'closest_end_line': best_index + window_size,
        'similarity': round(best_ratio, 4),
        'closest_text': closest_text if len(closest_text) <= 2_000 else None,
    }


def ensure_parent_directory(path: Path, raw_path: str) -> None:
    '''Create validated repository-relative parent directories when needed.'''
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ToolExecutionError(
            'parent_create_failed',
            f'Could not create parent directory for: {raw_path}',
            details={'path': raw_path},
        ) from error
    if not path.parent.is_dir():
        raise ToolExecutionError(
            'parent_create_failed',
            f'Parent path is not a directory: {raw_path}',
            details={'path': raw_path},
        )


def atomic_write_text(path: Path, content: str) -> None:
    '''Replace one text file without exposing a partially written result.'''
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            newline='',
            dir=path.parent,
            prefix=f'.{path.name}.',
            suffix='.forge-tmp',
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def read_text_preserving_newlines(path: Path) -> str:
    '''Read UTF-8 text without universal-newline conversion.'''
    with path.open('r', encoding='utf-8', newline='') as source:
        return source.read()


def dominant_newline(content: str) -> str:
    '''Return the most common newline sequence, defaulting to LF.'''
    crlf_count = content.count('\r\n')
    without_crlf = content.replace('\r\n', '')
    candidates = (
        ('\r\n', crlf_count),
        ('\n', without_crlf.count('\n')),
        ('\r', without_crlf.count('\r')),
    )
    newline, count = max(candidates, key=lambda item: item[1])
    return newline if count else '\n'


def convert_newlines(content: str, newline: str) -> str:
    '''Convert caller-provided text to one target newline sequence.'''
    normalized = content.replace('\r\n', '\n').replace('\r', '\n')
    return normalized.replace('\n', newline)
