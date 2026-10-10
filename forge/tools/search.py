'''Filesystem file discovery and text search tools.'''

from __future__ import annotations

import asyncio
import fnmatch
import os
from pathlib import Path
import re
import stat
from dataclasses import dataclass, field
from threading import Event
from time import monotonic
from collections.abc import Iterator

from pydantic import Field, field_validator

from forge.tools.base import (
    Tool,
    ToolExecutionError,
    ToolInput,
    ToolResult,
    display_path,
    file_access_guard,
    is_repository_path_protected,
    resolve_repository_path,
)


@dataclass
class SearchBudget:
    deadline: float
    maximum: int = 20000
    max_depth: int = 32
    cancelled: Event = field(default_factory=Event)
    scanned: int = 0
    skipped: int = 0
    stop_reason: str = ''

    def check(self):
        if not self.stop_reason:
            if self.cancelled.is_set():
                self.stop_reason = 'cancelled'
            elif monotonic() >= self.deadline:
                self.stop_reason = 'time_limit'
        return not self.stop_reason

    def visit(self):
        if not self.check():
            return False
        if self.scanned >= self.maximum:
            self.stop_reason = 'scan_limit'
            return False
        self.scanned += 1
        return True

    def metadata(self):
        return {'scanned_entries': self.scanned, 'skipped_paths': self.skipped,
                'truncated': bool(self.stop_reason), 'stop_reason': self.stop_reason or None,
                'complete': not self.stop_reason and not self.skipped}


def iter_files(path: Path, budget: SearchBudget | None = None) -> Iterator[Path]:
    '''Stream regular files, bounding enumeration before filtering matches.'''
    budget = budget or SearchBudget(monotonic() + 10)
    pending = [(path, 0)]
    while pending and budget.check():
        directory, depth = pending.pop()
        if os.name != 'nt' and any(directory == root or root in directory.parents
                                  for root in (Path('/proc'), Path('/sys'), Path('/dev'))):
            budget.skipped += 1
            continue
        if directory.is_symlink():
            continue
        if directory.is_file():
            if budget.visit():
                yield directory
            continue
        if depth > budget.max_depth:
            budget.skipped += 1
            continue
        files, directories = [], []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if not budget.visit():
                        break
                    if is_repository_path_protected(Path(entry.path)) or entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        directories.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        files.append(Path(entry.path))
                    else:
                        budget.skipped += 1
        except OSError:
            budget.skipped += 1
        # Retain partial enumeration at a limit, but never traverse more nodes.
        for candidate in sorted(files):
            if budget.cancelled.is_set() or monotonic() >= budget.deadline:
                budget.check()
                return
            yield candidate
        pending.extend((child, depth + 1) for child in sorted(directories, reverse=True))


async def bounded_search(callback, arguments):
    cancelled = Event()
    try:
        return await asyncio.wait_for(asyncio.to_thread(callback, arguments, cancelled),
                                      timeout=arguments.timeout_seconds + 0.25)
    except TimeoutError as error:
        raise ToolExecutionError('search_timeout', 'Search time limit reached. Narrow the path or pattern; no exhaustive absence claim can be made.') from error
    finally:
        cancelled.set()


def glob_variants(pattern: str) -> tuple[str, ...]:
    '''Treat every double-star directory segment as matching zero directories.'''
    variants = [pattern]
    pending = [pattern]
    while pending:
        current = pending.pop()
        marker = '**/'
        start = 0
        while True:
            index = current.find(marker, start)
            if index < 0:
                break
            collapsed = current[:index] + current[index + len(marker):]
            if collapsed not in variants:
                variants.append(collapsed)
                pending.append(collapsed)
            start = index + len(marker)
    return tuple(variants)


class FindFilesInput(ToolInput):
    pattern: str = Field(min_length=1)
    path: str = '.'
    max_results: int = Field(default=200, ge=1, le=1000)
    timeout_seconds: float = Field(default=10, gt=0, le=60)
    max_scan_entries: int = Field(default=20000, ge=1, le=500000)
    max_depth: int = Field(default=32, ge=0, le=128)


class FindFilesTool(Tool[FindFilesInput]):
    name = 'find_files'
    description = (
        'Find filesystem files (never directories) by a glob pattern. '
        'A zero result does not mean '
        'the directory tree is empty; use list_directory for directories. Use '
        'a narrow path and pattern, and do not vary extensions after a zero '
        'result when the task is to remove or clear directories.'
    )
    input_model = FindFilesInput

    async def execute(self, arguments: FindFilesInput) -> ToolResult:
        return await bounded_search(self._execute_sync, arguments)

    def _execute_sync(self, arguments: FindFilesInput, cancelled: Event | None = None) -> ToolResult:
        start = resolve_repository_path(self.root, arguments.path)
        matches: list[str] = []
        budget = SearchBudget(monotonic() + arguments.timeout_seconds, arguments.max_scan_entries,
                              arguments.max_depth, cancelled or Event())
        output_bytes = 0
        patterns = glob_variants(arguments.pattern)
        for candidate in iter_files(start, budget):
            relative = display_path(self.root, candidate)
            if any(
                fnmatch.fnmatch(relative, pattern)
                or fnmatch.fnmatch(candidate.name, pattern)
                for pattern in patterns
            ):
                output_bytes += len(relative.encode('utf-8')) + 1
                if output_bytes > 128000:
                    budget.stop_reason = 'output_limit'
                    break
                matches.append(relative)
                if len(matches) >= arguments.max_results:
                    budget.stop_reason = 'result_limit'
                    break

        summary = f'Found {len(matches)} matching files.'
        if budget.stop_reason or budget.skipped:
            summary += ' Search is incomplete; narrow the scope. Zero matches does not establish absence.'
        if not matches:
            summary += (
                ' find_files does not return directories; use the existing '
                'list_directory evidence for directory operations.'
            )
        return ToolResult.ok(
            summary,
            content='\n'.join(matches),
            metadata={
                'pattern': arguments.pattern,
                'path': display_path(self.root, start),
                'match_count': len(matches),
                **budget.metadata(),
            },
        )


class GrepInput(FindFilesInput):
    pattern: str = Field(min_length=1)
    path: str = '.'
    file_types: list[str] = Field(default_factory=list)
    case_sensitive: bool = True
    regex: bool = True
    max_results: int = Field(default=200, ge=1, le=1000)
    max_file_bytes: int = Field(default=2_000_000, ge=1, le=16_000_000)
    max_total_bytes: int = Field(default=16_000_000, ge=1, le=128_000_000)

    @field_validator('file_types')
    @classmethod
    def normalize_file_types(cls, values: list[str]) -> list[str]:
        return [
            value.casefold() if value.startswith('.') else f'.{value.casefold()}'
            for value in values
        ]


class GrepTool(Tool[GrepInput]):
    name = 'grep'
    description = (
        'Search UTF-8 filesystem files and return path, line number, and '
        'matching text. Use it to locate symbols or unknown occurrences before '
        'reading focused files. pattern is a regular expression by default; '
        'set regex=false for literal text containing characters such as '
        'parentheses or brackets. Do not grep a file already read in full, '
        'and do not vary patterns merely to re-display known content.'
    )
    input_model = GrepInput

    async def execute(self, arguments: GrepInput) -> ToolResult:
        return await bounded_search(self._execute_sync, arguments)

    def _execute_sync(self, arguments: GrepInput, cancelled: Event | None = None) -> ToolResult:
        start = resolve_repository_path(self.root, arguments.path)
        flags = 0 if arguments.case_sensitive else re.IGNORECASE
        expression = arguments.pattern if arguments.regex else re.escape(
            arguments.pattern
        )
        try:
            matcher = re.compile(expression, flags)
        except re.error as error:
            raise ToolExecutionError(
                'invalid_pattern',
                f'Invalid regular expression: {error}',
            ) from error

        matches: list[str] = []
        skipped_files = 0
        budget = SearchBudget(monotonic() + arguments.timeout_seconds, arguments.max_scan_entries,
                              arguments.max_depth, cancelled or Event())
        total_bytes = output_bytes = 0
        for candidate in iter_files(start, budget):
            if (
                arguments.file_types
                and candidate.suffix.casefold() not in arguments.file_types
            ):
                continue
            try:
                # Nonblocking open + fstat prevent a file-to-FIFO race from
                # blocking the search worker. Never follow a replaced symlink.
                flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
                guard = file_access_guard.get()
                if guard is not None:
                    guard(candidate)
                descriptor = os.open(candidate, flags)
                with os.fdopen(descriptor, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > arguments.max_file_bytes:
                        skipped_files += 1
                        continue
                    allowed = min(arguments.max_file_bytes, arguments.max_total_bytes - total_bytes)
                    if allowed <= 0:
                        budget.stop_reason = 'byte_limit'
                        break
                    data = guard.read_bytes(candidate, allowed + 1) if guard is not None else stream.read(allowed + 1)
                total_bytes += len(data)
                if len(data) > allowed:
                    skipped_files += 1
                    if total_bytes > arguments.max_total_bytes:
                        budget.stop_reason = 'byte_limit'
                        break
                    continue
                if b'\0' in data:
                    skipped_files += 1
                    continue
                lines = data.decode('utf-8').splitlines()
            except (UnicodeDecodeError, OSError):
                skipped_files += 1
                continue
            relative = display_path(self.root, candidate)
            for line_number, line in enumerate(lines, start=1):
                if not budget.check():
                    break
                if matcher.search(line) is None:
                    continue
                shown_line = line if len(line) <= 500 else f'{line[:497]}...'
                shown = f'{relative}:{line_number}:{shown_line}'
                output_bytes += len(shown.encode('utf-8')) + 1
                if output_bytes > 128000:
                    budget.stop_reason = 'output_limit'
                    break
                matches.append(shown)
                if len(matches) >= arguments.max_results:
                    budget.stop_reason = 'result_limit'
                    break
            if budget.stop_reason:
                break

        return ToolResult.ok(
            f'Found {len(matches)} matching lines.' + (
                ' Search is incomplete; skipped files or limits prevent an exhaustive absence claim.'
                if budget.stop_reason or budget.skipped or skipped_files else ''),
            content='\n'.join(matches),
            metadata={
                'pattern': arguments.pattern,
                'path': display_path(self.root, start),
                'match_count': len(matches),
                'skipped_files': skipped_files,
                **budget.metadata(),
                'complete': not budget.stop_reason and not budget.skipped and not skipped_files,
                'bytes_read': total_bytes,
            },
        )
