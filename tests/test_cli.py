'''Tests for the M0 ForgeCode CLI shell.'''

from pathlib import Path

import pytest
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


def test_config_command_reports_ready_without_exposing_api_key() -> None:
    result = runner.invoke(
        app,
        ['config'],
        env={
            'ANTHROPIC_API_KEY': 'secret-test-key',
            'MODEL_ID': 'claude-test',
            'ANTHROPIC_BASE_URL': 'https://gateway.example.com/anthropic/',
        },
    )

    assert result.exit_code == 0
    assert 'Anthropic configuration is ready.' in result.stdout
    assert 'Model ID: claude-test' in result.stdout
    assert 'https://gateway.example.com/anthropic' in result.stdout
    assert 'API key: configured' in result.stdout
    assert 'secret-test-key' not in result.stdout


def test_config_command_explains_missing_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('MODEL_ID', raising=False)
    monkeypatch.delenv('ANTHROPIC_BASE_URL', raising=False)

    result = runner.invoke(app, ['config'])

    assert result.exit_code == 1
    assert 'Model configuration is incomplete.' in result.output
    assert 'ANTHROPIC_API_KEY is not set.' in result.output


def test_config_command_explains_missing_model_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.delenv('MODEL_ID', raising=False)
    monkeypatch.delenv('ANTHROPIC_BASE_URL', raising=False)

    result = runner.invoke(app, ['config'])

    assert result.exit_code == 1
    assert 'MODEL_ID is not set.' in result.output
