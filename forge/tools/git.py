'''Read-only Git repository tools.'''

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
from typing import Any

from pydantic import Field

from forge.tools.base import (
    Tool,
    ToolInput,
    ToolResult,
    display_path,
    resolve_repository_path,
)
from forge.tools.shell import (
    process_metadata,
    render_process_output,
    run_process,
)


MAX_UNSCOPED_DIFF_CHARACTERS = 30_000
MAX_SCOPED_DIFF_CHARACTERS = 30_000


class GitStatusInput(ToolInput):
    pass


class GitStatusTool(Tool[GitStatusInput]):
    name = 'git_status'
    description = (
        'Show the repository branch and concise working tree status. Use it '
        'when changed-path state is needed, not as a substitute for reading '
        'source files.'
    )
    input_model = GitStatusInput

    async def execute(self, arguments: GitStatusInput) -> ToolResult:
        result = await run_process(
            ['git', 'status', '--short', '--branch'],
            cwd=self.root,
            timeout_seconds=30,
        )
        metadata = process_metadata(result)
        if result.exit_code != 0:
            return ToolResult.fail(
                'git_status_failed',
                f'git status exited with code {result.exit_code}.',
                content=render_process_output(result),
                metadata=metadata,
            )
        content = result.stdout.rstrip() or 'Working tree clean.'
        return ToolResult.ok(
            'Read Git working tree status.',
            content=content,
            metadata=metadata,
        )


class GitLogInput(ToolInput):
    max_count: int = Field(default=10, ge=1, le=50)
    path: str | None = Field(default=None, min_length=1)


class GitLogTool(Tool[GitLogInput]):
    name = 'git_log'
    description = (
        'Show a concise recent commit history, optionally limited to one '
        'repository file. Use this only when history is relevant to the '
        'investigation.'
    )
    input_model = GitLogInput

    async def execute(self, arguments: GitLogInput) -> ToolResult:
        command = [
            'git',
            'log',
            f'--max-count={arguments.max_count}',
            '--date=short',
            '--pretty=format:%h%x09%ad%x09%s',
        ]
        shown_path: str | None = None
        if arguments.path is not None:
            resolved = resolve_repository_path(
                self.root,
                arguments.path,
                must_exist=False,
            )
            shown_path = display_path(self.root, resolved)
            command.extend(['--', shown_path])
        result = await run_process(
            command,
            cwd=self.root,
            timeout_seconds=30,
        )
        metadata = {
            **process_metadata(result),
            'path': shown_path,
            'max_count': arguments.max_count,
        }
        if result.exit_code != 0:
            return ToolResult.fail(
                'git_log_failed',
                f'git log exited with code {result.exit_code}.',
                content=render_process_output(result),
                metadata=metadata,
            )
        return ToolResult.ok(
            'Read recent Git history.',
            content=result.stdout.rstrip() or 'No matching commits.',
            metadata=metadata,
        )


class GitDiffInput(ToolInput):
    staged: bool = False
    path: str | None = Field(default=None, min_length=1)
    offset: int = Field(default=0, ge=0)
    expected_sha256: str | None = Field(
        default=None,
        pattern=r'^[0-9a-fA-F]{64}$',
    )


class GitDiffTool(Tool[GitDiffInput]):
    name = 'git_diff'
    description = (
        'Show unstaged or staged Git changes, optionally limited to one '
        'repository file. Directory paths are rejected; select a concrete '
        'file from git_status or repository evidence. An unscoped response '
        'larger than 30000 characters is rejected with diff_too_large; retry '
        'with path set to one relevant file. A large '
        'single-file Diff is returned in ordered pages; continue with the '
        'returned next_offset and diff_sha256 as expected_sha256. A path-limited '
        'request also renders an untracked UTF-8 file as a reviewable '
        'new-file Diff. Prefer a path-limited Diff in a dirty '
        'repository. Use it to review actual changes, not to rediscover '
        'unchanged source.'
    )
    input_model = GitDiffInput

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._continuations: set[tuple[bool, str, str, int]] = set()

    async def execute(self, arguments: GitDiffInput) -> ToolResult:
        if arguments.path is None and (
            arguments.offset != 0 or arguments.expected_sha256 is not None
        ):
            return ToolResult.fail(
                'git_diff_page_requires_path',
                'Paged git_diff requests require path to identify one file. '
                'Set path, or omit offset and expected_sha256.',
            )
        command = ['git', 'diff', '--no-ext-diff']
        if arguments.staged:
            command.append('--cached')
        resolved_path: Path | None = None
        shown_path: str | None = None
        if arguments.path is not None:
            resolved_path = resolve_repository_path(
                self.root,
                arguments.path,
                must_exist=False,
            )
            shown_path = display_path(self.root, resolved_path)
            if resolved_path.exists() and resolved_path.is_dir():
                return ToolResult.fail(
                    'git_diff_path_is_directory',
                    'git_diff path must identify one file, not a directory. '
                    'Choose a concrete changed file and retry.',
                    details={
                        'path': shown_path,
                        'required_path_type': 'file',
                    },
                    metadata={
                        'staged': arguments.staged,
                        'path': shown_path,
                    },
                )
            command.extend(['--', shown_path])

        result = await run_process(
            command,
            cwd=self.root,
            timeout_seconds=30,
        )
        metadata = {
            **compact_process_metadata(result),
            'staged': arguments.staged,
            'path': shown_path,
        }
        if result.exit_code != 0:
            return ToolResult.fail(
                'git_diff_failed',
                f'git diff exited with code {result.exit_code}.',
                content=render_process_output(result),
                metadata=metadata,
            )

        raw_content = result.stdout
        if (
            shown_path is None
            and len(raw_content) > MAX_UNSCOPED_DIFF_CHARACTERS
        ):
            message = (
                f'Unscoped Git diff contains {len(raw_content)} characters, '
                f'exceeding the {MAX_UNSCOPED_DIFF_CHARACTERS}-character '
                'limit. Call git_diff again with path set to one relevant '
                'repository file.'
            )
            return ToolResult.fail(
                'diff_too_large',
                message,
                content=message,
                details={
                    'characters': len(raw_content),
                    'maximum_characters': MAX_UNSCOPED_DIFF_CHARACTERS,
                    'required_argument': 'path',
                },
                metadata={
                    **metadata,
                    'diff_characters': len(raw_content),
                    'maximum_characters': MAX_UNSCOPED_DIFF_CHARACTERS,
                },
            )

        content = raw_content.rstrip()
        summary = 'Read Git diff.' if content else 'No matching Git diff.'
        if (
            not content
            and shown_path is not None
            and resolved_path is not None
            and not arguments.staged
        ):
            untracked = await self._untracked_file_diff(
                resolved_path,
                shown_path,
            )
            if isinstance(untracked, ToolResult):
                return untracked
            if untracked is not None:
                untracked_content, untracked_metadata = untracked
                content = untracked_content
                metadata = {
                    **metadata,
                    **untracked_metadata,
                }
                summary = 'Read untracked file as a new-file Git diff.'

        if shown_path is not None and (
            len(content) > MAX_SCOPED_DIFF_CHARACTERS
            or arguments.offset != 0
        ):
            return self._paged_diff(
                arguments,
                shown_path=shown_path,
                content=content,
                metadata=metadata,
            )

        return ToolResult.ok(
            summary,
            content=content,
            metadata=metadata,
        )

    def _paged_diff(
        self,
        arguments: GitDiffInput,
        *,
        shown_path: str,
        content: str,
        metadata: dict[str, Any],
    ) -> ToolResult:
        '''Return one bounded, sequential page of a single-file Diff.'''
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        if arguments.offset == 0:
            self._continuations = {
                item
                for item in self._continuations
                if item[:2] != (arguments.staged, shown_path)
            }
        continuation = (
            arguments.staged,
            shown_path,
            digest,
            arguments.offset,
        )
        if arguments.offset:
            if arguments.expected_sha256 != digest:
                return ToolResult.fail(
                    'git_diff_changed',
                    f'Git diff for {shown_path} changed between pages. '
                    'Restart from offset 0.',
                    details={
                        'path': shown_path,
                        'restart_offset': 0,
                        'actual_sha256': digest,
                    },
                    metadata={
                        **metadata,
                        'diff_sha256': digest,
                        'diff_characters': len(content),
                    },
                )
            if continuation not in self._continuations:
                return ToolResult.fail(
                    'git_diff_page_out_of_order',
                    'The requested Git diff page was not the next page issued '
                    f'for {shown_path}. Restart from offset 0.',
                    details={
                        'path': shown_path,
                        'requested_offset': arguments.offset,
                        'restart_offset': 0,
                    },
                    metadata={
                        **metadata,
                        'diff_sha256': digest,
                        'diff_characters': len(content),
                    },
                )
            self._continuations.discard(continuation)

        start = arguments.offset
        if start >= len(content):
            return ToolResult.fail(
                'git_diff_offset_out_of_range',
                f'Git diff offset {start} is outside the '
                f'{len(content)}-character Diff for {shown_path}. '
                'Restart from offset 0.',
                details={
                    'path': shown_path,
                    'requested_offset': start,
                    'diff_characters': len(content),
                    'restart_offset': 0,
                },
                metadata={
                    **metadata,
                    'diff_sha256': digest,
                    'diff_characters': len(content),
                },
            )

        end = min(start + MAX_SCOPED_DIFF_CHARACTERS, len(content))
        if end < len(content):
            line_end = content.rfind('\n', start, end)
            if line_end >= start:
                end = line_end + 1
        page = content[start:end]
        complete = end >= len(content)
        next_offset = None if complete else end
        if next_offset is not None:
            self._continuations.add(
                (
                    arguments.staged,
                    shown_path,
                    digest,
                    next_offset,
                )
            )
        return ToolResult.ok(
            (
                f'Read final Git diff page for {shown_path}.'
                if complete
                else f'Read partial Git diff page for {shown_path}.'
            ),
            content=page,
            metadata={
                **metadata,
                'paged_diff': True,
                'diff_complete': complete,
                'diff_sha256': digest,
                'diff_characters': len(content),
                'page_start': start,
                'page_end': end,
                'next_offset': next_offset,
                'maximum_characters': MAX_SCOPED_DIFF_CHARACTERS,
            },
        )

    async def _untracked_file_diff(
        self,
        path: Path,
        shown_path: str,
    ) -> tuple[str, dict[str, Any]] | ToolResult | None:
        if not path.is_file():
            return None
        listed = await run_process(
            [
                'git',
                'ls-files',
                '--others',
                '--exclude-standard',
                '-z',
                '--',
                shown_path,
            ],
            cwd=self.root,
            timeout_seconds=30,
        )
        if listed.exit_code != 0:
            return ToolResult.fail(
                'git_diff_failed',
                f'git ls-files exited with code {listed.exit_code}.',
                content=render_process_output(listed),
                metadata={
                    **compact_process_metadata(listed),
                    'staged': False,
                    'path': shown_path,
                },
            )
        untracked_paths = {
            item.replace('\\', '/')
            for item in listed.stdout.split('\0')
            if item
        }
        if shown_path not in untracked_paths:
            return None
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            return ToolResult.fail(
                'untracked_diff_not_text',
                f'Cannot render {shown_path} as a UTF-8 text Diff.',
                details={'path': shown_path},
                metadata={
                    'staged': False,
                    'path': shown_path,
                    'untracked': True,
                },
            )
        content = render_untracked_diff(shown_path, text)
        return content, {
            'untracked': True,
            'synthetic_diff': True,
            'diff_characters': len(content),
            'file_characters': len(text),
        }


def compact_process_metadata(result: Any) -> dict[str, Any]:
    '''Keep process facts without duplicating a potentially huge Diff.'''
    metadata = process_metadata(result)
    stdout = str(metadata.pop('stdout', ''))
    stderr = str(metadata.pop('stderr', ''))
    metadata['stdout_characters'] = len(stdout)
    metadata['stderr_characters'] = len(stderr)
    return metadata


def render_untracked_diff(path: str, text: str) -> str:
    '''Render one UTF-8 untracked file as a conventional new-file Diff.'''
    source_lines = text.splitlines(keepends=True)
    normalized_lines = [
        line if line.endswith(('\n', '\r')) else line + '\n'
        for line in source_lines
    ]
    body = ''.join(
        difflib.unified_diff(
            [],
            normalized_lines,
            fromfile='/dev/null',
            tofile=f'b/{path}',
        )
    )
    prefix = (
        f'diff --git a/{path} b/{path}\n'
        'new file mode 100644\n'
    )
    if not body:
        body = f'--- /dev/null\n+++ b/{path}\n'
    if text and not text.endswith(('\n', '\r')):
        body += '\\ No newline at end of file\n'
    return prefix + body
