'''Command-line entry point for ForgeCode.'''

from typing import Annotated

import typer

from forge import __version__
from forge.config import ConfigurationError, ForgeConfig


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
        typer.echo('ForgeCode CLI is ready.')
        typer.echo('Agent runtime is not implemented yet.')
        typer.echo('Run forge --help to see available options.')


@app.command('config')
def show_config() -> None:
    '''Check the Anthropic-compatible model configuration.'''
    try:
        config = ForgeConfig.from_env()
    except ConfigurationError as error:
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
        raise typer.Exit(code=1) from error

    typer.echo('Anthropic configuration is ready.')
    typer.echo(f'Model ID: {config.model_id}')
    typer.echo(f'Base URL: {config.base_url}')
    typer.echo('API key: configured')


if __name__ == '__main__':
    app()
