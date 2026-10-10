"""Real local Git repositories exercise the transfer; no network or model calls."""
from pathlib import Path
import subprocess

import pytest

from scripts import resume_f31


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, encoding='utf-8').strip()


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    origin = tmp_path / 'origin'
    origin.mkdir()
    subprocess.run(['git', 'init', '-b', resume_f31.BRANCH, str(origin)], check=True, capture_output=True)
    git(origin, 'config', 'user.email', 'fixture@example.invalid')
    git(origin, 'config', 'user.name', 'Fixture')
    (origin / 'task.txt').write_text('first')
    git(origin, 'add', '.')
    git(origin, 'commit', '-m', 'Initial fixture')
    monkeypatch.setattr(resume_f31, 'REMOTE', str(origin))
    return origin, tmp_path / 'checkout'


def test_new_checkout_then_fast_forward_gets_current_branch(repositories):
    origin, checkout = repositories
    first = resume_f31.update_checkout(checkout)
    assert first == git(origin, 'rev-parse', 'HEAD')
    (origin / 'task.txt').write_text('latest')
    git(origin, 'commit', '-am', 'Latest fixture')
    latest = resume_f31.update_checkout(checkout)
    assert latest != first
    assert latest == git(origin, 'rev-parse', 'HEAD')
    assert (checkout / 'task.txt').read_text() == 'latest'


@pytest.mark.parametrize('filename', ['task.txt', 'untracked.txt'])
def test_update_preserves_uncommitted_work(repositories, filename):
    origin, checkout = repositories
    before = resume_f31.update_checkout(checkout)
    (checkout / filename).write_text('user work')
    with pytest.raises(RuntimeError, match='uncommitted'):
        resume_f31.update_checkout(checkout)
    assert git(checkout, 'rev-parse', 'HEAD') == before
    assert (checkout / filename).read_text() == 'user work'


def test_update_preserves_local_commits(repositories):
    origin, checkout = repositories
    resume_f31.update_checkout(checkout)
    git(checkout, 'config', 'user.email', 'fixture@example.invalid')
    git(checkout, 'config', 'user.name', 'Fixture')
    (checkout / 'task.txt').write_text('local change')
    git(checkout, 'commit', '-am', 'Local work')
    before = git(checkout, 'rev-parse', 'HEAD')
    with pytest.raises(RuntimeError, match='local commits'):
        resume_f31.update_checkout(checkout)
    assert git(checkout, 'rev-parse', 'HEAD') == before


@pytest.mark.parametrize('change', ['remote', 'branch', 'nested'])
def test_update_refuses_wrong_checkout(repositories, change):
    origin, checkout = repositories
    resume_f31.update_checkout(checkout)
    if change == 'remote':
        git(checkout, 'remote', 'set-url', 'origin', 'https://example.invalid/unrelated.git')
    elif change == 'branch':
        git(checkout, 'checkout', '-b', 'unrelated')
    else:
        checkout = checkout / 'nested'
        checkout.mkdir()
    with pytest.raises(RuntimeError):
        resume_f31.update_checkout(checkout)


def test_codex_receives_checkout_and_task_without_configuration_override(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(resume_f31.shutil, 'which', lambda name: '/fixture/codex')
    monkeypatch.setattr(resume_f31.subprocess, 'call', lambda argv, **kwargs: calls.append((argv, kwargs)) or 0)
    assert resume_f31.start_codex(tmp_path) == 0
    argv, options = calls[0]
    assert argv[:3] == ['/fixture/codex', '-C', str(tmp_path)]
    assert argv[3] == resume_f31.PROMPT
    assert options['cwd'] == tmp_path
    assert len(argv) == 4


def test_missing_codex_leaves_checkout_available(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(resume_f31.shutil, 'which', lambda name: None)
    assert resume_f31.start_codex(tmp_path) == 2
    assert 'F31-wsl-handoff.md' in capsys.readouterr().out
