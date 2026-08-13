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
        'workspace revision, so verify again after later edits. Runtime version '
        'queries, read-only Git inspection, and native directory listings are '
        'tolerated as inspection-only commands, but never count as verification '
        'evidence. Other repository inspection remains rejected. On Windows, '
        'POSIX heredocs and commands '
        'such as ls are invalid; use dedicated repository tools for inspection '
        'and a native test/build/check command for verification.'
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
        inspection_reason = non_verification_command_reason(
            arguments.command
        )
        if inspection_reason in {
            'a runtime or tool version query',
            'a read-only Git inspection command',
            'a shell directory inspection command',
            'an executable lookup command',
        }:
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
                'verification': False,
                'status': 'inspection_only',
                'inspection_reason': inspection_reason,
            }
            content = render_process_output(result)
            if result.timed_out:
                return ToolResult.fail(
                    'inspection_timeout',
                    f'Inspection command timed out after '
                    f'{arguments.timeout_seconds:g}s.',
                    content=content,
                    metadata=metadata,
                )
            if result.exit_code != 0:
                return ToolResult.fail(
                    'inspection_failed',
                    f'Inspection command exited with code {result.exit_code}.',
                    content=content,
                    metadata=metadata,
                )
            return ToolResult.ok(
                'Inspection command completed successfully; this result does '
                'not count as verification evidence.',
                content=content,
                metadata=metadata,
            )
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
        effective_command = arguments.command
        result = await run_process(
            effective_command,
            cwd=cwd,
            timeout_seconds=arguments.timeout_seconds,
            shell=True,
        )
        fallback_command = verification_command_fallback(
            effective_command,
            render_process_output(result),
        )
        if fallback_command is not None and result.exit_code != 0:
            fallback_result = await run_process(
                fallback_command,
                cwd=cwd,
                timeout_seconds=arguments.timeout_seconds,
                shell=True,
            )
            if fallback_result.exit_code == 0 and not fallback_result.timed_out:
                effective_command = fallback_command
                result = fallback_result
        metadata = {
            **process_metadata(result),
            'command': effective_command,
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
            unavailable_reason = runtime_verification_not_applicable_reason(
                arguments.command,
                content,
            )
            if unavailable_reason is not None:
                return ToolResult.fail(
                    'verification_command_not_applicable',
                    unavailable_reason,
                    content=content,
                    metadata={
                        **metadata,
                        'verification': False,
                        'status': 'not_applicable',
                    },
                )
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


def verification_command_fallback(
    command: str,
    output: str,
) -> str | None:
    '''Return a safe equivalent when a common executable name is unavailable.'''
    normalized = output.casefold()
    unavailable = (
        'not found' in normalized
        or 'command not found' in normalized
        or 'is not recognized as an internal or external command' in normalized
    )
    if not unavailable:
        return None
    stripped = command.strip()
    if re.match(r'^pytest(?:\.exe)?(?:\s|$)', stripped, re.IGNORECASE):
        return re.sub(r'^pytest(?:\.exe)?', 'python3 -m pytest', stripped, count=1, flags=re.IGNORECASE)
    if re.match(r'^python(?:\.exe)?(?:\s|$)', stripped, re.IGNORECASE):
        return re.sub(r'^python(?:\.exe)?', 'python3', stripped, count=1, flags=re.IGNORECASE)
    return None


def runtime_verification_not_applicable_reason(
    command: str,
    output: str,
) -> str | None:
    '''Recognize configured checks that cannot run in the supplied checkout.'''
    normalized_command = command.casefold()
    normalized_output = output.casefold()
    if (
        ('eslint' in normalized_command or 'lint' in normalized_command)
        and any(
            marker in normalized_output
            for marker in (
                "couldn't find an eslint.config",
                "couldn't find a configuration file",
                'no eslint configuration found',
            )
        )
    ):
        return 'The configured lint command has no lint configuration.'
    if (
        ('jest' in normalized_command or 'npm test' in normalized_command)
        and 'no tests found' in normalized_output
    ):
        return 'The configured test command found no tests in this checkout.'
    if (
        'cmake' in normalized_command
        and 'cannot find source file:' in normalized_output
        and 'no sources given to target' in normalized_output
    ):
        return (
            'CMake references a source or test file that is absent from the '
            'supplied checkout.'
        )
    return None



def verification_command_disallowed_reason(command: str) -> str | None:
    '''Prevent verify from bypassing tools or recording pure inspection as proof.'''
    unsafe_reason = (
        shell_file_read_reason(command)
        or shell_file_write_reason(command)
        or shell_directory_write_reason(command)
    )
    if unsafe_reason is not None:
        return unsafe_reason
    return non_verification_command_reason(command)


PURE_INSPECTION_PATTERNS = (
    (
        re.compile(
            r'(?:^|[;&|]\s*)(?:ls|dir|tree|pwd|cd)(?:\s|$)',
            re.IGNORECASE,
        ),
        'a shell directory inspection command',
    ),
    (
        re.compile(
            r'(?:^|[;&|]\s*)(?:where|which)(?:\.exe)?(?:\s|$)',
            re.IGNORECASE,
        ),
        'an executable lookup command',
    ),
    (
        re.compile(
            r'(?:^|[;&|]\s*)find(?:\.exe)?(?:\s|$)',
            re.IGNORECASE,
        ),
        'a shell file or text inspection command',
    ),
    (
        re.compile(
            r'^\s*(?:node|python(?:\d+(?:\.\d+)*)?|npm|pnpm|yarn|bun|'
            r'git|java|javac|go|cargo|rustc|dotnet)(?:\.exe|\.cmd)?\s+'
            r'(?:-v|--version|version)\s*$',
            re.IGNORECASE,
        ),
        'a runtime or tool version query',
    ),
    (
        re.compile(
            r'^\s*git(?:\.exe)?\s+'
            r'(?:status|log|show|branch|rev-parse|diff(?![^\r\n]*--check))\b',
            re.IGNORECASE,
        ),
        'a read-only Git inspection command',
    ),
)


def non_verification_command_reason(command: str) -> str | None:
    '''Return a reason for commands that can never validate repository behavior.'''
    normalized = ' '.join(command.split())
    for pattern, reason in PURE_INSPECTION_PATTERNS:
        if pattern.search(normalized):
            return reason
    return None


def missing_verification_manifest(command: str, cwd: Path) -> Path | None:
    '''Return a required project manifest missing for a known test command.'''
    normalized = ' '.join(command.casefold().split())
    if re.match(r'^(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?:\s|$)', normalized):
        manifest = cwd / 'package.json'
        if not manifest.is_file():
            return manifest
    return None
