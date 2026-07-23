'''Command-line entry point for ForgeCode.'''

import asyncio
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from forge import __version__
from forge.config import ConfigurationError, ForgeConfig
from forge.hooks import HookConfigurationError, HookManager
from forge.runtime.agent_loop import Conversation
from forge.runtime.model_client import AnthropicModelClient
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
from forge.tools import create_default_registry


app = typer.Typer(
    name='forge',
    help='ForgeCode terminal Agent Harness.',
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
)


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
) -> None:
    '''Start the ForgeCode command-line interface.'''
    if ctx.invoked_subcommand is None:
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
            )
        except (
            ConfigurationError,
            HookConfigurationError,
            SessionError,
        ) as error:
            print_configuration_error(error)
            raise typer.Exit(code=1) from error


def print_configuration_error(error: Exception) -> None:
    '''Print actionable model configuration guidance.'''
    if isinstance(error, SessionError):
        typer.echo('Session could not be resumed.', err=True)
        typer.echo(str(error), err=True)
        return
    if isinstance(error, HookConfigurationError):
        typer.echo('Hook configuration is invalid.', err=True)
        typer.echo(str(error), err=True)
        return
    typer.echo('Model configuration is incomplete.', err=True)
    typer.echo(str(error), err=True)
    typer.echo(
        'Set ANTHROPIC_API_KEY and MODEL_ID before starting ForgeCode.',
        err=True,
    )
    typer.echo(
        'ANTHROPIC_BASE_URL is optional and defaults to the official API.',
        err=True,
    )


def run_interactive_chat(
    session: Conversation | None = None,
    terminal: TerminalUI | None = None,
    recorder: TrajectoryRecorder | None = None,
    journal: SessionJournal | None = None,
    *,
    continue_session: bool = False,
    resume_identifier: str | None = None,
    fork_session: bool = False,
) -> None:
    '''Run a local chat session until the user interrupts it.'''
    resumed_state: SessionState | None = None
    if session is None:
        resolved_session, resolved_journal, resumed_state = (
            create_session_runtime(
                Path.cwd(),
                continue_session=continue_session,
                resume_identifier=resume_identifier,
                fork_session=fork_session,
            )
        )
    else:
        resolved_session = session
        resolved_journal = journal or getattr(
            resolved_session,
            'session_journal',
            None,
        )
    resolved_terminal = terminal if terminal is not None else TerminalUI()
    resolved_recorder = (
        recorder
        if recorder is not None
        else create_trajectory_recorder(Path.cwd())
    )
    client = getattr(resolved_session, 'client', None)
    model = getattr(client, 'model', 'configured model')
    resolved_terminal.show_welcome(model)
    start_interactive_session(
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

    while True:
        resume_options = build_resume_options(resolved_session)
        resolved_terminal.set_resume_options(resume_options)
        try:
            prompt = resolved_terminal.read_prompt()
        except (KeyboardInterrupt, EOFError, typer.Abort):
            stop_interactive_session(
                resolved_session,
                fallback_journal=resolved_journal,
                reason='input_exit',
            )
            resolved_terminal.show_goodbye()
            return

        if not prompt.strip():
            continue

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
                resolved_terminal.show_compaction(asyncio.run(compact()))
            continue

        if prompt.strip() == '/task':
            resolved_terminal.show_notice('Task', resolved_session.task_show())
            continue

        if prompt.strip() == '/task history':
            resolved_terminal.show_notice(
                'Task',
                resolved_session.task_history(),
            )
            continue

        if prompt.strip() == '/status':
            resolved_terminal.show_notice(
                'Session',
                resolved_session.session_status(),
            )
            continue

        if prompt.strip() == '/history':
            resolved_terminal.show_notice(
                'History',
                resolved_session.session_history(),
            )
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
            selected = resolved_terminal.select_session(resume_options)
            if selected is not None:
                try:
                    notice = resume_interactive_session(
                        resolved_session, selected
                    )
                    resolved_terminal.show_notice('Session', notice)
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
                notice = resume_interactive_session(
                    resolved_session, identifier
                )
                resolved_terminal.show_notice('Session', notice)
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
                    asyncio.run(branch_with_hooks(name))
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
                    asyncio.run(clear_with_hooks())
                    if clear_with_hooks is not None
                    else resolved_session.session_clear()
                )
                resolved_terminal.show_notice('Session', notice)
            except (OSError, ValueError, SessionError) as error:
                resolved_terminal.show_error(error)
            continue

        if prompt.strip() == '/checkpoints':
            resolved_terminal.show_notice(
                'Checkpoints',
                resolved_session.checkpoint_history(),
            )
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
            with resolved_terminal.stream_response() as response_view:
                asyncio.run(
                    render_streamed_turn(
                        resolved_session,
                        prompt,
                        response_view,
                        resolved_recorder,
                    )
                )
        except (KeyboardInterrupt, typer.Abort):
            stop_interactive_session(
                resolved_session,
                fallback_journal=resolved_journal,
                reason='turn_interrupted',
            )
            resolved_terminal.show_goodbye()
            return
        except Exception as error:
            resolved_terminal.show_error(error)
            continue


async def render_streamed_turn(
    session: Conversation,
    prompt: str,
    response_view: StreamingResponseView,
    recorder: TrajectoryRecorder | None = None,
) -> None:
    '''Forward conversation stream events to the live terminal view.'''
    if recorder is not None:
        recorder.record_user_message(prompt)
    try:
        async for event in session.stream(prompt):
            record_session_event = getattr(
                session,
                'record_session_event',
                None,
            )
            if record_session_event is not None:
                record_session_event(event)
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
        record_session_error = getattr(
            session,
            'record_session_error',
            None,
        )
        if record_session_error is not None:
            record_session_error(error)
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
) -> tuple[Conversation, SessionJournal, SessionState | None]:
    '''Create a new conversation or hydrate one from durable history.'''
    store = SessionStore(root)
    registry = create_default_registry(root)
    hook_manager = HookManager.from_root(root)
    if continue_session or resume_identifier is not None:
        state, journal = store.open(resume_identifier)
        checkpoint_store = CheckpointStore.for_session(
            root,
            journal.path,
            journal.session_id,
        )
        if fork_session:
            source = state
            journal = store.fork(
                source,
                messages=list(state.messages),
                task=state.active_task,
                model=state.info.model,
            )
            checkpoint_store = CheckpointStore.for_session(
                root,
                journal.path,
                journal.session_id,
            )
            state = store.load(journal.session_id)
        config = ForgeConfig.from_env()
        resumed_config = (
            replace(config, model_id=state.info.model)
            if state.info.model
            else config
        )
        conversation = Conversation(
            client=AnthropicModelClient.from_config(resumed_config),
            registry=registry,
            initial_messages=list(state.messages),
            active_task=state.active_task,
            session_journal=journal,
            checkpoint_store=checkpoint_store,
            session_store=store,
            hook_manager=hook_manager,
        )
        if not fork_session:
            journal.record_resumed()
        return conversation, journal, state

    conversation = Conversation(registry=registry)
    client = getattr(conversation, 'client', None)
    journal = store.create(model=str(getattr(client, 'model', '')))
    conversation.session_journal = journal
    conversation.session_store = store
    conversation.checkpoint_store = CheckpointStore.for_session(
        root,
        journal.path,
        journal.session_id,
    )
    conversation.hook_manager = hook_manager
    return conversation, journal, None


def stop_interactive_session(
    session: Conversation,
    *,
    fallback_journal: SessionJournal | None,
    reason: str,
) -> None:
    '''Run SessionEnd before durably marking the active session stopped.'''
    end = getattr(session, 'session_end', None)
    if end is not None:
        asyncio.run(end(reason=reason))
    active_journal = getattr(
        session,
        'session_journal',
        fallback_journal,
    )
    if active_journal is not None:
        active_journal.record_stopped()


def start_interactive_session(
    session: Conversation,
    *,
    source: str,
) -> None:
    '''Start Hook-aware sessions while preserving embeddable test doubles.'''
    start = getattr(session, 'session_start', None)
    if start is not None:
        asyncio.run(start(source=source))


def resume_interactive_session(
    session: Conversation,
    identifier: str,
) -> str:
    '''Switch sessions through lifecycle hooks when the runtime supports it.'''
    resume_with_hooks = getattr(session, 'session_resume_with_hooks', None)
    if resume_with_hooks is not None:
        return asyncio.run(resume_with_hooks(identifier))
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
        if info.session_id == current_id:
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


@app.command('config')
def show_config() -> None:
    '''Check the Anthropic-compatible model configuration.'''
    try:
        config = ForgeConfig.from_env()
    except ConfigurationError as error:
        print_configuration_error(error)
        raise typer.Exit(code=1) from error

    typer.echo('Anthropic configuration is ready.')
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


if __name__ == '__main__':
    app()
