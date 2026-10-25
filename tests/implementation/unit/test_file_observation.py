"""Engineering Git observation tests; native acceptance is separate."""
import asyncio
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from forge.sandbox.file_worker import FileAccess
from forge.sandbox.path_policy import PathPolicy, inspect_path
from forge.tools.shell import ProcessResult


@pytest.fixture
def access(tmp_path, monkeypatch):
    git = shutil.which('git')
    if git is None:
        pytest.skip('Git is required for Git observation')
    subprocess.run([git, 'init', '--quiet', str(tmp_path)], check=True)
    subprocess.run([git, '-C', str(tmp_path), 'config', 'core.autocrlf', 'false'], check=True)
    (tmp_path / 'source.py').write_text('baseline\n')
    (tmp_path / '.gitignore').write_text('ignored.txt\n')
    subprocess.run([git, '-C', str(tmp_path), 'add', '.'], check=True)
    subprocess.run([git, '-C', str(tmp_path), '-c', 'user.name=fixture',
                    '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'base'], check=True)
    from forge.release.toolchains import core_tool
    monkeypatch.setattr('forge.release.toolchains.core_tool',
                        lambda name: Path(git) if name == 'git' else core_tool(name))
    value = object.__new__(FileAccess)
    value.root = tmp_path
    value.policy = PathPolicy(inspect_path(tmp_path, absolute=True), 'strict_allowlist_required',
        (tmp_path,), (tmp_path,), ())
    value.limits = {'wall_time_seconds': 10, 'command_output_bytes': 1048576}
    value.windows_acl = False
    return value


def test_git_snapshot_dirty_paths_and_explicit_ignored_target(access):
    async def run():
        assert (await access.scan()) == {'files': {}, 'complete': True}
        (access.root / 'source.py').write_text('user edit\n')
        (access.root / 'new.txt').write_text('new\n')
        (access.root / 'ignored.txt').write_text('ignored\n')
        result = await access.scan()
        assert result['complete'] and set(result['files']) == {'source.py', 'new.txt', 'ignored.txt'}
        ignored_before = result['files']['ignored.txt']['sha256']
        (access.root / 'ignored.txt').write_text('command changed ignored file\n')
        assert (await access.scan())['files']['ignored.txt']['sha256'] != ignored_before
        assert access.snapshot(('ignored.txt',))['files']['ignored.txt']['sha256']
        (access.root / 'source.py').unlink()
        assert not (await access.scan())['files']['source.py']['exists']
    asyncio.run(run())


@pytest.mark.parametrize('config', ['[include]\n path = external\n',
    '[filter "fixture"]\n clean = unwanted-command\n', '[core]\n worktree = ../outside\n'])
def test_git_unsafe_config_uses_existing_bounded_scan(access, config):
    (access.root / '.git/config').write_text(config)
    access._scan_filesystem = lambda: {'files': {}, 'complete': False, 'reason': 'scan_limit'}
    assert asyncio.run(access.scan())['complete'] is False


@pytest.mark.parametrize('fault', ['truncated', 'unfinished', 'malformed', 'failed', 'timed_out'])
def test_git_incomplete_output_never_claims_complete(access, monkeypatch, fault):
    async def incomplete(*args, **kwargs):
        values = dict(exit_code=0, stdout=' M source.py\0', stderr='', duration_seconds=0)
        if fault == 'truncated':
            values['stdout_truncated'] = True
        elif fault == 'unfinished':
            values['output_incomplete'] = True
        elif fault == 'malformed':
            values['stdout'] = ' M source.py'
        elif fault == 'failed':
            values['exit_code'] = 1
        else:
            values['timed_out'] = True
        return ProcessResult(**values)
    monkeypatch.setattr('forge.tools.shell.run_process', incomplete)
    access._scan_filesystem = lambda: {'files': {}, 'complete': False, 'reason': 'scan_limit'}
    assert asyncio.run(access.scan())['complete'] is False


def test_git_clears_inherited_redirects_and_restores_environment(access, monkeypatch):
    monkeypatch.setenv('GIT_DIR', str(access.root.parent / 'unrelated'))
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'core.fsmonitor')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'unwanted-command')
    previous = {key: value for key, value in os.environ.items() if key.upper().startswith('GIT_')}
    assert asyncio.run(access.scan()) == {'files': {}, 'complete': True}
    assert previous == {key: value for key, value in os.environ.items() if key.upper().startswith('GIT_')}


def test_git_does_not_discover_parent_repository(access):
    child = access.root / 'nested'
    child.mkdir()
    access.root = child
    assert asyncio.run(access._git_snapshot()) is None
