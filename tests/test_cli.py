'''Tests for the M0 ForgeCode CLI shell.'''

from typer.testing import CliRunner

from forge.cli import app


runner = CliRunner()


def test_cli_starts_without_a_command() -> None:
    result = runner.invoke(app)

    assert result.exit_code == 0
    assert 'ForgeCode CLI is ready.' in result.stdout
    assert 'Agent runtime is not implemented yet.' in result.stdout


def test_cli_help() -> None:
    result = runner.invoke(app, ['--help'])

    assert result.exit_code == 0
    assert 'ForgeCode terminal Agent Harness.' in result.stdout
    assert '--version' in result.stdout


def test_cli_version() -> None:
    result = runner.invoke(app, ['--version'])

    assert result.exit_code == 0
    assert 'ForgeCode 0.1.0' in result.stdout
