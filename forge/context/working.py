'''Structured, task-local evidence kept outside raw conversation history.'''

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from forge.runtime.state import ToolCall
from forge.tools.base import ToolResult


@dataclass(slots=True)
class FileSegment:
    start_line: int
    end_line: int
    content: str

    def covers(self, start_line: int, end_line: int) -> bool:
        return self.start_line <= start_line and self.end_line >= end_line

    def slice(self, start_line: int, end_line: int) -> str:
        lines = self.content.splitlines()
        start = max(0, start_line - self.start_line)
        stop = start + max(0, end_line - start_line + 1)
        return '\n'.join(lines[start:stop])


@dataclass(slots=True)
class FileEvidence:
    path: str
    revision: int
    total_lines: int
    covered_ranges: list[tuple[int, int]] = field(default_factory=list)
    segments: list[FileSegment] = field(default_factory=list)

    def covers(self, start_line: int, end_line: int) -> bool:
        return any(
            start <= start_line and end >= end_line
            for start, end in self.covered_ranges
        )

    def add(self, start_line: int, end_line: int) -> bool:
        if end_line < start_line or self.covers(start_line, end_line):
            return False
        ranges = sorted([*self.covered_ranges, (start_line, end_line)])
        merged: list[tuple[int, int]] = []
        for start, end in ranges:
            if not merged or start > merged[-1][1] + 1:
                merged.append((start, end))
                continue
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        self.covered_ranges = merged
        return True

    def remember(
        self,
        start_line: int,
        end_line: int,
        content: str,
    ) -> None:
        if any(
            segment.start_line == start_line
            and segment.end_line == end_line
            for segment in self.segments
        ):
            return
        self.segments.append(FileSegment(start_line, end_line, content))

    def replay(self, start_line: int, end_line: int) -> str | None:
        segment = next(
            (
                item
                for item in reversed(self.segments)
                if item.covers(start_line, end_line)
            ),
            None,
        )
        if segment is not None:
            return segment.slice(start_line, end_line)
        if not self.covers(start_line, end_line):
            return None

        lines_by_number: dict[int, str] = {}
        for item in self.segments:
            for offset, line in enumerate(item.content.splitlines()):
                line_number = item.start_line + offset
                if line_number > item.end_line:
                    break
                lines_by_number[line_number] = line
        if any(
            line_number not in lines_by_number
            for line_number in range(start_line, end_line + 1)
        ):
            return None
        return '\n'.join(
            lines_by_number[line_number]
            for line_number in range(start_line, end_line + 1)
        )


class WorkingState:
    '''Track novel evidence and suppress semantically redundant exploration.'''

    MAX_VISIBLE_FILES = 50
    MAX_VISIBLE_DIRECTORIES = 20
    MAX_VISIBLE_SEARCH_HITS = 50
    MAX_VISIBLE_DISCOVERED_FILES = 50
    MAX_REPLAY_LINES = 120
    MAX_REPLAY_CHARACTERS = 12_000
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
        self.successful_signatures: set[str] = set()
        self.cached_results: dict[str, ToolResult] = {}
        self.failures: list[str] = []
        self.failure_codes: set[str] = set()
        self.latest_failure_code: str | None = None

    def preflight(
        self,
        tool_call: ToolCall,
        revision: int,
        signature: str | None = None,
    ) -> ToolResult | None:
        if tool_call.name == 'read_file':
            replay = self._replay_read(tool_call, revision)
            if replay is not None:
                return replay
        if signature is not None and signature in self.cached_results:
            cached = self.cached_results[signature]
            return ToolResult.ok(
                f'Cache hit: {cached.summary}',
                content=(
                    '[Identical cached tool result omitted. Reuse the existing '
                    'working evidence or change the query to request a '
                    'different fact.]'
                ),
                metadata={
                    **cached.metadata,
                    'cache_hit': True,
                    'cache_content_omitted': True,
                },
            )
        return None

    def observe(
        self,
        tool_call: ToolCall,
        result: ToolResult,
        revision: int,
        signature: str,
    ) -> bool:
        if not result.success:
            if result.error is not None:
                self.latest_failure_code = result.error.code
                self.failure_codes.add(result.error.code)
                self.failures.append(
                    f'{tool_call.name}: {result.error.code}'
                )
                self.failures = self.failures[-5:]
            return False
        self.latest_failure_code = None
        if (
            tool_call.name in {
                'read_file',
                'list_directory',
                'grep',
                'find_files',
                'git_status',
                'git_diff',
                'task_get',
            }
            and not result.metadata.get('cache_hit')
        ):
            self.cached_results[signature] = result
        if tool_call.name == 'read_file':
            required = {'path', 'start_line', 'end_line', 'total_lines'}
            if required.issubset(result.metadata):
                return self._observe_read(result, revision)
            return self._observe_signature(signature)
        if tool_call.name == 'list_directory':
            path = normalize_path(
                str(result.metadata.get('path', '.'))
            )
            key = (revision, path)
            if key in self.directories:
                return False
            self.directories.add(key)
            return True
        if tool_call.name == 'grep':
            return self._observe_grep(result, revision)
        if tool_call.name == 'find_files':
            return self._observe_find_files(result, revision)
        if tool_call.name in {'git_status', 'git_diff'}:
            if not result.content.strip():
                return False
            return self._observe_signature(signature)
        if tool_call.name == 'task_get':
            return self._observe_signature(signature)
        return False

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
        # Signatures include the workspace revision. Dropping this small cache
        # is safer than replaying stale content after any repository change.
        self.cached_results.clear()
        self.latest_failure_code = None

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

    def answer_mentions_evidence(self, text: str) -> bool:
        normalized = text.casefold()
        return any(
            candidate and candidate in normalized
            for path in self.evidence_paths
            for candidate in evidence_names(path)
        )

    def _observe_read(
        self,
        result: ToolResult,
        revision: int,
    ) -> bool:
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
        progressed = evidence.add(start_line, end_line)
        evidence.remember(start_line, end_line, result.content)
        return progressed

    def _replay_read(
        self,
        tool_call: ToolCall,
        revision: int,
    ) -> ToolResult | None:
        arguments = tool_call.arguments
        path = normalize_path(str(arguments.get('path', '')))
        evidence = self.files.get((revision, path))
        if evidence is None:
            return None
        try:
            start_line = int(arguments.get('start_line', 1))
            requested_end = arguments.get('end_line')
            end_line = (
                evidence.total_lines
                if requested_end is None
                else min(int(requested_end), evidence.total_lines)
            )
        except (TypeError, ValueError):
            return None
        if start_line < 1 or end_line < start_line:
            return None
        cached_content = evidence.replay(start_line, end_line)
        if cached_content is None:
            return None
        metadata = {
            'path': path,
            'start_line': start_line,
            'end_line': end_line,
            'total_lines': evidence.total_lines,
            'cache_hit': True,
            'evidence_replayed': True,
        }
        line_count = end_line - start_line + 1
        if (
            line_count > self.MAX_REPLAY_LINES
            or len(cached_content) > self.MAX_REPLAY_CHARACTERS
        ):
            metadata['cache_content_omitted'] = True
            return ToolResult.ok(
                f'Skipped covered read for {path} lines '
                f'{start_line}-{end_line}.',
                content=(
                    '[Cached source omitted to avoid reinjecting a large '
                    'covered range. Request at most 120 exact lines around '
                    'the required symbol; use grep first if its location is '
                    'unknown.]'
                ),
                metadata=metadata,
            )
        return ToolResult.ok(
            f'Replayed cached read for {path} lines '
            f'{start_line}-{end_line}.',
            content=cached_content,
            metadata=metadata,
        )

    def _observe_signature(self, signature: str) -> bool:
        if signature in self.successful_signatures:
            return False
        self.successful_signatures.add(signature)
        return True

    def _observe_grep(self, result: ToolResult, revision: int) -> bool:
        progressed = False
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
            hit = (revision, path, line_number)
            if hit not in self.search_hits:
                self.search_hits.add(hit)
                progressed = True
        return progressed

    def _observe_find_files(self, result: ToolResult, revision: int) -> bool:
        progressed = False
        for line in result.content.splitlines():
            path = normalize_path(line.strip())
            if not path or path == '.':
                continue
            discovery = (revision, path)
            if discovery in self.files:
                continue
            if discovery not in self.discovered_files:
                self.discovered_files.add(discovery)
                progressed = True
        return progressed


def normalize_path(path: str) -> str:
    return PurePosixPath(path.replace('\\', '/')).as_posix()


def evidence_names(path: str) -> tuple[str, ...]:
    item = PurePosixPath(path)
    return tuple(
        dict.fromkeys(
            value.casefold()
            for value in (path, item.name, item.stem)
            if value and value != '.'
        )
    )
