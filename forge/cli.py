'''Command-line entry point for ForgeCode.'''

import asyncio
from contextlib import suppress
from datetime import datetime
import os
from pathlib import Path
import re
import secrets
import signal
import sys
import threading
from typing import Annotated, Any, Callable

import typer
from click import Choice
from dotenv import dotenv_values, load_dotenv, set_key

from forge import __version__
from forge import config as model_config
from forge.channels import (
    ChannelConfig,
    ChannelConfigurationError,
    InboundMessage,
    load_channel_settings,
)
from forge.channels.feishu import FeishuChannelAdapter, FeishuChannelUnavailable
from forge.channels.gateway import ChannelGateway
from forge.config import ConfigurationError, ForgeConfig
from forge.hooks import HookConfigurationError
from forge.mcp import MCPConfigurationError
from forge.runtime.agent_loop import Conversation
from forge.runtime.completion import TaskPolicy
from forge.runtime.factory import create_runtime, load_runtime_mcp_servers
from forge.runtime.profile import ExecutionProfile
from forge.runtime.state import (
    CompletionBlocked,
    ModelTextDelta,
    ModelUsageUpdate,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
)
from forge.sessions.trajectory import TrajectoryRecorder
from forge.sessions.checkpoint import CheckpointError, CheckpointStore
from forge.sessions.store import (
    SessionError,
    SessionJournal,
    SessionState,
    SessionStore,
)
from forge.terminal import (
    SessionOption,
    StreamingResponseView,
    TerminalUI,
)
from forge.tools.base import is_repository_path_protected


app = typer.Typer(
    name='forge',
    help='ForgeCode terminal Agent Harness.',
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
)
feishu_app = typer.Typer(
    name='feishu',
    help='Feishu setup and pairing commands.',
    add_completion=False,
)
app.add_typer(feishu_app, name='feishu')
config_app = typer.Typer(help='Check or configure the model connection.', invoke_without_command=True,
                         no_args_is_help=False, add_completion=False)
app.add_typer(config_app, name='config')


_NOTICE_COMMANDS = {
    '/task': ('Task', 'task_show'),
    '/task history': ('Task', 'task_history'),
    '/status': ('Session', 'session_status'),
    '/history': ('History', 'session_history'),
    '/skills': ('Skills', 'skill_list'),
    '/mcp': ('MCP Servers', 'mcp_status'),
    '/permission': ('Permissions', 'permission_status'),
    '/checkpoints': ('Checkpoints', 'checkpoint_history'),
}


def version_callback(value: bool) -> None:
    '''Print the installed ForgeCode version and exit.'''
    if value:
        typer.echo(f'ForgeCode {__version__}')
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            '--version',
            '-V',
            callback=version_callback,
            is_eager=True,
            help='Show the ForgeCode version and exit.',
        ),
    ] = False,
    continue_session: Annotated[
        bool,
        typer.Option(
            '--continue',
            '-c',
            help='Resume the most recent session for this project.',
        ),
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option(
            '--resume',
            '-r',
            help='Resume a session by ID or name.',
        ),
    ] = None,
    fork_session: Annotated[
        bool,
        typer.Option(
            '--fork-session',
            help='Fork the resumed session under a new session ID.',
        ),
    ] = False,
    provider: Annotated[str | None, typer.Option('--provider', help='anthropic, openai_responses, or deepseek')] = None,
    model: Annotated[str | None, typer.Option('--model', help='Model identifier for the configured provider')] = None,
    base_url: Annotated[str | None, typer.Option('--base-url', help='Native provider or explicit gateway URL')] = None,
    sandbox: Annotated[str, typer.Option('--sandbox', help='read-only, workspace-write or explicit full-access')] = 'workspace-write',
    approval: Annotated[str, typer.Option('--approval', help='plan, supervised or auto; independent of sandbox')] = 'supervised',
    read_root: Annotated[list[Path] | None, typer.Option('--read-root', help='Additional file-tool read scope')] = None,
    write_root: Annotated[list[Path] | None, typer.Option('--write-root', help='Writable subdirectory; repeat for multiple scopes')] = None,
) -> None:
    '''Start the ForgeCode command-line interface.'''
    if ctx.invoked_subcommand is None:
        previous_overrides = {key: os.environ.get(key) for key in ('FORGE_PROVIDER', 'FORGE_MODEL', 'FORGE_BASE_URL')}
        def restore_overrides():
            for key, value in previous_overrides.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        ctx.call_on_close(restore_overrides)
        if provider is not None:
            if provider not in {'anthropic', 'openai_responses', 'deepseek'}:
                raise typer.BadParameter('Unsupported provider')
            os.environ['FORGE_PROVIDER'] = provider
        if model is not None:
            os.environ['FORGE_MODEL'] = model
        if base_url is not None:
            os.environ['FORGE_BASE_URL'] = base_url
        if continue_session and resume is not None:
            raise typer.BadParameter(
                'Use --continue or --resume, not both.'
            )
        if fork_session and not (continue_session or resume is not None):
            raise typer.BadParameter(
                '--fork-session requires --continue or --resume.'
            )
        try:
            run_interactive_chat(
                continue_session=continue_session,
                resume_identifier=resume,
                fork_session=fork_session,
                sandbox=sandbox, approval=approval, read_roots=tuple(read_root or ()),
                write_roots=tuple(write_root) if write_root is not None else None,
            )
        except (
            ConfigurationError,
            HookConfigurationError,
            MCPConfigurationError,
            ChannelConfigurationError,
            SessionError,
            BlockingIOError,
            ValueError,
            OSError,
        ) as error:
            print_configuration_error(error)
            raise typer.Exit(code=1) from error


def print_configuration_error(error: Exception) -> None:
    '''Print actionable model configuration guidance.'''
    from forge.protocol import ContractError
    if isinstance(error, ContractError):
        typer.echo(f'{error.kind}: {error}', err=True)
        return
    if isinstance(error, (OSError, ValueError)) and not isinstance(error,
            (ConfigurationError, HookConfigurationError, MCPConfigurationError, ChannelConfigurationError, BlockingIOError)):
        typer.echo(str(error), err=True)
        return
    if isinstance(error, BlockingIOError):
        typer.echo('Project is already in use. Stop the active turn before trying again.', err=True)
        return
    if isinstance(error, SessionError):
        typer.echo('Session could not be resumed.', err=True)
        typer.echo(str(error), err=True)
        return
    if isinstance(error, HookConfigurationError):
        typer.echo('Hook configuration is invalid.', err=True)
        typer.echo(str(error), err=True)
        return
    if isinstance(error, MCPConfigurationError):
        typer.echo('MCP configuration is invalid.', err=True)
        typer.echo(str(error), err=True)
        return
    if isinstance(error, ChannelConfigurationError):
        typer.echo('Channel configuration is invalid.', err=True)
        typer.echo(str(error), err=True)
        return
    typer.echo('Model configuration is incomplete.', err=True)
    typer.echo(str(error), err=True)
    typer.echo('Run forge config setup to configure your model connection.', err=True)
    typer.echo(f'User configuration: {model_config.FORGECODE_ENV_PATH}', err=True)


def run_interactive_chat(
    session: Conversation | None = None,
    terminal: TerminalUI | None = None,
    recorder: TrajectoryRecorder | None = None,
    journal: SessionJournal | None = None,
    *,
    continue_session: bool = False,
    resume_identifier: str | None = None,
    fork_session: bool = False,
    sandbox: str = 'workspace-write',
    approval: str = 'supervised',
    read_roots: tuple[Path, ...] = (),
    write_roots: tuple[Path, ...] | None = None,
) -> None:
    '''Run one interactive process inside one long-lived event loop.'''
    asyncio.run(
        _run_interactive_chat(
            session=session,
            terminal=terminal,
            recorder=recorder,
            journal=journal,
            continue_session=continue_session,
            resume_identifier=resume_identifier,
            fork_session=fork_session,
            sandbox=sandbox, approval=approval, read_roots=read_roots, write_roots=write_roots,
        )
    )


async def _run_interactive_chat(
    session: Conversation | None = None,
    terminal: TerminalUI | None = None,
    recorder: TrajectoryRecorder | None = None,
    journal: SessionJournal | None = None,
    *,
    continue_session: bool = False,
    resume_identifier: str | None = None,
    fork_session: bool = False,
    sandbox: str = 'workspace-write',
    approval: str = 'supervised',
    read_roots: tuple[Path, ...] = (),
    write_roots: tuple[Path, ...] | None = None,
) -> None:
    '''Run a local chat session until the user interrupts it.'''
    resumed_state: SessionState | None = None
    if session is None:
        from forge.api import SessionOptions, open_runtime
        resolved_session, resolved_journal, resumed_state, _ = await open_runtime(
            SessionOptions(Path.cwd(), sandbox=sandbox, approval=approval,
                read_roots=read_roots, write_roots=write_roots), runtime_factory=create_session_runtime,
            continue_session=continue_session, resume_identifier=resume_identifier, fork_session=fork_session)
    else:
        resolved_session = session
        resolved_journal = journal or getattr(
            resolved_session,
            'session_journal',
            None,
        )
    try:
        resolved_terminal = terminal if terminal is not None else TerminalUI()
        active_response_view: StreamingResponseView | None = None
        turn_input = None
        auto_queue = True

        async def approve_permission(request: Any):
            if turn_input is not None:
                await turn_input.pause()
            if active_response_view is not None:
                active_response_view.pause_for_prompt()
            try:
                return await asyncio.to_thread(
                    resolved_terminal.select_permission,
                    request,
                )
            finally:
                if active_response_view is not None:
                    active_response_view.resume_after_prompt()
                if turn_input is not None:
                    turn_input.resume()

        permission_manager = getattr(
            resolved_session,
            'permission_manager',
            None,
        )
        if permission_manager is not None:
            permission_manager.approval_handler = approve_permission
            permission_manager.bind_session(resolved_journal)
        resolved_recorder = recorder
        resolved_session.defer_new_sessions = True
        client = getattr(resolved_session, 'client', None)
        model = getattr(client, 'model', 'configured model')
        resolved_terminal.show_welcome(model)
        await start_interactive_session_async(
            resolved_session,
            source='resume' if resumed_state is not None else 'new',
        )
        if resumed_state is not None:
            notice = (
                f'Resumed {resumed_state.info.session_id} with '
                f'{len(resumed_state.messages)} committed message(s).'
            )
            if resumed_state.indeterminate_tools:
                notice += (
                    '\nWarning: '
                    f'{len(resumed_state.indeterminate_tools)} tool execution(s) '
                    'had no durable completion record and will not be replayed.'
                )
            resolved_terminal.show_notice('Session', notice)
            show_session_history(resolved_session, resolved_terminal)

        while True:
            resume_options = build_resume_options(resolved_session)
            resolved_terminal.set_resume_options(resume_options)
            try:
                inbox = getattr(resolved_session, 'inbox', None)
                queued = inbox.peek() if inbox is not None and auto_queue else None
                if queued is not None:
                    from forge.messages import input_text
                    prompt = input_text(queued['content'])
                else:
                    prompt = await read_terminal_prompt(resolved_terminal)
            except (KeyboardInterrupt, EOFError, typer.Abort):
                resolved_terminal.show_goodbye()
                return

            if not prompt.strip():
                continue

            if prompt.strip() == '/pending' and inbox is not None:
                from forge.messages import input_text
                resolved_terminal.show_notice('Pending', '\n'.join(
                    f"{item['id']}: {item['mode']} — {input_text(item['content'])[:100]}"
                    for item in inbox.pending) or 'No queued inputs.')
                continue
            if prompt.startswith('/discard ') and inbox is not None:
                removed = inbox.discard(prompt[len('/discard '):].strip())
                resolved_terminal.show_notice('Pending', 'Removed.' if removed else 'Input ID not found.')
                continue
            if prompt.strip() == '/next' and inbox is not None:
                queued = inbox.peek()
                if queued is None:
                    resolved_terminal.show_notice('Pending', 'No queued inputs.')
                    continue
                from forge.messages import input_text
                prompt = input_text(queued['content'])
                auto_queue = True

            if prompt.strip() == '/context':
                stats = getattr(resolved_session, 'context_stats', None)
                if stats is None:
                    resolved_terminal.show_error(
                        RuntimeError('Context statistics are unavailable.')
                    )
                else:
                    resolved_terminal.show_context(stats)
                continue

            if prompt.strip() == '/compact':
                compact = getattr(resolved_session, 'compact', None)
                if compact is None:
                    resolved_terminal.show_error(
                        RuntimeError('Context compaction is unavailable.')
                    )
                else:
                    resolved_terminal.show_compaction(await compact())
                continue

            notice_command = _NOTICE_COMMANDS.get(prompt.strip())
            if notice_command is not None:
                title, method = notice_command
                resolved_terminal.show_notice(title, getattr(resolved_session, method)())
                continue

            if prompt.strip() == '/history all':
                show_session_history(resolved_session, resolved_terminal, limit=None)
                continue

            if prompt.startswith('/rename '):
                name = prompt[len('/rename '):].strip()
                try:
                    notice = resolved_session.session_rename(name)
                    resolved_terminal.show_notice('Session', notice)
                except ValueError as error:
                    resolved_terminal.show_error(error)
                continue

            if prompt.strip() == '/resume':
                selected = await asyncio.to_thread(
                    resolved_terminal.select_session,
                    resume_options,
                )
                if selected is not None:
                    try:
                        notice = await resume_interactive_session_async(
                            resolved_session, selected
                        )
                        resolved_terminal.show_notice('Session', notice)
                        show_session_history(resolved_session, resolved_terminal)
                    except (OSError, ValueError, SessionError) as error:
                        resolved_terminal.show_error(error)
                elif not resume_options:
                    resolved_terminal.show_notice(
                        'Sessions',
                        'No other saved ForgeCode sessions for this project.',
                    )
                elif not resolved_terminal.supports_session_picker:
                    resolved_terminal.show_notice(
                        'Sessions',
                        resolved_session.session_candidates(),
                    )
                continue

            if prompt.startswith('/resume '):
                identifier = prompt[len('/resume '):].strip()
                try:
                    notice = await resume_interactive_session_async(
                        resolved_session, identifier
                    )
                    resolved_terminal.show_notice('Session', notice)
                    show_session_history(resolved_session, resolved_terminal)
                except (OSError, ValueError, SessionError) as error:
                    resolved_terminal.show_error(error)
                continue

            if prompt.strip() == '/branch' or prompt.startswith('/branch '):
                name = prompt[len('/branch'):].strip() or None
                try:
                    branch_with_hooks = getattr(
                        resolved_session,
                        'session_branch_with_hooks',
                        None,
                    )
                    notice = (
                        await branch_with_hooks(name)
                        if branch_with_hooks is not None
                        else resolved_session.session_branch(name)
                    )
                    resolved_terminal.show_notice('Session', notice)
                except (OSError, ValueError, SessionError) as error:
                    resolved_terminal.show_error(error)
                continue

            if prompt.strip() == '/clear':
                try:
                    clear_with_hooks = getattr(
                        resolved_session,
                        'session_clear_with_hooks',
                        None,
                    )
                    notice = (
                        await clear_with_hooks()
                        if clear_with_hooks is not None
                        else resolved_session.session_clear()
                    )
                    resolved_terminal.show_notice('Session', notice)
                except (OSError, ValueError, SessionError) as error:
                    resolved_terminal.show_error(error)
                continue

            if prompt.strip() == '/skill':
                resolved_terminal.show_error(
                    ValueError('Usage: /skill skill-name')
                )
                continue

            if prompt.startswith('/skill '):
                name = prompt[len('/skill '):].strip()
                if not name:
                    resolved_terminal.show_error(
                        ValueError('Usage: /skill skill-name')
                    )
                else:
                    try:
                        resolved_terminal.show_notice(
                            'Skill',
                            resolved_session.skill_show(name),
                        )
                    except ValueError as error:
                        resolved_terminal.show_error(error)
                continue

            if prompt.startswith('/permission '):
                try:
                    notice = resolved_session.permission_set_mode(
                        prompt[len('/permission '):].strip()
                    )
                    resolved_terminal.show_notice('Permissions', notice)
                except ValueError as error:
                    resolved_terminal.show_error(error)
                continue

            if prompt.strip() == '/undo':
                try:
                    notice = resolved_session.checkpoint_undo()
                    resolved_terminal.show_notice('Checkpoint', notice)
                except (OSError, ValueError, SessionError, CheckpointError) as error:
                    resolved_terminal.show_error(error)
                continue

            if prompt.strip() == '/rewind' or prompt.startswith('/rewind '):
                arguments = prompt[len('/rewind'):].strip().split()
                mode = 'both'
                if arguments and arguments[-1] in {
                    'code',
                    'conversation',
                    'both',
                }:
                    mode = arguments.pop()
                checkpoint_id = arguments[0] if arguments else None
                if len(arguments) > 1:
                    resolved_terminal.show_error(
                        ValueError(
                            'Usage: /rewind [checkpoint-id] '
                            '[code|conversation|both]'
                        )
                    )
                    continue
                try:
                    notice = resolved_session.checkpoint_rewind(
                        checkpoint_id,
                        mode=mode,
                    )
                    resolved_terminal.show_notice('Checkpoint', notice)
                except (OSError, ValueError, SessionError, CheckpointError) as error:
                    resolved_terminal.show_error(error)
                continue

            if prompt.strip().startswith('/task resume '):
                task_id = prompt.strip()[len('/task resume '):].strip()
                if not task_id:
                    resolved_terminal.show_error(
                        ValueError('Usage: /task resume task-id')
                    )
                else:
                    try:
                        notice = resolved_session.task_resume(task_id)
                        resolved_terminal.show_notice('Task', notice)
                    except (OSError, ValueError) as error:
                        resolved_terminal.show_error(error)
                continue

            if prompt.startswith('/remember '):
                payload = prompt[len('/remember '):].strip()
                name, separator, content = payload.partition('|')
                if not separator:
                    resolved_terminal.show_error(
                        ValueError('Usage: /remember name | content')
                    )
                else:
                    try:
                        notice = resolved_session.remember(name.strip(), content.strip())
                        resolved_terminal.show_notice('Memory', notice)
                    except ValueError as error:
                        resolved_terminal.show_error(error)
                continue

            if prompt == '/memory list':
                resolved_terminal.show_notice(
                    'Memory', resolved_session.memory_list()
                )
                continue

            if prompt.startswith('/memory show '):
                resolved_terminal.show_notice(
                    'Memory',
                    resolved_session.memory_show(
                        prompt[len('/memory show '):].strip()
                    ),
                )
                continue

            if prompt.startswith('/memory forget '):
                resolved_terminal.show_notice(
                    'Memory',
                    resolved_session.memory_forget(
                        prompt[len('/memory forget '):].strip()
                    ),
                )
                continue

            if prompt == '/memory rebuild':
                resolved_terminal.show_notice(
                    'Memory', resolved_session.memory_rebuild()
                )
                continue

            if prompt == '/memory consolidate':
                resolved_terminal.show_notice(
                    'Memory', resolved_session.memory_consolidate()
                )
                continue

            try:
                prompt = queued['content'] if queued is not None else expand_file_mentions(prompt, Path.cwd())
            except ValueError as error:
                resolved_terminal.show_notice('File mention', str(error))
                continue

            try:
                if resolved_recorder is None:
                    resolved_recorder = create_trajectory_recorder(Path.cwd())
                with resolved_terminal.stream_response() as response_view:
                    active_response_view = response_view
                    try:
                        render = render_streamed_turn(
                            resolved_session,
                            prompt,
                            response_view,
                            resolved_recorder,
                            **({'input_id': queued['id']} if queued is not None else {}),
                        )
                        if (isinstance(resolved_terminal, TerminalUI) and resolved_terminal.console.is_terminal
                                and resolved_terminal.prompt_session is not None and inbox is not None):
                            from forge.terminal_input import TurnInput
                            from prompt_toolkit.patch_stdout import patch_stdout
                            turn_input = TurnInput(resolved_terminal, resolved_session,
                                lambda text: expand_file_mentions(text, Path.cwd()))
                            turn_input.turn = asyncio.create_task(render)
                            try:
                                with patch_stdout(raw=True):
                                    turn_input.resume()
                                    await turn_input.turn
                            except asyncio.CancelledError:
                                auto_queue = False
                                response_view.cancel()
                                resolved_terminal.show_notice('Cancelled', 'Turn stopped. Use /pending to inspect queued inputs and /next to continue.')
                            finally:
                                await turn_input.pause()
                                turn_input = None
                        else:
                            await render
                    finally:
                        active_response_view = None
            except (KeyboardInterrupt, typer.Abort):
                resolved_terminal.show_goodbye()
                return
            except Exception as error:
                resolved_terminal.show_error(error)
                continue
    finally:
        await stop_interactive_session_async(
            resolved_session, fallback_journal=resolved_journal, reason='exit')


FILE_MENTION_PATTERN = re.compile(r'(?<!\S)@([^\s]+)')


def expand_file_mentions(prompt: str, root: Path) -> str:
    '''Attach bounded UTF-8 files and images referenced as @path.'''
    from forge.messages import MAX_TEXT_BYTES, MAX_IMAGE_BYTES, image_block, normalize_input
    resolved_root = root.resolve()
    references: list[tuple[str, str]] = []
    images = []
    text_bytes = len(prompt.encode('utf-8'))
    seen: set[Path] = set()
    for match in FILE_MENTION_PATTERN.finditer(prompt):
        raw_path = match.group(1)
        candidate = (resolved_root / raw_path).resolve(strict=False)
        try:
            relative = candidate.relative_to(resolved_root)
        except ValueError as error:
            raise ValueError(
                f'@{raw_path} is outside the working directory.'
            ) from error
        if is_repository_path_protected(relative):
            raise ValueError(f'@{raw_path} is a protected file.')
        if not candidate.is_file():
            raise ValueError(f'@{raw_path} is not a file.')
        if candidate in seen:
            continue
        is_image = candidate.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
        limit = MAX_IMAGE_BYTES if is_image else MAX_TEXT_BYTES - text_bytes
        if limit < 0:
            raise ValueError('Referenced text exceeds 256 KiB')
        with candidate.open('rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f'@{raw_path} exceeds the attachment size limit')
        if is_image:
            images.append(image_block(data))
            seen.add(candidate)
            continue
        try:
            content = data.decode('utf-8')
        except UnicodeDecodeError as error:
            raise ValueError(f'@{raw_path} is not a UTF-8 text file.') from error
        seen.add(candidate)
        text_bytes += len(data)
        references.append((relative.as_posix(), content))
    blocks = [
        f'[Referenced file: {path}]\n{content}'
        for path, content in references
    ]
    text = prompt + ('\n\n' + '\n\n'.join(blocks) if blocks else '')
    return normalize_input([{'type': 'text', 'text': text}, *images] if images else text)


async def render_streamed_turn(
    session: Conversation,
    prompt: str,
    response_view: StreamingResponseView,
    recorder: TrajectoryRecorder | None = None,
    *, input_id=None,
) -> None:
    '''Render events; the runtime owns Journal persistence.'''
    if recorder is not None:
        recorder.record_user_message(prompt)
    try:
        async for event in session.stream(prompt, **({'input_id': input_id} if input_id else {})):
            if recorder is not None:
                recorder.record_event(event)
            if isinstance(event, ModelTextDelta):
                response_view.append_text(event.text)
            elif isinstance(event, ModelUsageUpdate):
                response_view.update_usage(
                    event.usage,
                    request_usage=event.request_usage,
                    model_calls=event.model_calls,
                )
            elif isinstance(event, ToolExecutionStarted):
                response_view.start_tool(event.tool_call)
            elif isinstance(event, ToolExecutionCompleted):
                response_view.complete_tool(event.tool_call, event.result)
            elif isinstance(event, CompletionBlocked):
                response_view.block_completion(event.reasons)
            elif isinstance(event, TurnCompleted):
                response_view.complete(event.result)
    except Exception as error:
        if recorder is not None:
            recorder.record_error(error)
        raise


def create_trajectory_recorder(root: Path) -> TrajectoryRecorder:
    '''Create the default append-only recorder for one CLI session.'''
    return TrajectoryRecorder.create(root)


def create_session_runtime(
    root: Path,
    *,
    continue_session: bool = False,
    resume_identifier: str | None = None,
    fork_session: bool = False,
    model_override: str | None = None,
    task_policy: TaskPolicy | None = None,
    execution_profile: ExecutionProfile | None = None,
    allow_container_writes: bool = False,
    bindings: Any | None = None,
) -> tuple[Conversation, SessionJournal, SessionState | None]:
    '''Compatibility wrapper; runtime assembly lives in forge.runtime.factory.'''
    return create_runtime(
        root,
        continue_session=continue_session,
        resume_identifier=resume_identifier,
        fork_session=fork_session,
        model_override=model_override,
        task_policy=task_policy,
        execution_profile=execution_profile,
        allow_container_writes=allow_container_writes,
        conversation_factory=Conversation,
        defer_new_session=True,
        **({'bindings': bindings} if bindings is not None else {}),
    )


async def read_terminal_prompt(terminal: Any) -> str:
    reader = getattr(terminal, 'read_prompt_async', None)
    if reader is not None:
        return await reader()
    return await asyncio.to_thread(terminal.read_prompt)


async def stop_interactive_session_async(
    session: Conversation,
    *,
    fallback_journal: SessionJournal | None,
    reason: str,
) -> None:
    '''Close Hooks and MCP before durably stopping the active session.'''
    close_runtime = getattr(session, 'runtime_close', None)
    if close_runtime is not None:
        cleanup = await close_runtime(reason=reason)
        if isinstance(cleanup, dict) and cleanup.get('state') != 'clean':
            raise RuntimeError(f'Sandbox cleanup could not be confirmed: {cleanup.get("state")}')
    else:
        end = getattr(session, 'session_end', None)
        if end is not None:
            await end(reason=reason)
    active_journal = getattr(
        session,
        'session_journal',
        fallback_journal,
    )
    if active_journal is not None:
        active_journal.record_stopped()


async def start_interactive_session_async(
    session: Conversation,
    *,
    source: str,
) -> None:
    start = getattr(session, 'session_start', None)
    if start is not None:
        await start(source=source)


def show_session_history(session, terminal, *, limit=10):
    journal = getattr(session, 'session_journal', None)
    store = getattr(session, 'session_store', None)
    if store is None or journal is None or journal.deferred:
        terminal.show_notice('History', 'No saved conversation history.')
        return
    try:
        messages, notices = store.transcript(journal.session_id)
        terminal.show_history(journal.session_id, messages, notices=notices, limit=limit)
    except (SessionError, OSError, ValueError) as error:
        terminal.show_notice('History', f'History unavailable: {error}')


async def resume_interactive_session_async(
    session: Conversation,
    identifier: str,
) -> str:
    resume_with_hooks = getattr(session, 'session_resume_with_hooks', None)
    if resume_with_hooks is not None:
        return await resume_with_hooks(identifier)
    return session.session_resume(identifier)








def build_resume_options(
    conversation: Conversation,
) -> tuple[SessionOption, ...]:
    '''Build picker and completion rows for other project sessions.'''
    store = getattr(conversation, 'session_store', None)
    if store is None:
        return ()
    journal = getattr(conversation, 'session_journal', None)
    current_id = getattr(journal, 'session_id', None)
    options: list[SessionOption] = []
    for info in store.list():
        if info.session_id == current_id or info.message_count == 0:
            continue
        label = info.title
        description = '{} · {} · {}'.format(
            session_status_label(info.status),
            format_session_age(info.updated_at),
            info.session_id[-12:],
        )
        options.append(
            SessionOption(
                identifier=info.session_id,
                label=label,
                description=description,
            )
        )
    return tuple(options)


def session_status_label(status: str) -> str:
    return {
        'active': '进行中',
        'stopped': '已停止',
        'completed': '已完成',
        'blocked': '已阻塞',
        'stuck': '已卡住',
    }.get(status, status)


def format_session_age(value: str) -> str:
    try:
        updated = datetime.fromisoformat(value)
        now = datetime.now().astimezone()
        seconds = max(0, int((now - updated).total_seconds()))
    except (TypeError, ValueError):
        return value
    if seconds < 60:
        return '刚刚'
    minutes = seconds // 60
    if minutes < 60:
        return '{} 分钟前'.format(minutes)
    hours = minutes // 60
    if hours < 24:
        return '{} 小时前'.format(hours)
    days = hours // 24
    return '{} 天前'.format(days)


@app.command('sessions')
def list_sessions() -> None:
    '''List saved sessions for the current project.'''
    sessions = SessionStore(Path.cwd()).list()
    if not sessions:
        typer.echo('No saved ForgeCode sessions for this project.')
        return
    for info in sessions:
        label = f' ({info.name})' if info.name else ''
        typer.echo(
            f'{info.session_id}{label} [{info.status}] '
            f'{info.updated_at}'
        )


@config_app.callback()
def show_config(ctx: typer.Context) -> None:
    '''Check effective settings without making a model request or displaying the key.'''
    if ctx.invoked_subcommand is not None:
        return
    try:
        config = ForgeConfig.from_env()
    except ConfigurationError as error:
        print_configuration_error(error)
        raise typer.Exit(code=1) from error

    label = {'anthropic': 'Anthropic', 'openai_responses': 'OpenAI Responses', 'deepseek': 'DeepSeek'}[config.provider]
    typer.echo(f'{label} configuration is ready.')
    typer.echo(f'User configuration: {model_config.FORGECODE_ENV_PATH}')
    typer.echo('Priority: process environment > project .env > user configuration.')
    typer.echo(f'Model ID: {config.model_id}')
    typer.echo(f'Base URL: {config.base_url}')
    typer.echo(f'Max output tokens: {config.max_tokens:,}')
    typer.echo(
        f'Model request timeout: {config.request_timeout_seconds:g} seconds'
    )
    typer.echo(
        'Context window: '
        + (
            f'{config.context_window:,}'
            if config.context_window is not None
            else 'not configured'
        )
    )
    typer.echo('API key: configured')


@config_app.command('init')
def initialize_model_config() -> None:
    '''Create the user configuration without replacing an existing file.'''
    try:
        created = model_config.initialize_user_config()
    except (OSError, ConfigurationError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f'User configuration {"created" if created else "preserved"}: {model_config.FORGECODE_ENV_PATH}')
    typer.echo('Run forge config setup to configure your model connection.')


@config_app.command('path')
def model_config_path() -> None:
    '''Show the user configuration file location.'''
    typer.echo(str(model_config.FORGECODE_ENV_PATH))


@config_app.command('setup')
def setup_model_config(
    from_env: Annotated[Path | None, typer.Option('--from-env', exists=True, dir_okay=False,
                                                help='Import only model settings from this .env file.')] = None,
) -> None:
    '''Configure the user model connection, with hidden API key input.'''
    try:
        if from_env is not None:
            config = ForgeConfig.from_env(dotenv_values(from_env, interpolate=False))
        else:
            values = model_config.read_user_config()
            previous_provider = values.get('FORGE_PROVIDER', 'anthropic')
            providers = ['anthropic', 'openai_responses', 'deepseek']
            typer.echo(f'User configuration: {model_config.FORGECODE_ENV_PATH}')
            provider = typer.prompt('Provider', type=Choice(providers),
                                    default=previous_provider if previous_provider in providers else 'anthropic')
            try:
                previous = ForgeConfig.from_env(values) if provider == previous_provider else None
            except ConfigurationError:
                previous = None
            model = typer.prompt('Model ID', default=previous.model_id if previous else None)
            default_url = ForgeConfig(api_key='unused', model_id='unused', provider=provider).base_url
            base_url = typer.prompt('Base URL', default=previous.base_url if previous else default_url)
            key = typer.prompt('API key (leave empty to keep existing)' if previous else 'API key',
                               hide_input=True, default='' if previous else None, show_default=False)
            config = ForgeConfig.from_env({**values, 'FORGE_PROVIDER': provider, 'FORGE_MODEL': model,
                'FORGE_BASE_URL': base_url, 'FORGE_API_KEY': key or (previous.api_key if previous else '')})
        model_config.save_user_config({
            'FORGE_PROVIDER': config.provider, 'FORGE_MODEL': config.model_id,
            'FORGE_BASE_URL': config.base_url, 'FORGE_API_KEY': config.api_key,
            'MODEL_MAX_TOKENS': str(config.max_tokens),
            'MODEL_CONTEXT_WINDOW': str(config.context_window) if config.context_window is not None else '',
            'MODEL_REQUEST_TIMEOUT_SECONDS': str(config.request_timeout_seconds),
            'FORGE_REASONING_EFFORT': config.reasoning_effort or '',
        })
    except (OSError, ConfigurationError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f'Model settings saved: {model_config.FORGECODE_ENV_PATH}')
    typer.echo('Run forge config to check effective settings. Project .env and process variables take priority.')


@config_app.command('set')
def set_model_config(
    setting: Annotated[str, typer.Argument(help='model, base-url or api-key; use setup to change provider.')],
    value: Annotated[str | None, typer.Argument(help='Omit for api-key to enter it privately.')] = None,
) -> None:
    '''Change one user setting without displaying or passing API keys as arguments.'''
    fields = {'model': 'FORGE_MODEL', 'base-url': 'FORGE_BASE_URL', 'api-key': 'FORGE_API_KEY'}
    if setting not in fields:
        raise typer.BadParameter('Choose model, base-url or api-key. Use forge config setup to change provider.')
    if setting == 'api-key':
        if value is not None:
            raise typer.BadParameter('Omit the API key argument; enter it at the hidden prompt instead.')
        value = typer.prompt('API key', hide_input=True)
    if value is None or not value.strip():
        raise typer.BadParameter('A nonempty value is required.')
    try:
        value = value.strip()
        if setting == 'base-url':
            value = ForgeConfig(api_key='unused', model_id='unused', base_url=value).base_url
        model_config.save_user_config({fields[setting]: value})
    except (OSError, ConfigurationError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f'Saved {setting}: {model_config.FORGECODE_ENV_PATH}')



async def _pair_feishu_user(
    config: ChannelConfig,
    *,
    pairing_code: str,
    timeout_seconds: float,
) -> str:
    adapter = FeishuChannelAdapter(config, pairing_mode=True)
    pairing_loop = asyncio.get_running_loop()
    paired: asyncio.Future[str] = pairing_loop.create_future()

    async def on_message(message: InboundMessage) -> None:
        expected = f'绑定 {pairing_code}'
        code_matches = message.text.strip() == expected
        typer.echo(
            'Pairing event received: '
            f'chat_type={message.chat_type}, '
            f'sender_id={"present" if message.sender_id else "missing"}, '
            f'code_match={"yes" if code_matches else "no"}'
        )
        if (
            message.chat_type == 'p2p'
            and message.sender_id
            and code_matches
        ):
            def complete_pairing() -> None:
                if not paired.done():
                    paired.set_result(message.sender_id)

            pairing_loop.call_soon_threadsafe(complete_pairing)

    async def on_approval(_value: Any) -> None:
        return None

    async def connect() -> None:
        try:
            await adapter.start(on_message, on_approval)
        except Exception as error:
            if not paired.done():
                paired.set_exception(error)

    connection_task = asyncio.create_task(connect())
    try:
        return await asyncio.wait_for(paired, timeout_seconds)
    finally:
        await adapter.stop()
        if not connection_task.done():
            with suppress(Exception):
                await asyncio.wait_for(connection_task, 5)


@feishu_app.command('setup')
def setup_feishu(
    timeout_seconds: Annotated[
        float,
        typer.Option('--timeout', help='Pairing timeout in seconds.'),
    ] = 300,
) -> None:
    '''Pair the first private-chat user and save their open_id to .env.'''
    root = Path.cwd()
    load_dotenv(dotenv_path=root / '.env', override=False)
    if os.environ.get('FEISHU_ALLOWED_USERS', '').strip():
        typer.echo(
            'Feishu is already paired. Edit FEISHU_ALLOWED_USERS in .env '
            'to change the allowed user.'
        )
        return
    if not os.environ.get('FEISHU_APP_ID', '').strip() or not os.environ.get(
        'FEISHU_APP_SECRET', ''
    ).strip():
        typer.echo(
            'Missing FEISHU_APP_ID or FEISHU_APP_SECRET in the project .env.',
            err=True,
        )
        raise typer.Exit(code=1)

    config = ChannelConfig(
        platform='feishu',
        enabled=False,
        transport='websocket',
        appIdEnv='FEISHU_APP_ID',
        appSecretEnv='FEISHU_APP_SECRET',
        tenantId=os.environ.get('FEISHU_TENANT_ID', 'company-main').strip()
        or 'company-main',
        requireMention=False,
    )
    pairing_code = secrets.token_hex(3)
    typer.echo(
        'Pairing Feishu: send this exact private message to the bot within '
        f'{timeout_seconds:g} seconds:'
    )
    typer.echo(f'  绑定 {pairing_code}')
    try:
        sender_id = asyncio.run(
            _pair_feishu_user(
                config,
                pairing_code=pairing_code,
                timeout_seconds=timeout_seconds,
            )
        )
    except TimeoutError:
        typer.echo('Pairing timed out. Run forge feishu setup again.', err=True)
        raise typer.Exit(code=1) from None
    except (FeishuChannelUnavailable, RuntimeError, ValueError) as error:
        typer.echo(f'Feishu pairing failed: {error}', err=True)
        raise typer.Exit(code=1) from error

    env_path = root / '.env'
    set_key(str(env_path), 'FEISHU_ALLOWED_USERS', sender_id, quote_mode='never')
    typer.echo(f'Paired Feishu user: {sender_id}')
    typer.echo('Saved FEISHU_ALLOWED_USERS to .env. Start the gateway with:')
    typer.echo('  uv run forge gateway --channel feishu-main')


@app.command('integrations')
def show_integrations() -> None:
    '''Show channel readiness without displaying credentials.'''
    try:
        settings = load_channel_settings(Path.cwd())
    except ChannelConfigurationError as error:
        print_configuration_error(error)
        raise typer.Exit(code=1) from error
    if not settings.channels:
        typer.echo('No office channels configured.')
        typer.echo('Create .forge/channels.json from examples/channels.feishu.json.')
        return
    try:
        import lark_channel  # noqa: F401
        feishu_sdk = True
    except ImportError:
        feishu_sdk = False
    for name, config in settings.channels.items():
        ready, missing = config.credential_status()
        state = 'ready' if config.enabled and ready else 'disabled' if not config.enabled else 'missing credentials'
        if config.platform == 'feishu' and not feishu_sdk:
            state = 'missing lark-channel-sdk'
        typer.echo(
            f'{name}: {state} · {config.platform} · {config.transport} · '
            f'{len(config.allowed_users)} user(s) · {len(config.allowed_chats)} chat(s)'
        )
        if missing:
            typer.echo('  missing environment: ' + ', '.join(missing))


def _start_windows_ctrl_c_reader(
    on_ctrl_c: Callable[[], None],
) -> Callable[[], None] | None:
    if os.name != 'nt' or not sys.stdin.isatty():
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import msvcrt

        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        get_std_handle = kernel32.GetStdHandle
        get_std_handle.argtypes = [wintypes.DWORD]
        get_std_handle.restype = wintypes.HANDLE
        get_console_mode = kernel32.GetConsoleMode
        get_console_mode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_console_mode.restype = wintypes.BOOL
        set_console_mode = kernel32.SetConsoleMode
        set_console_mode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        set_console_mode.restype = wintypes.BOOL

        handle = get_std_handle(wintypes.DWORD(-10))
        original_mode = wintypes.DWORD()
        if not handle or not get_console_mode(handle, ctypes.byref(original_mode)):
            return None
        if not set_console_mode(handle, original_mode.value & ~0x0001):
            return None
    except (ImportError, OSError, ValueError):
        return None

    stopped = threading.Event()

    def read_console() -> None:
        while not stopped.is_set():
            try:
                character = msvcrt.getwch()
            except (OSError, ValueError):
                return
            if character == '\x03':
                on_ctrl_c()

    threading.Thread(
        target=read_console,
        name='forge-windows-ctrl-c',
        daemon=True,
    ).start()

    def restore() -> None:
        stopped.set()
        set_console_mode(handle, original_mode.value)

    return restore


_GATEWAY_FORCE_EXIT_SECONDS = 10.0


def _exit_gateway_process() -> None:
    try:
        os.write(2, b'Forcing ForgeCode gateway process exit.\n')
    except OSError:
        pass
    os._exit(130)


def _force_exit_if_stuck(process_stopped: threading.Event) -> None:
    if not process_stopped.wait(_GATEWAY_FORCE_EXIT_SECONDS):
        _exit_gateway_process()


def _exit_gateway_process_cleanly() -> None:
    try:
        os.write(1, b'Gateway stopped.\n')
    except OSError:
        pass
    os._exit(0)


async def serve_channel_gateway(
    gateway: ChannelGateway,
    process_stopped: threading.Event | None = None,
) -> bool:
    '''Run a channel gateway with explicit Windows console signal handling.'''
    loop = asyncio.get_running_loop()
    stop_requested = asyncio.Event()
    previous_handlers: list[tuple[int, Any]] = []
    stop_signal_received = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_signal_received
        if stop_signal_received:
            if process_stopped is not None:
                _exit_gateway_process()
            return
        stop_signal_received = True
        if process_stopped is not None:
            threading.Thread(
                target=_force_exit_if_stuck,
                args=(process_stopped,),
                name='forge-gateway-exit-watchdog',
                daemon=True,
            ).start()
        loop.call_soon_threadsafe(stop_requested.set)

    watched_signals = [signal.SIGINT]
    sigbreak = getattr(signal, 'SIGBREAK', None)
    if sigbreak is not None:
        watched_signals.append(sigbreak)
    for watched in watched_signals:
        previous_handlers.append((watched, signal.getsignal(watched)))
        signal.signal(watched, request_stop)
    restore_console_input = _start_windows_ctrl_c_reader(
        lambda: request_stop(signal.SIGINT, None)
    )
    if restore_console_input is not None:
        typer.echo('[Gateway] Windows Ctrl+C direct handler active')

    gateway_task = asyncio.create_task(gateway.run())
    stop_task = asyncio.create_task(stop_requested.wait())
    try:
        done, _ = await asyncio.wait(
            (gateway_task, stop_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            typer.echo('Stopping ForgeCode gateway...')
        if gateway_task in done:
            await gateway_task
    finally:
        stop_task.cancel()
        with suppress(asyncio.CancelledError):
            await stop_task
        try:
            await gateway.close()
            if not gateway_task.done():
                try:
                    await asyncio.wait_for(gateway_task, timeout=5.0)
                except TimeoutError:
                    gateway_task.cancel()
            with suppress(asyncio.CancelledError):
                await gateway_task
        finally:
            if restore_console_input is not None:
                restore_console_input()
            for watched, previous in previous_handlers:
                signal.signal(watched, previous)
    return stop_signal_received


@app.command('gateway')
def run_gateway(
    channel: Annotated[
        str | None,
        typer.Option('--channel', '-C', help='Configured channel name.'),
    ] = None,
) -> None:
    '''Run one official chat channel gateway until interrupted.'''
    root = Path.cwd()
    try:
        settings = load_channel_settings(root)
        enabled = {
            name: value
            for name, value in settings.channels.items()
            if value.enabled
        }
        if channel is None:
            if len(enabled) != 1:
                raise ChannelConfigurationError(
                    'Specify --channel when zero or multiple channels are enabled.'
                )
            channel, config = next(iter(enabled.items()))
        else:
            config = enabled.get(channel)
            if config is None:
                raise ChannelConfigurationError(
                    f'Enabled channel not found: {channel}'
                )
        if config.platform != 'feishu':
            raise ChannelConfigurationError(
                f'{config.platform} gateway is reserved for a later adapter.'
            )
        if config.transport != 'websocket':
            raise ChannelConfigurationError(
                'The Feishu gateway currently requires transport=websocket.'
            )
        ready, missing = config.credential_status()
        if not ready:
            raise ChannelConfigurationError(
                'Missing channel credential environment: ' + ', '.join(missing)
            )
        store = SessionStore(root)
        state_directory = store.directory.parent / 'channels' / channel
        adapter = FeishuChannelAdapter(config)

        def runtime_factory(identifier: str | None):
            return create_session_runtime(
                root,
                resume_identifier=identifier,
            ) if identifier else create_session_runtime(root)

        gateway = ChannelGateway(
            adapter=adapter,
            config=config,
            runtime_factory=runtime_factory,
            state_directory=state_directory,
            progress=typer.echo,
        )

        typer.echo(f'Starting ForgeCode gateway: {channel} ({config.platform})')
        process_stopped = threading.Event()
        interrupted = False
        try:
            interrupted = asyncio.run(
                serve_channel_gateway(gateway, process_stopped)
            )
        finally:
            process_stopped.set()
        if interrupted:
            _exit_gateway_process_cleanly()
        typer.echo('Gateway stopped.')
    except (ChannelConfigurationError, ConfigurationError) as error:
        print_configuration_error(error)
        raise typer.Exit(code=1) from error
    except KeyboardInterrupt:
        typer.echo('Gateway stopped.')


from forge.compatibility_cli import register as register_compatibility_commands
register_compatibility_commands(app)


if __name__ == '__main__':
    app()
