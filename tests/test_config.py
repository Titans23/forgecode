'''Tests for environment-backed ForgeCode configuration.'''

from pathlib import Path

import pytest

from forge.config import (
    DEFAULT_ANTHROPIC_BASE_URL,
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
