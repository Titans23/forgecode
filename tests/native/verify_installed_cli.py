"""Exercise the real npm-installed CLI without requesting a model response.

Set FORGE_NPM_PACKAGE_ROOT to the installed @titans23/forgecode directory.
Run explicitly; installation acceptance is separate from source unit tests.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_installed_cli_starts_and_exits_with_redirected_utf8_io(tmp_path):
    package = Path(os.environ['FORGE_NPM_PACKAGE_ROOT'])
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    if sys.platform == 'win32':
        from forge.sandbox.windows_worker import grant_controller_access
        grant_controller_access(workspace)
    environment = {
        **os.environ,
        'FORGE_PROVIDER': 'anthropic',
        'FORGE_API_KEY': 'offline-install-acceptance',
        'FORGE_MODEL': 'offline-install-acceptance',
        'FORGE_BASE_URL': 'http://127.0.0.1:9',
        'FORGE_DATA_DIR': str(tmp_path / 'sessions'),
        'FEISHU_ENABLED': '0',
    }
    command = [shutil.which('node'), str(package / 'bin/forge.mjs')]
    for _ in range(2):
        result = subprocess.run(command, cwd=workspace, env=environment, input='\n',
            capture_output=True, encoding='utf-8', errors='replace', timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Session ended.' in result.stdout and '\u276f' in result.stdout
    assert not list((tmp_path / 'sessions').rglob('session-*.jsonl'))
    assert not (workspace / '.forge/trajectories').exists()

    from forge.sessions.store import SessionStore
    journal = SessionStore(workspace, data_root=tmp_path / 'sessions').create(
        model='offline-install-acceptance', provider='anthropic')
    for i in range(12):
        journal.record_user_message({'role': 'user', 'content': f'question-{i:02}'}, None)
        journal.record_assistant_message({'role': 'assistant', 'content': f'answer-{i:02}'})
    result = subprocess.run([*command, '--resume', journal.session_id], cwd=workspace,
        env=environment, input='', capture_output=True, encoding='utf-8', errors='replace', timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'question-00' not in result.stdout
    assert 'question-02' in result.stdout and 'answer-11' in result.stdout
    assert '2 earlier turn(s)' in result.stdout
