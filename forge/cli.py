'''Command-line entry point for ForgeCode.'''

from typing import Annotated

import typer

from forge import __version__


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


if __name__ == '__main__':
    app()
