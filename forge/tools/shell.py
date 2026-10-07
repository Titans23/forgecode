'''Local command execution tool and shared subprocess helpers.'''

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import tempfile
import sys
import json
from time import perf_counter

from pydantic import Field

from forge.tools.base import (
    Tool,
    ToolExecutionError,
    ToolInput,
    ToolResult,
    file_access_guard,
    display_path,
    resolve_repository_path,
)
from forge.runtime.profile import ExecutionProfile


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_artifact: str | None = None
    stderr_artifact: str | None = None
    output_incomplete: bool = False


MAX_PROCESS_OUTPUT_BYTES = 1_000_000
PROCESS_CLEANUP_SECONDS = 2.0
SENSITIVE_ENV_MARKERS = (
    'API_KEY',
    'TOKEN',
    'SECRET',
    'PASSWORD',
    'CREDENTIAL',
    'PRIVATE_KEY',
)


async def run_process(
    command: list[str] | str,
    *,
    cwd: Path,
    timeout_seconds: float,
    input_text: str | None = None,
    shell: bool = False,
    max_output_bytes: int = MAX_PROCESS_OUTPUT_BYTES,
    artifact_root: Path | None = None,
) -> ProcessResult:
    '''Run one sanitized subprocess with bounded output and tree termination.'''
    if max_output_bytes < 1:
        raise ValueError('max_output_bytes must be positive')
    access = file_access_guard.get()
    if access is not None:
        timeout_seconds = min(timeout_seconds, access.limits['wall_time_seconds'])
        # Two streams plus JSON escaping must fit the one-frame helper response.
        max_output_bytes = min(max_output_bytes, access.limits['command_output_bytes'] // 2, 65536)
    job = None
    if os.name == 'nt':
        from forge.tools.windows_job import WindowsJob
        job = WindowsJob()
    try:
        return await _run_process(command, cwd=cwd, timeout_seconds=timeout_seconds,
                                  input_text=input_text, shell=shell,
                                  max_output_bytes=max_output_bytes, process_job=job,
                                  artifact_root=None if access is not None else artifact_root or cwd)
    finally:
        if job is not None:
            job.close()


async def _run_process(
    command, *, cwd, timeout_seconds, input_text, shell, max_output_bytes, process_job, artifact_root,
) -> ProcessResult:
    started = perf_counter()
    stdin = asyncio.subprocess.PIPE if input_text is not None else None
    process_options: dict[str, object] = {
        'cwd': cwd,
        'stdin': stdin,
        'stdout': asyncio.subprocess.PIPE,
        'stderr': asyncio.subprocess.PIPE,
        'env': sanitized_process_environment(),
    }
    if os.name == 'nt':
        import subprocess
        from forge.release.processes import worker_argv
        if (shell and not isinstance(command, str)) or (not shell and isinstance(command, str)):
            raise TypeError('Shell commands must be strings; executable commands must be lists.')
        process_options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        process_options['stdin'] = asyncio.subprocess.PIPE
        command = worker_argv(command,shell=shell)
        shell = False
    else:
        process_options['start_new_session'] = True
        if sys.platform.startswith('linux'):
            if (shell and not isinstance(command, str)) or (not shell and isinstance(command, str)):
                raise TypeError('Shell commands must be strings; executable commands must be lists.')
            from forge.release.processes import worker_argv
            command = worker_argv(command,shell=shell)
            shell = False
    if shell:
        if not isinstance(command, str):
            raise TypeError('Shell commands must be strings.')
        process = await asyncio.create_subprocess_shell(command, **process_options)
    else:
        if isinstance(command, str):
            raise TypeError('Executable commands must be argument lists.')
        process = await asyncio.create_subprocess_exec(*command, **process_options)
    if process_job is not None:
        try:
            process_job.assign(process.pid)
        except BaseException:
            process.kill()  # Only the gated worker exists; no command has run.
            await process.communicate()
            raise

    stdout_task = asyncio.create_task(
        _read_bounded(process.stdout, max_output_bytes, artifact_root)
    )
    stderr_task = asyncio.create_task(
        _read_bounded(process.stderr, max_output_bytes, artifact_root)
    )
    output_incomplete = False
    try:
        # The timeout covers stdin backpressure AND inherited output pipes,
        # 超时覆盖完整输入输出过程；父进程退出后，子进程仍可能占着输出管道。
        # not just the parent process wait. A child may never consume stdin,
        # or may keep stdout open after its parent has exited.
        async with asyncio.timeout(timeout_seconds):
            if process.stdin is not None:
                try:
                    process.stdin.write((b'!' if process_job is not None else b'')
                                        + (input_text or '').encode('utf-8'))
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    process.stdin.close()
            await process.wait()
            await asyncio.shield(asyncio.gather(stdout_task, stderr_task))
        timed_out = False
    except TimeoutError:
        output_incomplete = await _stop_and_collect(process, process_job, stdout_task, stderr_task)
        timed_out = True
    except asyncio.CancelledError:
        # A stopped ForgeCode turn must not leave a compiler, test runner, or
        # shell child alive after the caller has already moved on.
        await _stop_and_collect(process, process_job, stdout_task, stderr_task)
        raise
    stdout_bytes, stdout_total, stdout_truncated, stdout_artifact = await stdout_task
    stderr_bytes, stderr_total, stderr_truncated, stderr_artifact = await stderr_task
    return ProcessResult(
        exit_code=process.returncode if process.returncode is not None else -1,
        stdout=stdout_bytes.decode('utf-8', errors='replace'),
        stderr=stderr_bytes.decode('utf-8', errors='replace'),
        duration_seconds=perf_counter() - started,
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        stdout_bytes=stdout_total,
        stderr_bytes=stderr_total,
        stdout_artifact=stdout_artifact,
        stderr_artifact=stderr_artifact,
        output_incomplete=output_incomplete,
    )


async def _stop_and_collect(process, process_job, *readers) -> bool:
    '''Bound cleanup even if an inherited descriptor or OS process will not exit.'''
    incomplete = False
    # 清理也有截止时间；保留“不完整输出”标记，不能把清理卡住当作正常完成。
    try:
        async with asyncio.timeout(PROCESS_CLEANUP_SECONDS):
            await _terminate_process_tree(process, process_job)
            await process.wait()
            await asyncio.shield(asyncio.gather(*readers))
    except TimeoutError:
        incomplete = True
        if os.name != 'nt':
            import signal
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # asyncio exposes no public method to close subprocess output pipes.
        # Closing the transport stops pipe holders from extending this deadline.
        transport = getattr(process, '_transport', None)
        if transport is not None:
            transport.close()
        for reader in readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
    return incomplete


async def _read_bounded(
    stream: asyncio.StreamReader | None,
    maximum: int,
    artifact_root: Path,
) -> tuple[bytes, int, bool, str | None]:
    if stream is None:
        return b'', 0, False, None
    kept = bytearray()
    tail = bytearray()
    total = 0
    digest = sha256()
    archive = None
    directory = artifact_root / '.forge' / 'context' / 'tool-results' if artifact_root is not None else None
    try:
        while True:
            chunk = await stream.read(65_536)
            if not chunk:
                break
            if directory is not None and archive is None and total + len(chunk) > maximum:
                directory.mkdir(parents=True, exist_ok=True)
                archive = tempfile.NamedTemporaryFile(dir=directory, suffix='.partial', delete=False)
                archive.write(kept)
            if archive is not None:
                archive.write(chunk)
            digest.update(chunk)
            total += len(chunk)
            remaining = maximum - len(kept)
            if remaining > 0:
                kept.extend(chunk[:remaining])
            tail.extend(chunk)
            if len(tail) > maximum // 2:
                del tail[:len(tail) - maximum // 2]
    except asyncio.CancelledError:
        # Preserve the prefix/tail and archive collected before a cleanup bound.
        pass
    finally:
        if archive is not None:
            archive.close()
    artifact_id = None
    if archive is not None:
        artifact_id = digest.hexdigest()
        Path(archive.name).replace(directory / f'{artifact_id}.txt')
    if total > maximum:
        kept = kept[:maximum - len(tail)] + tail
    return bytes(kept), total, total > len(kept), artifact_id


async def _terminate_process_tree(process: asyncio.subprocess.Process, process_job=None) -> None:
    if process_job is not None:
        process_job.close()
        return
    if os.name == 'nt':
        if process.returncode is not None:
            return
        killer = await asyncio.create_subprocess_exec(
            'taskkill',
            '/PID',
            str(process.pid),
            '/T',
            '/F',
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
        if process.returncode is None:
            process.kill()
        return
    import signal

    try:
        if sys.platform.startswith('linux'):
            # The worker owns detached descendants; let it reap them first.
            os.kill(process.pid, signal.SIGTERM)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def sanitized_process_environment() -> dict[str, str]:
    '''Remove common credentials before launching repository processes.'''
    return {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in SENSITIVE_ENV_MARKERS)
    }


def process_metadata(result: ProcessResult) -> dict[str, object]:
    return {
        'exit_code': result.exit_code,
        'stdout': result.stdout,
        'stderr': result.stderr,
        'duration_seconds': result.duration_seconds,
        'timed_out': result.timed_out,
        'stdout_truncated': result.stdout_truncated,
        'stderr_truncated': result.stderr_truncated,
        'stdout_bytes': result.stdout_bytes,
        'stderr_bytes': result.stderr_bytes,
        'stdout_artifact': result.stdout_artifact,
        'stderr_artifact': result.stderr_artifact,
        'output_incomplete': result.output_incomplete,
    }


def render_process_output(result: ProcessResult) -> str:
    sections: list[str] = []
    if result.stdout:
        suffix = (
            f'\n[stdout head/tail preview; {result.stdout_bytes} bytes total; '
            + (f'read_context_artifact artifact_id={result.stdout_artifact}]' if result.stdout_artifact else 'output truncated]')
            if result.stdout_truncated
            else ''
        )
        sections.append(f'stdout:\n{result.stdout.rstrip()}{suffix}')
    if result.stderr:
        suffix = (
            f'\n[stderr head/tail preview; {result.stderr_bytes} bytes total; '
            + (f'read_context_artifact artifact_id={result.stderr_artifact}]' if result.stderr_artifact else 'output truncated]')
            if result.stderr_truncated
            else ''
        )
        sections.append(f'stderr:\n{result.stderr.rstrip()}{suffix}')
    return '\n\n'.join(sections)


class RunCommandInput(ToolInput):
    command: str = Field(min_length=1)
    cwd: str = '.'
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    stdin: str | None = Field(default=None, max_length=8_000)


class RunCommandTool(Tool[RunCommandInput]):
    name = 'run_command'
    description = (
        'Run an executable command for exploration, diagnostics, '
        'or development. Do not use it to display source files or directory '
        'trees; use read_file, grep, find_files, or list_directory. In the '
        'supervised host profile, do not create directories or write files '
        'through scripts or redirection; use create_directory, write_file, or '
        'apply_patch. A disposable sandbox profile may explicitly allow setup '
        'writes. Use verify instead when the command is '
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

    def __init__(
        self,
        root: Path,
        *,
        execution_profile: ExecutionProfile | None = None,
        allow_container_writes: bool = False,
    ) -> None:
        super().__init__(root)
        self.execution_profile = execution_profile or (
            ExecutionProfile.sandbox()
            if allow_container_writes
            else ExecutionProfile.host()
        )

    @property
    def allow_container_writes(self) -> bool:
        '''Compatibility view for callers using the pre-profile API.'''
        return self.execution_profile.allow_command_file_writes

    async def execute(self, arguments: RunCommandInput) -> ToolResult:
        if not self.execution_profile.allow_command_file_writes:
            directory_write = shell_directory_write_reason(arguments.command)
            if directory_write is not None:
                raise ToolExecutionError(
                    'shell_directory_write_denied',
                    'run_command cannot create repository directories. Use the '
                    'create_directory tool so the directory has a Git marker '
                    'and is visible to completion tracking.',
                    details={'detected': directory_write},
                )
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
        if not self.execution_profile.allow_command_file_writes:
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
                    'Use write_file or apply_patch instead.',
                    details={'detected': denied_reason},
                )
        if (
            arguments.stdin is not None
            and not self.execution_profile.allow_command_file_writes
        ):
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
                    'write_file or apply_patch instead.',
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
            artifact_root=self.root,
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


def shell_directory_write_reason(command: str) -> str | None:
    '''Detect direct directory creation that bypasses workspace tracking.'''
    normalized = command.lstrip()
    if re.search(r'(?i)(?:^|[;&|]\s*)mkdir\s+', normalized):
        return 'mkdir command'
    if os.name == 'nt' and re.search(
        r'(?i)(?:^|[;&|]\s*)md\s+', normalized
    ):
        return 'md command'
    if re.search(
        r'(?i)(?:^|[;&|]\s*)New-Item\b[^\r\n]*'
        r'(?:-ItemType\s+(?:Directory|Container)|-Type\s+(?:Directory|Container))',
        normalized,
    ):
        return 'New-Item directory command'
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
