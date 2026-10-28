'''Bounded subprocess execution for lifecycle hooks.'''

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any

from forge.hooks.models import HookEvent, HookSpec
from forge.sandbox.process_owner import LocalProcessOwner
from forge.tools.shell import sanitized_process_environment


MAX_HOOK_OUTPUT_BYTES = 1_000_000
AUDIT_PREVIEW_CHARACTERS = 2_000


class HookOutputLimitError(RuntimeError):
    '''Raised as soon as combined Hook output exceeds its hard limit.'''


@dataclass(frozen=True, slots=True)
class CommandResult:
    decision: str
    updated_arguments: dict[str, Any] | None
    additional_context: str
    reason: str
    duration_seconds: float
    exit_code: int | None
    timed_out: bool
    output_preview: str


async def run_hook_command(
    root: Path,
    spec: HookSpec,
    event: HookEvent,
) -> CommandResult:
    '''Run one hook with a JSON stdin payload and bounded output.'''
    command = [_expand(part, event) for part in spec.command]
    payload = json.dumps(
        {
            'event': event.name,
            'session_id': event.session_id,
            'tool': (
                {
                    'id': event.tool_call_id,
                    'name': event.tool_name,
                    'arguments': event.arguments,
                }
                if event.tool_name is not None
                else None
            ),
            'paths': list(event.paths),
            'payload': event.payload,
        },
        ensure_ascii=False,
        default=str,
    ).encode('utf-8')
    environment = sanitized_process_environment()
    environment.update(
        {
            'FORGE_HOOK_EVENT': event.name,
            'FORGE_SESSION_ID': event.session_id or '',
            'FORGE_TOOL_NAME': event.tool_name or '',
            'FORGE_FILE_PATH': event.paths[0] if event.paths else '',
        }
    )
    started = time.monotonic()
    owner = await LocalProcessOwner.start(command, cwd=root, environment=environment,
        output=True, capture_stderr=True)
    process = owner.process
    failure = ''
    timed_out = False
    stdout = stderr = b''
    try:
        async with asyncio.timeout(spec.timeout_seconds):
            stdout, stderr = await _communicate_bounded(process, payload)
    except TimeoutError:
        failure = f'Hook timed out after {spec.timeout_seconds:g} seconds.'
        timed_out = True
    except HookOutputLimitError:
        failure = f'Hook output exceeded {MAX_HOOK_OUTPUT_BYTES} bytes.'
    finally:
        # A cancelled stdin drain or bounded reader can leave output pipes paused.
        # Close our pipes before the owner waits for the process tree to exit.
        for descriptor in (1, 2):
            pipe = process._transport.get_pipe_transport(descriptor)
            if pipe is not None:
                pipe.close()
        cleanup = await owner.close()
    if cleanup.get('state') != 'clean':
        cleanup_failure = 'Hook process cleanup could not be confirmed.'
        failure = f'{failure} {cleanup_failure}' if failure else cleanup_failure
    duration = time.monotonic() - started
    if failure:
        return CommandResult(
            decision='error',
            updated_arguments=None,
            additional_context='',
            reason=failure,
            duration_seconds=duration,
            exit_code=None if timed_out else process.returncode,
            timed_out=timed_out,
            output_preview='',
        )
    decoded_stdout = stdout.decode('utf-8', errors='replace').strip()
    decoded_stderr = stderr.decode('utf-8', errors='replace').strip()
    if process.returncode == 2:
        return CommandResult(
            decision='deny',
            updated_arguments=None,
            additional_context='',
            reason=decoded_stderr or decoded_stdout or 'Hook denied operation.',
            duration_seconds=duration,
            exit_code=2,
            timed_out=False,
            output_preview=_preview(stdout, stderr),
        )
    if process.returncode != 0:
        return CommandResult(
            decision='error',
            updated_arguments=None,
            additional_context='',
            reason=(
                decoded_stderr
                or decoded_stdout
                or f'Hook exited with code {process.returncode}.'
            ),
            duration_seconds=duration,
            exit_code=process.returncode,
            timed_out=False,
            output_preview=_preview(stdout, stderr),
        )
    response: dict[str, Any] = {}
    if decoded_stdout.startswith('{'):
        try:
            parsed = json.loads(decoded_stdout)
        except json.JSONDecodeError as error:
            return CommandResult(
                decision='error',
                updated_arguments=None,
                additional_context='',
                reason=f'Hook returned invalid JSON: {error}',
                duration_seconds=duration,
                exit_code=0,
                timed_out=False,
                output_preview=_preview(stdout, stderr),
            )
        if not isinstance(parsed, dict):
            response = {}
        else:
            response = parsed
    decision = str(response.get('decision', 'allow')).casefold()
    if decision not in {'allow', 'deny'}:
        return CommandResult(
            decision='error',
            updated_arguments=None,
            additional_context='',
            reason=f'Hook returned unsupported decision: {decision}',
            duration_seconds=duration,
            exit_code=0,
            timed_out=False,
            output_preview=_preview(stdout, stderr),
        )
    updated = response.get('updated_arguments')
    if updated is not None and not isinstance(updated, dict):
        return CommandResult(
            decision='error',
            updated_arguments=None,
            additional_context='',
            reason='updated_arguments must be a JSON object.',
            duration_seconds=duration,
            exit_code=0,
            timed_out=False,
            output_preview=_preview(stdout, stderr),
        )
    return CommandResult(
        decision=decision,
        updated_arguments=updated,
        additional_context=str(response.get('additional_context', '')).strip(),
        reason=str(response.get('reason', '')).strip(),
        duration_seconds=duration,
        exit_code=0,
        timed_out=False,
        output_preview=_preview(stdout, stderr),
    )


def _expand(value: str, event: HookEvent) -> str:
    replacements = {
        '{event}': event.name,
        '{session_id}': event.session_id or '',
        '{tool}': event.tool_name or '',
        '{path}': event.paths[0] if event.paths else '',
    }
    for marker, replacement in replacements.items():
        value = value.replace(marker, replacement)
    return value


async def _communicate_bounded(
    process: asyncio.subprocess.Process,
    payload: bytes,
) -> tuple[bytes, bytes]:
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError('Hook subprocess pipes are unavailable.')
    try:
        process.stdin.write(payload)
        await process.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        process.stdin.close()
    total = [0]
    stdout_task = asyncio.create_task(
        _read_bounded(process.stdout, total)
    )
    stderr_task = asyncio.create_task(
        _read_bounded(process.stderr, total)
    )
    try:
        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        await process.wait()
        return stdout, stderr
    except BaseException:
        stdout_task.cancel()
        stderr_task.cancel()
        await asyncio.gather(
            stdout_task,
            stderr_task,
            return_exceptions=True,
        )
        raise


async def _read_bounded(
    stream: asyncio.StreamReader,
    total: list[int],
) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = await stream.read(65_536)
        if not chunk:
            return b''.join(chunks)
        total[0] += len(chunk)
        if total[0] > MAX_HOOK_OUTPUT_BYTES:
            raise HookOutputLimitError
        chunks.append(chunk)


def _preview(stdout: bytes, stderr: bytes) -> str:
    text = '\n'.join(
        part.decode('utf-8', errors='replace').strip()
        for part in (stdout, stderr)
        if part.strip()
    )
    return text[:AUDIT_PREVIEW_CHARACTERS]
