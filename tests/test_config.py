'''Tests for environment-backed ForgeCode configuration.'''

from pathlib import Path

import pytest

import forge.config as config_module
from forge.config import (
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_MODEL_MAX_TOKENS,
    DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS,
    ConfigurationError,
    ForgeConfig,
)


def test_config_uses_official_base_url_by_default() -> None:
    config = ForgeConfig.from_env(
        {
            'ANTHROPIC_API_KEY': ' test-key ',
            'MODEL_ID': ' claude-test ',
        }
    )

    assert config.api_key == 'test-key'
    assert config.model_id == 'claude-test'
    assert config.base_url == DEFAULT_ANTHROPIC_BASE_URL
    assert config.max_tokens == DEFAULT_MODEL_MAX_TOKENS
    assert config.context_window is None
    assert (
        config.request_timeout_seconds
        == DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS
    )


def test_config_accepts_anthropic_compatible_base_url() -> None:
    config = ForgeConfig.from_env(
        {
            'ANTHROPIC_API_KEY': 'test-key',
            'MODEL_ID': 'claude-test',
            'ANTHROPIC_BASE_URL': 'http://localhost:8080/anthropic/',
        }
    )

    assert config.base_url == 'http://localhost:8080/anthropic'


def test_config_loads_dotenv_from_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('MODEL_ID', raising=False)
    monkeypatch.delenv('ANTHROPIC_BASE_URL', raising=False)
    (tmp_path / '.env').write_text(
        'ANTHROPIC_API_KEY=dotenv-key\n'
        'MODEL_ID=dotenv-model\n'
        'ANTHROPIC_BASE_URL=http://localhost:8080/anthropic/\n',
        encoding='utf-8',
    )

    config = ForgeConfig.from_env()

    assert config.api_key == 'dotenv-key'
    assert config.model_id == 'dotenv-model'
    assert config.base_url == 'http://localhost:8080/anthropic'


def test_config_loads_user_dotenv_outside_the_installation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    working_directory = tmp_path / 'workspace'
    working_directory.mkdir()
    fallback_env = tmp_path / 'forgecode.env'
    fallback_env.write_text(
        'ANTHROPIC_API_KEY=fallback-key\n'
        'MODEL_ID=fallback-model\n',
        encoding='utf-8',
    )
    monkeypatch.chdir(working_directory)
    monkeypatch.setattr(config_module, 'FORGECODE_ENV_PATH', fallback_env)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('MODEL_ID', raising=False)

    config = ForgeConfig.from_env()

    assert config.api_key == 'fallback-key'
    assert config.model_id == 'fallback-model'


def test_current_directory_dotenv_takes_priority_over_user_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    working_directory = tmp_path / 'workspace'
    working_directory.mkdir()
    (working_directory / '.env').write_text(
        'ANTHROPIC_API_KEY=workspace-key\n'
        'MODEL_ID=workspace-model\n',
        encoding='utf-8',
    )
    fallback_env = tmp_path / 'forgecode.env'
    fallback_env.write_text(
        'ANTHROPIC_API_KEY=fallback-key\n'
        'MODEL_ID=fallback-model\n',
        encoding='utf-8',
    )
    monkeypatch.chdir(working_directory)
    monkeypatch.setattr(config_module, 'FORGECODE_ENV_PATH', fallback_env)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('MODEL_ID', raising=False)

    config = ForgeConfig.from_env()

    assert config.api_key == 'workspace-key'
    assert config.model_id == 'workspace-model'


def test_environment_variables_override_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'environment-key')
    monkeypatch.setenv('MODEL_ID', 'environment-model')
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'https://environment.example.com')
    (tmp_path / '.env').write_text(
        'ANTHROPIC_API_KEY=dotenv-key\n'
        'MODEL_ID=dotenv-model\n'
        'ANTHROPIC_BASE_URL=https://dotenv.example.com\n',
        encoding='utf-8',
    )

    config = ForgeConfig.from_env()

    assert config.api_key == 'environment-key'
    assert config.model_id == 'environment-model'
    assert config.base_url == 'https://environment.example.com'


@pytest.mark.parametrize('project', [
    'DATABASE_URL=postgresql://example.invalid/app\n',
    'ANTHROPIC_API_KEY=project-key\nMODEL_ID=project-model\n',
])
def test_global_model_config_survives_unrelated_project_env_and_aliases_override(tmp_path, monkeypatch, project):
    global_env = tmp_path / 'user.env'
    global_env.write_text('FORGE_API_KEY=global-key\nFORGE_MODEL=global-model\n', encoding='utf-8')
    workspace = tmp_path / 'project'
    workspace.mkdir()
    (workspace / '.env').write_text(project, encoding='utf-8')
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(config_module, 'FORGECODE_ENV_PATH', global_env)
    for name in ('FORGE_API_KEY', 'FORGE_MODEL', 'FORGE_PROVIDER', 'ANTHROPIC_API_KEY', 'MODEL_ID',
                 'FORGE_BASE_URL', 'ANTHROPIC_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    config = ForgeConfig.from_env()
    use_project = 'project-key' in project
    assert config.api_key == ('project-key' if use_project else 'global-key')
    assert config.model_id == ('project-model' if use_project else 'global-model')


def test_user_defaults_do_not_become_process_overrides_for_later_projects(tmp_path, monkeypatch):
    import os
    global_env = tmp_path / 'user.env'
    global_env.write_text('FORGE_API_KEY=global-key\nFORGE_MODEL=global-model\n', encoding='utf-8')
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    (second / '.env').write_text('ANTHROPIC_API_KEY=second-key\nMODEL_ID=second-model\n', encoding='utf-8')
    monkeypatch.setattr(config_module, 'FORGECODE_ENV_PATH', global_env)
    for name in ('FORGE_API_KEY', 'FORGE_MODEL', 'FORGE_PROVIDER', 'ANTHROPIC_API_KEY', 'MODEL_ID'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(first)
    assert ForgeConfig.from_env().api_key == 'global-key'
    assert 'FORGE_API_KEY' not in os.environ and 'FORGE_MODEL' not in os.environ
    monkeypatch.chdir(second)
    config = ForgeConfig.from_env()
    assert config.api_key == 'second-key' and config.model_id == 'second-model'


def test_config_rejects_missing_api_key() -> None:
    with pytest.raises(ConfigurationError, match='ANTHROPIC_API_KEY'):
        ForgeConfig.from_env({})


def test_config_rejects_missing_model_id() -> None:
    with pytest.raises(ConfigurationError, match='MODEL_ID'):
        ForgeConfig.from_env({'ANTHROPIC_API_KEY': 'test-key'})


def test_config_rejects_invalid_base_url() -> None:
    with pytest.raises(ConfigurationError, match='ANTHROPIC_BASE_URL'):
        ForgeConfig(
            api_key='test-key',
            model_id='claude-test',
            base_url='localhost:8080',
        )


def test_config_reads_and_validates_model_max_tokens() -> None:
    config = ForgeConfig.from_env(
        {
            'ANTHROPIC_API_KEY': 'test-key',
            'MODEL_ID': 'test-model',
            'MODEL_MAX_TOKENS': '16384',
        }
    )

    assert config.max_tokens == 16_384


def test_config_reads_model_context_window() -> None:
    config = ForgeConfig.from_env(
        {
            'ANTHROPIC_API_KEY': 'test-key',
            'MODEL_ID': 'test-model',
            'MODEL_MAX_TOKENS': '8192',
            'MODEL_CONTEXT_WINDOW': '128000',
        }
    )

    assert config.context_window == 128_000


def test_config_reads_model_request_timeout() -> None:
    config = ForgeConfig.from_env(
        {
            'ANTHROPIC_API_KEY': 'test-key',
            'MODEL_ID': 'test-model',
            'MODEL_REQUEST_TIMEOUT_SECONDS': '45.5',
        }
    )

    assert config.request_timeout_seconds == 45.5


@pytest.mark.parametrize('value', ['invalid', '9', '601'])
def test_config_rejects_invalid_model_request_timeout(value: str) -> None:
    with pytest.raises(
        ConfigurationError,
        match='MODEL_REQUEST_TIMEOUT_SECONDS',
    ):
        ForgeConfig.from_env(
            {
                'ANTHROPIC_API_KEY': 'test-key',
                'MODEL_ID': 'test-model',
                'MODEL_REQUEST_TIMEOUT_SECONDS': value,
            }
        )


@pytest.mark.parametrize('value', ['invalid', '1000', '40000'])
def test_config_rejects_invalid_model_max_tokens(value: str) -> None:
    with pytest.raises(ConfigurationError, match='MODEL_MAX_TOKENS'):
        ForgeConfig.from_env(
            {
                'ANTHROPIC_API_KEY': 'test-key',
                'MODEL_ID': 'test-model',
                'MODEL_MAX_TOKENS': value,
            }
        )


@pytest.mark.parametrize('value', ['invalid', '4000', '3000000', '8192'])
def test_config_rejects_invalid_context_window(value: str) -> None:
    with pytest.raises(ConfigurationError, match='MODEL_CONTEXT_WINDOW'):
        ForgeConfig.from_env(
            {
                'ANTHROPIC_API_KEY': 'test-key',
                'MODEL_ID': 'test-model',
                'MODEL_MAX_TOKENS': '8192',
                'MODEL_CONTEXT_WINDOW': value,
            }
        )
