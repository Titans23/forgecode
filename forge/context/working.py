'''Structured, task-local evidence kept outside raw conversation history.'''

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from forge.runtime.state import ToolCall
from forge.tools.base import ToolResult


@dataclass(slots=True)
class FileEvidence:
    path: str
    revision: int
    total_lines: int
    covered_ranges: list[tuple[int, int]] = field(default_factory=list)

    def covers(self, start_line: int, end_line: int) -> bool:
        return any(
            start <= start_line and end >= end_line
            for start, end in self.covered_ranges
        )

    def add(self, start_line: int, end_line: int) -> None:
        if end_line < start_line or self.covers(start_line, end_line):
            return
        ranges = sorted([*self.covered_ranges, (start_line, end_line)])
        merged: list[tuple[int, int]] = []
        for start, end in ranges:
            if not merged or start > merged[-1][1] + 1:
                merged.append((start, end))
                continue
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        self.covered_ranges = merged


class WorkingState:
    '''Index observed paths, covered ranges and failures for model context.'''

    MAX_VISIBLE_FILES = 50
    MAX_VISIBLE_DIRECTORIES = 20
    MAX_VISIBLE_SEARCH_HITS = 50
    MAX_VISIBLE_DISCOVERED_FILES = 50
    EXTERNAL_BLOCKER_CODES = frozenset(
        {
            'approval_required',
            'authentication_required',
            'permission_denied',
            'network_unavailable',
            'dependency_unavailable',
        }
    )

    def __init__(self) -> None:
        self.files: dict[tuple[int, str], FileEvidence] = {}
        self.directories: set[tuple[int, str]] = set()
        self.search_hits: set[tuple[int, str, int]] = set()
        self.discovered_files: set[tuple[int, str]] = set()
        self.failures: list[str] = []
        self.latest_failure_code: str | None = None

    def observe(
        self,
        tool_call: ToolCall,
        result: ToolResult,
        revision: int,
    ) -> None:
        if not result.success:
            if result.error is not None:
                self.latest_failure_code = result.error.code
                self.failures.append(f'{tool_call.name}: {result.error.code}')
                self.failures = self.failures[-5:]
            return
        self.latest_failure_code = None
        if tool_call.name == 'read_file':
            required = {'path', 'start_line', 'end_line', 'total_lines'}
            if required.issubset(result.metadata):
                self._observe_read(result, revision)
        elif tool_call.name == 'list_directory':
            path = normalize_path(str(result.metadata.get('path', '.')))
            self.directories.add((revision, path))
        elif tool_call.name == 'grep':
            self._observe_grep(result, revision)
        elif tool_call.name == 'find_files':
            self._observe_find_files(result, revision)

    def advance_revision(
        self,
        revision: int,
        changed_paths: tuple[str, ...],
    ) -> None:
        '''Carry unchanged evidence forward and invalidate changed paths only.'''
        changed = {normalize_path(path) for path in changed_paths}
        latest_files: dict[str, FileEvidence] = {}
        for evidence in self.files.values():
            previous = latest_files.get(evidence.path)
            if previous is None or evidence.revision > previous.revision:
                latest_files[evidence.path] = evidence
        self.files = {}
        for path, evidence in latest_files.items():
            if path in changed:
                continue
            evidence.revision = revision
            self.files[(revision, path)] = evidence

        invalid_directories = {
            normalize_path(str(PurePosixPath(path).parent))
            for path in changed
        }
        self.directories = {
            (revision, path)
            for _, path in self.directories
            if path not in invalid_directories
        }
        self.search_hits = {
            (revision, path, line)
            for _, path, line in self.search_hits
            if path not in changed
        }
        self.discovered_files = {
            (revision, path)
            for _, path in self.discovered_files
            if path not in changed
        }
        self.latest_failure_code = None

    def invalidate_process_caches(self) -> None:
        '''Forget broad search indexes while retaining covered file ranges.'''
        self.directories.clear()
        self.search_hits.clear()
        self.discovered_files.clear()

    def system_suffix(self) -> str:
        if (
            not self.files
            and not self.directories
            and not self.search_hits
            and not self.discovered_files
            and not self.failures
        ):
            return ''
        lines = [
            '[Current Working Evidence]',
            'Use this evidence instead of re-reading covered content.',
        ]
        file_evidence = sorted(
            self.files.values(),
            key=lambda item: (item.path, item.revision),
        )
        visible_files = file_evidence[-self.MAX_VISIBLE_FILES:]
        omitted_files = len(file_evidence) - len(visible_files)
        if omitted_files:
            lines.append(f'- ... {omitted_files} older file entries omitted')
        for evidence in visible_files:
            ranges = ', '.join(
                f'{start}-{end}'
                for start, end in evidence.covered_ranges
            )
            lines.append(
                f'- {evidence.path} @ revision {evidence.revision}: '
                f'lines {ranges}; total {evidence.total_lines}'
            )
        directories = sorted(self.directories)
        visible_directories = directories[-self.MAX_VISIBLE_DIRECTORIES:]
        omitted_directories = len(directories) - len(visible_directories)
        if omitted_directories:
            lines.append(
                f'- ... {omitted_directories} older directory entries omitted'
            )
        for revision, path in visible_directories:
            lines.append(f'- listed {path} @ revision {revision}')
        search_hits = sorted(self.search_hits)
        visible_hits = search_hits[-self.MAX_VISIBLE_SEARCH_HITS:]
        omitted_hits = len(search_hits) - len(visible_hits)
        if omitted_hits:
            lines.append(
                f'- ... {omitted_hits} older search hits omitted'
            )
        for revision, path, line in visible_hits:
            lines.append(
                f'- search hit {path}:{line} @ revision {revision}'
            )
        discovered = sorted(self.discovered_files)
        visible_discovered = discovered[
            -self.MAX_VISIBLE_DISCOVERED_FILES:
        ]
        omitted_discovered = len(discovered) - len(visible_discovered)
        if omitted_discovered:
            lines.append(
                f'- ... {omitted_discovered} older discovered files omitted'
            )
        for revision, path in visible_discovered:
            lines.append(
                f'- discovered {path} @ revision {revision}'
            )
        if self.failures:
            lines.append('Recent tool failures:')
            lines.extend(f'- {failure}' for failure in self.failures)
        return '\n'.join(lines)

    @property
    def evidence_paths(self) -> tuple[str, ...]:
        paths = {evidence.path for evidence in self.files.values()}
        paths.update(path for _, path in self.directories if path != '.')
        paths.update(path for _, path, _ in self.search_hits)
        paths.update(path for _, path in self.discovered_files)
        return tuple(sorted(paths))

    @property
    def has_external_blocker(self) -> bool:
        return self.latest_failure_code in self.EXTERNAL_BLOCKER_CODES

    def _observe_read(
        self,
        result: ToolResult,
        revision: int,
    ) -> None:
        metadata = result.metadata
        path = normalize_path(str(metadata['path']))
        total_lines = int(metadata['total_lines'])
        start_line = int(metadata['start_line'])
        end_line = int(metadata['end_line'])
        key = (revision, path)
        evidence = self.files.get(key)
        if evidence is None:
            evidence = FileEvidence(
                path=path,
                revision=revision,
                total_lines=total_lines,
            )
            self.files[key] = evidence
        evidence.add(start_line, end_line)

    def _observe_grep(self, result: ToolResult, revision: int) -> None:
        for line in result.content.splitlines():
            parts = line.split(':', 2)
            if len(parts) < 3:
                continue
            path = normalize_path(parts[0])
            try:
                line_number = int(parts[1])
            except ValueError:
                continue
            evidence = self.files.get((revision, path))
            if evidence is not None and evidence.covers(
                line_number,
                line_number,
            ):
                continue
            self.search_hits.add((revision, path, line_number))

    def _observe_find_files(self, result: ToolResult, revision: int) -> None:
        for line in result.content.splitlines():
            path = normalize_path(line.strip())
            if not path or path == '.':
                continue
            discovery = (revision, path)
            if discovery in self.files:
                continue
            self.discovered_files.add(discovery)


def normalize_path(path: str) -> str:
    return PurePosixPath(path.replace('\\', '/')).as_posix()
