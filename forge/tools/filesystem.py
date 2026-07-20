'''Repository-scoped directory and file reading tools.'''

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile

from pydantic import Field, model_validator

from forge.tools.base import (
    Tool,
    ToolExecutionError,
    ToolInput,
    ToolResult,
    display_path,
    is_repository_path_protected,
    resolve_repository_path,
)


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
        'Read a UTF-8 repository file with line numbers. Omit start_line and '
        'end_line to read the whole file, or request one necessary inclusive '
        'range. The runtime tracks covered lines; do not change ranges or use '
        'shell commands to re-read content already provided. Re-read a file '
        'only after that file changes or when an uncovered range is needed.'
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
            )
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except UnicodeDecodeError as error:
            raise ToolExecutionError(
                'not_utf8_text',
                f'File is not valid UTF-8 text: {arguments.path}',
            ) from error

        total_lines = len(lines)
        start_index = min(arguments.start_line - 1, total_lines)
        end_line = (
            total_lines
            if arguments.end_line is None
            else min(arguments.end_line, total_lines)
        )
        selected = lines[start_index:end_line]
        numbered = [
            f'{line_number:>6} | {line}'
            for line_number, line in enumerate(
                selected,
                start=arguments.start_line,
            )
        ]
        shown_path = display_path(self.root, path)
        return ToolResult.ok(
            f'Read {len(selected)} lines from {shown_path}.',
            content='\n'.join(numbered),
            metadata={
                'path': shown_path,
                'start_line': arguments.start_line,
                'end_line': end_line,
                'total_lines': total_lines,
            },
        )


class WriteFileInput(ToolInput):
    path: str = Field(min_length=1)
    content: str = Field(max_length=8_000)


class WriteFileTool(Tool[WriteFileInput]):
    name = 'write_file'
    description = (
        'Create or fully replace one small UTF-8 repository text file '
        'atomically. Content is limited to 8000 characters. For larger files, '
        'write a minimal skeleton first, then use replace_text or apply_patch '
        'in multiple focused calls. Do not use this for a small edit to an '
        'existing large file.'
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
        if not path.parent.is_dir():
            raise ToolExecutionError(
                'parent_not_found',
                f'Parent directory does not exist: {arguments.path}',
            )
        existed = path.exists()
        atomic_write_text(path, arguments.content)
        shown_path = display_path(self.root, path)
        action = 'Replaced' if existed else 'Created'
        return ToolResult.ok(
            f'{action} {shown_path} with {len(arguments.content)} characters.',
            metadata={
                'path': shown_path,
                'characters': len(arguments.content),
                'created': not existed,
            },
        )


class ReplaceTextInput(ToolInput):
    path: str = Field(min_length=1)
    old_text: str = Field(min_length=1, max_length=8_000)
    new_text: str = Field(max_length=8_000)


class ReplaceTextTool(Tool[ReplaceTextInput]):
    name = 'replace_text'
    description = (
        'Replace one exact, unique UTF-8 text fragment in an existing '
        'repository file. Both old_text and new_text are limited to 8000 '
        'characters. Use this for a focused edit after reading the relevant '
        'source. If the text is not unique, read a smaller exact region and '
        'retry with more surrounding context. Do not use it to create files.'
    )
    input_model = ReplaceTextInput
    effect = 'workspace_write'

    async def execute(self, arguments: ReplaceTextInput) -> ToolResult:
        return await asyncio.to_thread(self._execute_sync, arguments)

    def _execute_sync(self, arguments: ReplaceTextInput) -> ToolResult:
        path = resolve_repository_path(self.root, arguments.path)
        if not path.is_file():
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
        occurrences = content.count(old_text)
        if occurrences != 1:
            raise ToolExecutionError(
                'text_not_unique',
                'old_text must occur exactly once in '
                f'{arguments.path}; found {occurrences}.',
                details={'occurrences': occurrences},
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
