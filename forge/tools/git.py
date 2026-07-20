'''Read-only Git status and diff tools.'''

from __future__ import annotations

import difflib
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


class GitDiffInput(ToolInput):
    staged: bool = False
    path: str | None = Field(default=None, min_length=1)


class GitDiffTool(Tool[GitDiffInput]):
    name = 'git_diff'
    description = (
        'Show unstaged or staged Git changes, optionally limited to one '
        'repository file. Directory paths are rejected; select a concrete '
        'file from git_status or repository evidence. Any response larger '
        'than 30000 characters is rejected with diff_too_large. For an '
        'unscoped result, retry with path set to the relevant file; for a '
        'large single file, use focused read_file ranges. A path-limited '
        'request also renders an untracked UTF-8 file as a reviewable '
        'new-file Diff. Prefer a path-limited Diff in a dirty '
        'repository. Use it to review actual changes, not to rediscover '
        'unchanged source.'
    )
    input_model = GitDiffInput

    async def execute(self, arguments: GitDiffInput) -> ToolResult:
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
        if (
            shown_path is not None
            and len(raw_content) > MAX_SCOPED_DIFF_CHARACTERS
        ):
            message = (
                f'Git diff for {shown_path} contains '
                f'{len(raw_content)} characters, exceeding the '
                f'{MAX_SCOPED_DIFF_CHARACTERS}-character per-file limit. '
                'Use read_file with focused line ranges and review only the '
                'relevant code.'
            )
            return ToolResult.fail(
                'diff_too_large',
                message,
                content=message,
                details={
                    'path': shown_path,
                    'characters': len(raw_content),
                    'maximum_characters': MAX_SCOPED_DIFF_CHARACTERS,
                    'recommended_tool': 'read_file',
                },
                metadata={
                    **metadata,
                    'diff_characters': len(raw_content),
                    'maximum_characters': MAX_SCOPED_DIFF_CHARACTERS,
                },
            )

        content = raw_content.rstrip()
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
                return ToolResult.ok(
                    'Read untracked file as a new-file Git diff.',
                    content=untracked_content,
                    metadata={
                        **metadata,
                        **untracked_metadata,
                    },
                )

        return ToolResult.ok(
            'Read Git diff.' if content else 'No matching Git diff.',
            content=content,
            metadata=metadata,
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
        if len(content) > MAX_SCOPED_DIFF_CHARACTERS:
            message = (
                f'Git diff for {shown_path} contains {len(content)} '
                f'characters, exceeding the '
                f'{MAX_SCOPED_DIFF_CHARACTERS}-character per-file limit. '
                'Use read_file with focused line ranges.'
            )
            return ToolResult.fail(
                'diff_too_large',
                message,
                content=message,
                details={
                    'path': shown_path,
                    'characters': len(content),
                    'maximum_characters': MAX_SCOPED_DIFF_CHARACTERS,
                    'recommended_tool': 'read_file',
                },
                metadata={
                    'staged': False,
                    'path': shown_path,
                    'untracked': True,
                    'diff_characters': len(content),
                    'maximum_characters': MAX_SCOPED_DIFF_CHARACTERS,
                },
            )
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
