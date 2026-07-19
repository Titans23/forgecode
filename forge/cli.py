'''Command-line entry point for ForgeCode.'''

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from forge import __version__
from forge.config import ConfigurationError, ForgeConfig
from forge.runtime.agent_loop import Conversation
from forge.runtime.state import (
    CompletionBlocked,
    ModelTextDelta,
    ModelUsageUpdate,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
)
from forge.sessions.trajectory import TrajectoryRecorder
from forge.terminal import StreamingResponseView, TerminalUI
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
) -> None:
    '''Start the ForgeCode command-line interface.'''
    if ctx.invoked_subcommand is None:
        try:
            run_interactive_chat()
        except ConfigurationError as error:
            print_configuration_error(error)
            raise typer.Exit(code=1) from error


def print_configuration_error(error: ConfigurationError) -> None:
    '''Print actionable model configuration guidance.'''
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
) -> None:
    '''Run a local chat session until the user interrupts it.'''
    resolved_session = (
        session
        if session is not None
        else Conversation(registry=create_default_registry(Path.cwd()))
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

    while True:
        try:
            prompt = resolved_terminal.read_prompt()
        except (KeyboardInterrupt, EOFError, typer.Abort):
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
            if recorder is not None:
                recorder.record_event(event)
            if isinstance(event, ModelTextDelta):
                response_view.append_text(event.text)
            elif isinstance(event, ModelUsageUpdate):
                response_view.update_usage(event.usage)
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
