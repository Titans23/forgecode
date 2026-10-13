"""User configuration commands operate on private fixtures, without model calls."""
import os
from pathlib import Path

import pytest
from dotenv import dotenv_values
from typer.testing import CliRunner

from forge.cli import app
import forge.config as settings


runner = CliRunner()


@pytest.fixture
def user_file(tmp_path, monkeypatch):
    path = tmp_path / 'user' / '.forge' / '.env'
    monkeypatch.setattr(settings, 'FORGECODE_ENV_PATH', path)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    for name in tuple(os.environ):
        if name.startswith(('FORGE_', 'MODEL_', 'ANTHROPIC_', 'OPENAI_', 'DEEPSEEK_')):
            monkeypatch.delenv(name)
    return path


def test_init_creates_private_template_and_preserves_existing_bytes(user_file):
    result = runner.invoke(app, ['config', 'init'])
    assert result.exit_code == 0, result.output
    assert str(user_file) in result.output and 'forge config setup' in result.output
    assert not dotenv_values(user_file)
    if os.name != 'nt':
        assert user_file.stat().st_mode & 0o077 == 0
    original = b'# Existing private settings\nFORGE_API_KEY=keep-me\nEXTRA=preserve\n'
    user_file.write_bytes(original)
    result = runner.invoke(app, ['config', 'init'])
    assert result.exit_code == 0 and 'preserved' in result.output
    assert user_file.read_bytes() == original
    assert 'keep-me' not in result.output


@pytest.mark.parametrize('provider', ['anthropic', 'openai_responses', 'deepseek'])
def test_setup_saves_provider_and_hidden_key_for_other_projects(user_file, provider, monkeypatch, tmp_path):
    result = runner.invoke(app, ['config', 'setup'], input=f'{provider}\nfixture-model\n\nprivate-key\n')
    assert result.exit_code == 0, result.output
    assert 'private-key' not in result.output
    other = tmp_path / 'other-project'
    other.mkdir()
    monkeypatch.chdir(other)
    config = settings.ForgeConfig.from_env()
    assert (config.provider, config.model_id, config.api_key) == (provider, 'fixture-model', 'private-key')
    checked = runner.invoke(app, ['config'])
    assert checked.exit_code == 0 and 'configuration is ready' in checked.output
    assert 'private-key' not in checked.output
    if os.name != 'nt':
        assert user_file.stat().st_mode & 0o077 == 0


def test_setup_imports_only_model_settings_and_preserves_source_and_user_notes(user_file, tmp_path):
    source = tmp_path / 'source.env'
    source.write_text('ANTHROPIC_API_KEY=imported-key\nMODEL_ID=imported-model\n'
                      'ANTHROPIC_BASE_URL=https://gateway.example/anthropic\n'
                      'MODEL_MAX_TOKENS=4096\nMODEL_CONTEXT_WINDOW=64000\n'
                      'FEISHU_APP_SECRET=not-a-model-setting\n', encoding='utf-8')
    original_source = source.read_bytes()
    settings.initialize_user_config()
    user_file.write_text('# Preserve this comment\nCUSTOM_SETTING=keep\n', encoding='utf-8')
    result = runner.invoke(app, ['config', 'setup', '--from-env', str(source)])
    assert result.exit_code == 0, result.output
    assert 'imported-key' not in result.output and 'not-a-model-setting' not in result.output
    assert source.read_bytes() == original_source
    text = user_file.read_text(encoding='utf-8')
    assert '# Preserve this comment' in text and 'CUSTOM_SETTING=keep' in text
    assert 'FEISHU_APP_SECRET' not in text
    config = settings.ForgeConfig.from_env()
    assert (config.model_id, config.api_key, config.max_tokens, config.context_window) == (
        'imported-model', 'imported-key', 4096, 64000)


def test_setup_cancel_and_invalid_import_do_not_overwrite_user_config(user_file, tmp_path):
    settings.save_user_config({'FORGE_API_KEY': 'original', 'FORGE_MODEL': 'original-model'})
    before = user_file.read_bytes()
    cancelled = runner.invoke(app, ['config', 'setup'], input='anthropic\n')
    assert cancelled.exit_code != 0 and user_file.read_bytes() == before
    source = tmp_path / 'invalid.env'
    source.write_text('MODEL_ID=missing-key\n', encoding='utf-8')
    invalid = runner.invoke(app, ['config', 'setup', '--from-env', str(source)])
    assert invalid.exit_code != 0 and user_file.read_bytes() == before


def test_setup_keeps_hidden_key_only_for_unchanged_provider(user_file):
    settings.save_user_config({'FORGE_PROVIDER': 'anthropic', 'FORGE_API_KEY': 'existing-key', 'FORGE_MODEL': 'old'})
    result = runner.invoke(app, ['config', 'setup'], input='\nnew-model\n\n\n')
    assert result.exit_code == 0 and 'existing-key' not in result.output
    assert settings.ForgeConfig.from_env().api_key == 'existing-key'
    before = user_file.read_bytes()
    changed = runner.invoke(app, ['config', 'setup'], input='deepseek\nnew-model\n\n\n')
    assert changed.exit_code != 0 and user_file.read_bytes() == before


def test_set_key_round_trips_literal_characters_without_echoing_them(user_file, monkeypatch):
    key = "a'\\b${DO_NOT_EXPAND}"
    monkeypatch.setenv('DO_NOT_EXPAND', 'wrong-value')
    result = runner.invoke(app, ['config', 'set', 'api-key'], input=key + '\n')
    assert result.exit_code == 0, result.output
    assert key not in result.output
    assert runner.invoke(app, ['config', 'set', 'model', 'fixture-model']).exit_code == 0
    assert settings.ForgeConfig.from_env().api_key == key
    assert not (Path.cwd() / '.env').exists()


def test_set_updates_one_field_and_rejects_invalid_values_without_writing(user_file):
    settings.save_user_config({'FORGE_API_KEY': 'existing-key', 'FORGE_MODEL': 'old'})
    result = runner.invoke(app, ['config', 'set', 'base-url', 'https://gateway.example/v1/'])
    assert result.exit_code == 0
    config = settings.ForgeConfig.from_env()
    assert config.api_key == 'existing-key' and config.model_id == 'old'
    assert config.base_url == 'https://gateway.example/v1'
    before = user_file.read_bytes()
    for args in [['base-url', 'invalid'], ['model', ''], ['model', 'line\ninjection'],
                 ['unknown', 'value'], ['api-key', 'do-not-log-this-key']]:
        rejected = runner.invoke(app, ['config', 'set', *args])
        assert rejected.exit_code != 0 and user_file.read_bytes() == before
        assert 'do-not-log-this-key' not in rejected.output


def test_failed_atomic_save_keeps_original_and_removes_temporary_file(user_file, monkeypatch):
    settings.initialize_user_config()
    before = user_file.read_bytes()
    def failed_replace(*args):
        raise OSError('replacement failed')
    monkeypatch.setattr(settings.os, 'replace', failed_replace)
    result = runner.invoke(app, ['config', 'set', 'model', 'not-saved'])
    assert result.exit_code != 0 and user_file.read_bytes() == before
    assert list(user_file.parent.iterdir()) == [user_file]


@pytest.mark.parametrize('provider, key_name', [
    ('anthropic', 'ANTHROPIC_API_KEY'), ('openai_responses', 'OPENAI_API_KEY'), ('deepseek', 'DEEPSEEK_API_KEY'),
])
def test_check_reports_path_and_setup_command_when_not_configured(user_file, provider, key_name):
    assert runner.invoke(app, ['config', 'path']).output.strip() == str(user_file)
    settings.save_user_config({'FORGE_PROVIDER': provider})
    result = runner.invoke(app, ['config'])
    assert result.exit_code == 1
    assert 'forge config setup' in result.output and str(user_file) in result.output
    assert f'{key_name} is not set.' in result.output
    if provider != 'anthropic':
        assert 'ANTHROPIC_API_KEY' not in result.output
