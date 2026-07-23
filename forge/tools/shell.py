'''Local command execution tool and shared subprocess helpers.'''

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import re
from time import perf_counter

from pydantic import Field

from forge.tools.base import (
    Tool,
    ToolExecutionError,
    ToolInput,
    ToolResult,
    display_path,
    resolve_repository_path,
)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False


async def run_process(
    command: list[str] | str,
    *,
    cwd: Path,
    timeout_seconds: float,
    input_text: str | None = None,
    shell: bool = False,
) -> ProcessResult:
    '''Run one subprocess and capture deterministic result fields.'''
    started = perf_counter()
    stdin = asyncio.subprocess.PIPE if input_text is not None else None
    if shell:
        if not isinstance(command, str):
            raise TypeError('Shell commands must be strings.')
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    else:
        if isinstance(command, str):
            raise TypeError('Executable commands must be argument lists.')
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(
                input_text.encode('utf-8') if input_text is not None else None
            ),
            timeout=timeout_seconds,
        )
        timed_out = False
    except TimeoutError:
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
        timed_out = True

    return ProcessResult(
        exit_code=process.returncode if process.returncode is not None else -1,
        stdout=stdout_bytes.decode('utf-8', errors='replace'),
        stderr=stderr_bytes.decode('utf-8', errors='replace'),
        duration_seconds=perf_counter() - started,
        timed_out=timed_out,
    )


def process_metadata(result: ProcessResult) -> dict[str, object]:
    return {
        'exit_code': result.exit_code,
        'stdout': result.stdout,
        'stderr': result.stderr,
        'duration_seconds': result.duration_seconds,
        'timed_out': result.timed_out,
    }


def render_process_output(result: ProcessResult) -> str:
    sections: list[str] = []
    if result.stdout:
        sections.append(f'stdout:\n{result.stdout.rstrip()}')
    if result.stderr:
        sections.append(f'stderr:\n{result.stderr.rstrip()}')
    return '\n\n'.join(sections)


class RunCommandInput(ToolInput):
    command: str = Field(min_length=1)
    cwd: str = '.'
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    stdin: str | None = Field(default=None, max_length=8_000)


class RunCommandTool(Tool[RunCommandInput]):
    name = 'run_command'
    description = (
        'Run an executable repository command for exploration, diagnostics, '
        'or development. Do not use it to display source files or directory '
        'trees; use read_file, grep, find_files, or list_directory. Do not '
        'write files through scripts or redirection; use write_file, '
        'replace_text, or apply_patch. Use verify instead when the command is '
        'intended as formal completion evidence. For multiline scripts, pass '
        'command="python -" or command="node" and put the script in stdin; '
        'do not embed a POSIX heredoc in command. '
        + (
            'Commands run through Windows cmd.exe, which does not support '
            'the POSIX << heredoc syntax.'
            if os.name == 'nt'
            else 'Commands run through the platform default shell.'
        )
    )
    input_model = RunCommandInput
    effect = 'process'

    async def execute(self, arguments: RunCommandInput) -> ToolResult:
        if os.name == 'nt' and has_unquoted_heredoc(arguments.command):
            raise ToolExecutionError(
                'unsupported_shell_syntax',
                'Windows cmd.exe does not support POSIX << heredocs. Use '
                'command="python -" or command="node" and pass the '
                'multiline program in the stdin field.',
                details={
                    'shell': 'cmd.exe',
                    'supported_fields': [
                        'command',
                        'cwd',
                        'timeout_seconds',
                        'stdin',
                    ],
                },
            )
        destructive_reason = destructive_git_command_reason(arguments.command)
        if destructive_reason is not None:
            raise ToolExecutionError(
                'destructive_git_command_denied',
                'run_command cannot discard repository changes. Use '
                'ForgeCode checkpoints or ask the user before restoring '
                'files.',
                details={'detected': destructive_reason},
            )
        read_reason = shell_file_read_reason(arguments.command)
        if read_reason is not None:
            raise ToolExecutionError(
                'shell_file_read_denied',
                'run_command cannot be used as a substitute for repository '
                'reading tools. Use read_file, list_directory, grep, or '
                'find_files so ForgeCode can track the evidence.',
                details={'detected': read_reason},
            )
        denied_reason = shell_file_write_reason(arguments.command)
        if denied_reason is not None:
            raise ToolExecutionError(
                'shell_file_write_denied',
                'run_command cannot be used to write repository files. '
                'Use write_file, replace_text, or apply_patch instead.',
                details={'detected': denied_reason},
            )
        if arguments.stdin is not None:
            stdin_read_reason = shell_file_read_reason(arguments.stdin)
            if stdin_read_reason is not None:
                raise ToolExecutionError(
                    'shell_file_read_denied',
                    'run_command stdin cannot bypass repository reading '
                    'tools. Use read_file, list_directory, grep, or find_files.',
                    details={'detected': stdin_read_reason},
                )
            stdin_write_reason = shell_file_write_reason(arguments.stdin)
            if stdin_write_reason is not None:
                raise ToolExecutionError(
                    'shell_file_write_denied',
                    'run_command stdin cannot write repository files. Use '
                    'write_file, replace_text, or apply_patch instead.',
                    details={'detected': stdin_write_reason},
                )
        cwd = resolve_repository_path(self.root, arguments.cwd)
        if not cwd.is_dir():
            raise ToolExecutionError(
                'not_a_directory',
                f'Command cwd is not a directory: {arguments.cwd}',
            )
        result = await run_process(
            arguments.command,
            cwd=cwd,
            timeout_seconds=arguments.timeout_seconds,
            input_text=arguments.stdin,
            shell=True,
        )
        metadata = {
            **process_metadata(result),
            'command': arguments.command,
            'cwd': display_path(self.root, cwd),
            'stdin_characters': len(arguments.stdin or ''),
        }
        content = render_process_output(result)
        if result.timed_out:
            return ToolResult.fail(
                'command_timeout',
                f'Command timed out after {arguments.timeout_seconds:g}s.',
                content=content,
                metadata=metadata,
            )
        if result.exit_code != 0:
            return ToolResult.fail(
                'command_failed',
                f'Command exited with code {result.exit_code}.',
                content=content,
                metadata=metadata,
            )
        return ToolResult.ok(
            f'Command completed with exit code 0 in '
            f'{result.duration_seconds:.3f}s.',
            content=content,
            metadata=metadata,
        )


DESTRUCTIVE_GIT_PATTERN = re.compile(
    r'(?:^|[|;&]+\s*)git(?:\.exe)?'
    r'(?:\s+-[A-Za-z]\s+\S+)*'
    r'\s+(checkout|restore|reset|clean)\b',
    re.IGNORECASE,
)


def destructive_git_command_reason(command: str) -> str | None:
    '''Reject Git operations that can discard uncommitted workspace state.'''
    match = DESTRUCTIVE_GIT_PATTERN.search(command)
    if match is None:
        return None
    return f'git {match.group(1).casefold()}'


SCRIPT_WRITE_PATTERNS = (
    (
        re.compile(
            r'\b(?:writeFile|writeFileSync|appendFile|appendFileSync)\s*\(',
            re.IGNORECASE,
        ),
        'Node filesystem write API',
    ),
    (
        re.compile(r'\.(?:write_text|write_bytes)\s*\(', re.IGNORECASE),
        'Python pathlib write API',
    ),
    (
        re.compile(
            r'\bopen\s*\([^\n]*,\s*[\x27\x22](?:w|a|x|\+)',
            re.IGNORECASE,
        ),
        'Python writable open mode',
    ),
    (
        re.compile(
            r'\b(?:Set-Content|Add-Content|Out-File)\b',
            re.IGNORECASE,
        ),
        'PowerShell file-writing command',
    ),
)


SCRIPT_READ_PATTERNS = (
    (re.compile(r'\bGet-Content\b', re.IGNORECASE), 'PowerShell Get-Content'),
    (re.compile(r'\bGet-ChildItem\b', re.IGNORECASE), 'PowerShell Get-ChildItem'),
    (re.compile(r'(^|[|;&]\s*)\b(?:cat|head|tail|nl)\b', re.IGNORECASE), 'shell file reader'),
    (re.compile(r'(^|[|;&]\s*)\bsed\s+-n\b', re.IGNORECASE), 'sed line reader'),
)


def shell_file_read_reason(command: str) -> str | None:
    '''Detect shell commands that bypass repository evidence tracking.'''
    for pattern, reason in SCRIPT_READ_PATTERNS:
        if pattern.search(command):
            return reason
    return None


def shell_file_write_reason(command: str) -> str | None:
    '''Detect common direct file-writing shortcuts before shell execution.'''
    for pattern, reason in SCRIPT_WRITE_PATTERNS:
        if pattern.search(command):
            return reason
    if has_unquoted_output_redirection(command):
        return 'shell output redirection'
    return None


def has_unquoted_output_redirection(command: str) -> bool:
    single_quoted = False
    double_quoted = False
    escaped = False
    for index, character in enumerate(command):
        if escaped:
            escaped = False
            continue
        if character == '\\':
            escaped = True
            continue
        if character == chr(39) and not double_quoted:
            single_quoted = not single_quoted
            continue
        if character == chr(34) and not single_quoted:
            double_quoted = not double_quoted
            continue
        if character != '>' or single_quoted or double_quoted:
            continue
        following = command[index + 1:index + 2]
        preceding = command[index - 1:index] if index else ''
        if following == '&' or preceding == '=':
            continue
        return True
    return False


def has_unquoted_heredoc(command: str) -> bool:
    '''Detect POSIX heredoc operators without matching quoted bit shifts.'''
    single_quoted = False
    double_quoted = False
    escaped = False
    for index, character in enumerate(command):
        if escaped:
            escaped = False
            continue
        if character == '\\':
            escaped = True
            continue
        if character == chr(39) and not double_quoted:
            single_quoted = not single_quoted
            continue
        if character == chr(34) and not single_quoted:
            double_quoted = not double_quoted
            continue
        if (
            character == '<'
            and not single_quoted
            and not double_quoted
            and command[index + 1:index + 2] == '<'
        ):
            return True
    return False
