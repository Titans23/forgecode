'''Command verification that produces completion evidence.'''

from __future__ import annotations

import os
from pathlib import Path
import re

from pydantic import Field

from forge.runtime.workspace import WorkspaceTracker
from forge.tools.base import (
    Tool,
    ToolExecutionError,
    ToolInput,
    ToolResult,
    display_path,
    resolve_repository_path,
)
from forge.tools.shell import (
    process_metadata,
    render_process_output,
    run_process,
    shell_directory_write_reason,
    shell_file_read_reason,
    shell_file_write_reason,
    has_unquoted_heredoc,
)


class VerifyInput(ToolInput):
    command: str = Field(min_length=1)
    cwd: str = Field(
        default='.',
        description=(
            "Repository-relative directory; omit to verify the repository root. "
            "Absolute paths and '..' are forbidden."
        ),
    )
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)


class VerifyTool(Tool[VerifyInput]):
    name = 'verify'
    description = (
        'Run a test, build, lint, or type-check command as formal completion '
        'evidence after workspace changes. Choose the most relevant project '
        'command; use git diff --check only when no more specific validation '
        'exists. A successful result applies only to the exact current '
        'workspace revision, so verify again after later edits. Do not use '
        'verify for repository inspection. On Windows, POSIX heredocs and '
        'commands such as ls are invalid; use dedicated repository tools for '
        'inspection and a native test/build/check command for verification.'
    )
    input_model = VerifyInput
    effect = 'process'

    def __init__(self, root: Path, tracker: WorkspaceTracker) -> None:
        super().__init__(root)
        self.tracker = tracker

    async def execute(self, arguments: VerifyInput) -> ToolResult:
        cwd = resolve_repository_path(self.root, arguments.cwd)
        if os.name == 'nt' and has_unquoted_heredoc(arguments.command):
            return ToolResult.fail(
                'unsupported_shell_syntax',
                'Windows cmd.exe does not support POSIX << heredocs. Use a '
                'dedicated repository tool for inspection or a single-line '
                'native verification command such as node --check <path>.',
                metadata={
                    'command': arguments.command,
                    'cwd': arguments.cwd,
                    'workspace_revision': self.tracker.revision,
                    'verification': True,
                },
            )
        if not cwd.is_dir():
            raise ToolExecutionError(
                'not_a_directory',
                f'Verification cwd is not a directory: {arguments.cwd}',
            )
        revision = self.tracker.revision
        disallowed_reason = verification_command_disallowed_reason(
            arguments.command
        )
        if disallowed_reason is not None:
            return ToolResult.fail(
                'verification_command_not_allowed',
                'Verification commands must run tests, builds, lint, type '
                'checks, syntax checks, or Git diff validation; they cannot '
                'read or modify repository files.',
                content=(
                    f'Detected {disallowed_reason}. Use the dedicated '
                    'repository tools for file access.'
                ),
                metadata={
                    'command': arguments.command,
                    'cwd': display_path(self.root, cwd),
                    'workspace_revision': revision,
                    'verification': True,
                },
            )
        missing_manifest = missing_verification_manifest(
            arguments.command,
            cwd,
        )
        if missing_manifest is not None:
            return ToolResult.fail(
                'verification_command_not_applicable',
                f'Cannot run {arguments.command!r}: {missing_manifest.name} '
                f'does not exist in {display_path(self.root, cwd)}.',
                content=(
                    'Choose a verification command supported by the current '
                    'project. For a standalone JavaScript file, prefer '
                    '`node --check <path>`.'
                ),
                metadata={
                    'command': arguments.command,
                    'cwd': display_path(self.root, cwd),
                    'workspace_revision': revision,
                    'verification': True,
                    'required_manifest': missing_manifest.name,
                },
            )
        result = await run_process(
            arguments.command,
            cwd=cwd,
            timeout_seconds=arguments.timeout_seconds,
            shell=True,
        )
        metadata = {
            **process_metadata(result),
            'command': arguments.command,
            'cwd': display_path(self.root, cwd),
            'workspace_revision': revision,
            'verification': True,
        }
        content = render_process_output(result)
        if result.timed_out:
            return ToolResult.fail(
                'verification_timeout',
                f'Verification timed out after '
                f'{arguments.timeout_seconds:g}s.',
                content=content,
                metadata=metadata,
            )
        if result.exit_code != 0:
            return ToolResult.fail(
                'verification_failed',
                f'Verification exited with code {result.exit_code}.',
                content=content,
                metadata=metadata,
            )
        return ToolResult.ok(
            f'Verification passed in {result.duration_seconds:.3f}s.',
            content=content,
            metadata=metadata,
        )



def verification_command_disallowed_reason(command: str) -> str | None:
    '''Prevent verify from bypassing tracked file and directory tools.'''
    return (
        shell_file_read_reason(command)
        or shell_file_write_reason(command)
        or shell_directory_write_reason(command)
    )


def missing_verification_manifest(command: str, cwd: Path) -> Path | None:
    '''Return a required project manifest missing for a known test command.'''
    normalized = ' '.join(command.casefold().split())
    if re.match(r'^(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?:\s|$)', normalized):
        manifest = cwd / 'package.json'
        if not manifest.is_file():
            return manifest
    return None
