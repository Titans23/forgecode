'''Tests for M2 workspace tracking and deterministic completion checks.'''

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from forge.runtime.agent_loop import mutation_target_paths
from forge.runtime.completion import (
    CompletionGate,
    TaskPolicy,
    matches_any,
)
from forge.runtime.state import ToolCall, VerificationEvidence
from forge.runtime.workspace import WorkspaceTracker
from forge.tools.filesystem import CreateDirectoryTool, RemoveDirectoryTool


def test_workspace_tracker_imports_in_fresh_process() -> None:
    result = subprocess.run(
        [
            sys.executable,
            '-c',
            (
                'from forge.runtime.workspace import WorkspaceTracker; '
                'print(WorkspaceTracker.__name__)'
            ),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'WorkspaceTracker'


def test_workspace_tracker_falls_back_when_git_is_missing(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    sample = tmp_path / 'sample.txt'
    sample.write_text('old\n', encoding='utf-8')
    tracker = WorkspaceTracker(tmp_path)
    monkeypatch.setenv('PATH', str(tmp_path / 'missing-bin'))

    run(tracker.begin_turn())
    sample.write_text('new content\n', encoding='utf-8')
    change = run(tracker.refresh())
    decision = run(
        CompletionGate(tmp_path).evaluate(
            tracker,
            None,
            mutation_attempted=True,
        )
    )

    assert tracker.available is True
    assert tracker.git_available is False
    assert change is not None
    assert change.paths == ('sample.txt',)
    assert tracker.changed_paths == ('sample.txt',)
    assert decision.allowed is True


def test_filesystem_fallback_detects_same_size_edit_with_same_timestamp(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    sample = tmp_path / 'sample.txt'
    sample.write_text('old\n', encoding='utf-8')
    tracker = WorkspaceTracker(tmp_path)
    monkeypatch.setenv('PATH', str(tmp_path / 'missing-bin'))

    run(tracker.begin_turn())
    timestamp = sample.stat().st_mtime_ns
    sample.write_text('new\n', encoding='utf-8')
    os.utime(sample, ns=(timestamp, timestamp))

    change = run(tracker.refresh())

    assert change is not None
    assert change.paths == ('sample.txt',)


def run(coroutine: object) -> Any:
    return asyncio.run(coroutine)  # type: ignore[arg-type]


def initialize_git_repository(root: Path) -> None:
    subprocess.run(['git', 'init', '--quiet'], cwd=root, check=True)
    subprocess.run(
        ['git', 'config', 'user.email', 'forge@example.test'],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ['git', 'config', 'user.name', 'ForgeCode Tests'],
        cwd=root,
        check=True,
    )
    (root / 'sample.txt').write_text('old\n', encoding='utf-8')
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'baseline'],
        cwd=root,
        check=True,
    )


def test_empty_directory_tree_cleanup_is_visible_to_workspace_tracker(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    play = tmp_path / 'play'
    (play / 'src' / 'modules').mkdir(parents=True)
    (play / '.tmp').mkdir()
    tracker = WorkspaceTracker(tmp_path)
    run(tracker.begin_turn())
    tracker.watch_paths(('play',))

    result = run(
        RemoveDirectoryTool(tmp_path).run(
            {
                'path': 'play',
                'recursive': True,
                'contents_only': True,
            }
        )
    )
    change = run(tracker.refresh())
    decision = run(
        CompletionGate(tmp_path).evaluate(
            tracker,
            None,
            mutation_attempted=True,
        )
    )

    assert result.success is True
    assert change is not None
    assert change.paths == ('play',)
    assert tracker.changed_paths == ('play',)
    assert play.is_dir()
    assert list(play.iterdir()) == []
    assert decision.allowed is True


def test_create_directory_is_visible_to_completion_gate(tmp_path: Path) -> None:
    initialize_git_repository(tmp_path)
    tracker = WorkspaceTracker(tmp_path)
    run(tracker.begin_turn())
    call = ToolCall(
        0,
        'create-play',
        'create_directory',
        {'path': 'play'},
    )

    targets = mutation_target_paths(call, maximum=None)
    tracker.watch_paths(targets)
    result = run(CreateDirectoryTool(tmp_path).run(call.arguments))
    change = run(tracker.refresh())
    decision = run(
        CompletionGate(tmp_path).evaluate(
            tracker,
            None,
            mutation_attempted=True,
        )
    )

    assert result.success is True
    assert targets == ('play',)
    assert change is not None
    assert change.paths == ('play',)
    assert tracker.changed_paths == ('play',)
    assert decision.allowed is True


def test_workspace_tracker_records_absolute_external_changes(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'repository'
    root.mkdir()
    initialize_git_repository(root)
    external = tmp_path / 'external.txt'
    tracker = WorkspaceTracker(root)
    run(tracker.begin_turn())
    tracker.watch_paths((str(external.resolve()),))

    external.write_text('outside change\n', encoding='utf-8')
    change = run(tracker.refresh())
    decision = run(
        CompletionGate(root).evaluate(
            tracker,
            None,
            mutation_attempted=True,
        )
    )

    shown_path = external.resolve().as_posix()
    assert change is not None
    assert change.paths == (shown_path,)
    assert tracker.changed_paths == (shown_path,)
    assert decision.allowed is True


def test_workspace_tracker_preserves_preexisting_user_changes(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    (tmp_path / 'user.txt').write_text('user edit\n', encoding='utf-8')
    tracker = WorkspaceTracker(tmp_path)

    run(tracker.begin_turn())
    (tmp_path / 'sample.txt').write_text('agent edit\n', encoding='utf-8')
    change = run(tracker.refresh())

    assert change is not None
    assert change.revision == 1
    assert change.paths == ('sample.txt',)
    assert tracker.changed_paths == ('sample.txt',)


def test_workspace_tracker_carries_persisted_task_change_across_turns(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    (tmp_path / 'sample.txt').write_text(
        'earlier task edit\n',
        encoding='utf-8',
    )
    tracker = WorkspaceTracker(tmp_path)

    run(tracker.begin_turn())
    assert tracker.changed_paths == ()

    tracker.carry_existing_changes(('sample.txt',))

    assert tracker.changed_paths == ('sample.txt',)


def test_workspace_tracker_only_carries_still_dirty_persisted_paths(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    (tmp_path / 'sample.txt').write_text(
        'earlier task edit\n',
        encoding='utf-8',
    )
    tracker = WorkspaceTracker(tmp_path)

    run(tracker.begin_turn())
    tracker.carry_existing_changes(('user.txt', 'missing.txt'))
    assert tracker.changed_paths == ()

    (tmp_path / 'sample.txt').write_text('old\n', encoding='utf-8')
    clean_tracker = WorkspaceTracker(tmp_path)
    run(clean_tracker.begin_turn())
    clean_tracker.carry_existing_changes(('sample.txt',))

    assert clean_tracker.changed_paths == ()


def test_workspace_tracker_excludes_generated_runtime_state(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    tracker = WorkspaceTracker(tmp_path)
    run(tracker.begin_turn())
    tasks = tmp_path / '.forge' / 'tasks'
    tasks.mkdir(parents=True)
    (tasks / 'current.json').write_text('{}\n', encoding='utf-8')
    (tmp_path / '.forge' / 'settings.json').write_text(
        '{}\n',
        encoding='utf-8',
    )
    python_cache = tmp_path / '__pycache__'
    python_cache.mkdir()
    (python_cache / 'app.cpython-312.pyc').write_bytes(b'cache')
    pytest_cache = tmp_path / '.pytest_cache'
    pytest_cache.mkdir()
    (pytest_cache / 'README.md').write_text('cache\n', encoding='utf-8')
    forge_data = tmp_path / '.forge-data' / 'projects' / 'example'
    forge_data.mkdir(parents=True)
    (forge_data / 'session.jsonl').write_text('{}\n', encoding='utf-8')
    ordinary_hidden = tmp_path / '.project-data'
    ordinary_hidden.mkdir()
    (ordinary_hidden / 'state.json').write_text('{}\n', encoding='utf-8')

    change = run(tracker.refresh())

    assert change is not None
    assert change.paths == ('.forge/settings.json', '.project-data/state.json')
    assert tracker.changed_paths == (
        '.forge/settings.json',
        '.project-data/state.json',
    )


def test_path_patterns_match_deep_source_files() -> None:
    assert matches_any('src/todo.ts', ('src/**',))
    assert matches_any('src/main/java/Order.java', ('src/main/**',))
    assert matches_any('tests/hidden/a/b.py', ('tests/hidden/**',))


def test_workspace_tracker_detects_untracked_files_and_reverts(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    tracker = WorkspaceTracker(tmp_path)
    run(tracker.begin_turn())

    generated = tmp_path / 'generated.txt'
    generated.write_text('new\n', encoding='utf-8')
    first = run(tracker.refresh())
    generated.unlink()
    second = run(tracker.refresh())

    assert first is not None and first.revision == 1
    assert first.paths == ('generated.txt',)
    assert second is not None and second.revision == 2
    assert tracker.changed_paths == ()


def test_workspace_tracker_watches_ignored_write_targets(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    (tmp_path / '.gitignore').write_text('ignored/\n', encoding='utf-8')
    subprocess.run(['git', 'add', '.gitignore'], cwd=tmp_path, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'ignore generated files'],
        cwd=tmp_path,
        check=True,
    )
    ignored = tmp_path / 'ignored'
    ignored.mkdir()
    existing = ignored / 'app.js'
    existing.write_text('old\n', encoding='utf-8')
    tracker = WorkspaceTracker(tmp_path)

    run(tracker.begin_turn())
    tracker.watch_paths(('ignored/app.js', 'ignored/new.js'))
    existing.write_text('changed\n', encoding='utf-8')
    (ignored / 'new.js').write_text('created\n', encoding='utf-8')
    change = run(tracker.refresh())
    unchanged = run(tracker.refresh())

    assert change is not None
    assert change.revision == 1
    assert change.paths == ('ignored/app.js', 'ignored/new.js')
    assert tracker.changed_paths == ('ignored/app.js', 'ignored/new.js')
    assert unchanged is None


def test_completion_gate_requires_verification_only_when_policy_requests_it(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    tracker = WorkspaceTracker(tmp_path)
    run(tracker.begin_turn())
    (tmp_path / 'sample.txt').write_text('changed\n', encoding='utf-8')
    run(tracker.refresh())
    gate = CompletionGate(
        tmp_path,
        TaskPolicy(require_verification=True),
    )

    missing = run(gate.evaluate(tracker, None, mutation_attempted=False))
    current = VerificationEvidence(
        command='pytest',
        cwd='.',
        exit_code=0,
        duration_seconds=0.1,
        timed_out=False,
        workspace_revision=1,
    )
    accepted = run(
        gate.evaluate(tracker, current, mutation_attempted=False)
    )
    (tmp_path / 'sample.txt').write_text('changed again\n', encoding='utf-8')
    run(tracker.refresh())
    stale = run(gate.evaluate(tracker, current, mutation_attempted=False))

    assert missing.allowed is False
    assert 'has not been verified' in missing.reasons[0]
    assert accepted.allowed is True
    assert stale.allowed is False
    assert any('changed after verification' in item for item in stale.reasons)


def test_completion_gate_allows_unverified_diff_by_default(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    tracker = WorkspaceTracker(tmp_path)
    run(tracker.begin_turn())
    (tmp_path / 'sample.txt').write_text('changed\n', encoding='utf-8')
    run(tracker.refresh())

    decision = run(
        CompletionGate(tmp_path).evaluate(
            tracker,
            None,
            mutation_attempted=False,
        )
    )

    assert decision.allowed is True


def test_completion_gate_blocks_current_optional_verification_failure(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    tracker = WorkspaceTracker(tmp_path)
    run(tracker.begin_turn())
    (tmp_path / 'sample.txt').write_text('changed\n', encoding='utf-8')
    run(tracker.refresh())
    failed = VerificationEvidence(
        command='pytest',
        cwd='.',
        exit_code=1,
        duration_seconds=0.1,
        timed_out=False,
        workspace_revision=1,
    )

    decision = run(
        CompletionGate(tmp_path).evaluate(
            tracker,
            failed,
            mutation_attempted=False,
        )
    )

    assert decision.allowed is False
    assert 'latest verification failed' in decision.reasons[0]


def test_completion_gate_blocks_stale_optional_verification_failure(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    tracker = WorkspaceTracker(tmp_path)
    run(tracker.begin_turn())
    (tmp_path / 'sample.txt').write_text('revision one\n', encoding='utf-8')
    run(tracker.refresh())
    failed = VerificationEvidence(
        command='pytest',
        cwd='.',
        exit_code=1,
        duration_seconds=0.1,
        timed_out=False,
        workspace_revision=1,
    )
    (tmp_path / 'sample.txt').write_text('revision two\n', encoding='utf-8')
    run(tracker.refresh())

    decision = run(
        CompletionGate(tmp_path).evaluate(
            tracker,
            failed,
            mutation_attempted=False,
        )
    )

    assert decision.allowed is False
    assert any('latest verification failed' in item for item in decision.reasons)
    assert any('run verify again' in item for item in decision.reasons)


def test_completion_gate_ignores_unrelated_preexisting_whitespace_errors(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    (tmp_path / 'user.txt').write_text(
        'preexisting user edit with trailing spaces  \n',
        encoding='utf-8',
    )
    tracker = WorkspaceTracker(tmp_path)
    run(tracker.begin_turn())
    (tmp_path / 'sample.txt').write_text('agent edit\n', encoding='utf-8')
    run(tracker.refresh())
    evidence = VerificationEvidence(
        command='pytest',
        cwd='.',
        exit_code=0,
        duration_seconds=0.1,
        timed_out=False,
        workspace_revision=1,
    )

    decision = run(
        CompletionGate(tmp_path).evaluate(
            tracker,
            evidence,
            mutation_attempted=True,
        )
    )

    assert tracker.changed_paths == ('sample.txt',)
    assert decision.allowed is True


def test_completion_gate_checks_task_local_change_to_untracked_file(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    untracked = tmp_path / 'play' / 'world.js'
    untracked.parent.mkdir()
    untracked.write_text('const face = 1;\n', encoding='utf-8')
    tracker = WorkspaceTracker(tmp_path)
    run(tracker.begin_turn())
    untracked.write_text('const face = 6;  \n', encoding='utf-8')
    run(tracker.refresh())
    evidence = VerificationEvidence(
        command='git diff --check',
        cwd='.',
        exit_code=0,
        duration_seconds=0.1,
        timed_out=False,
        workspace_revision=1,
    )

    decision = run(
        CompletionGate(tmp_path).evaluate(
            tracker,
            evidence,
            mutation_attempted=True,
        )
    )

    assert tracker.changed_paths == ('play/world.js',)
    assert decision.allowed is False
    assert any(
        'untracked file: play/world.js' in reason
        for reason in decision.reasons
    )


def test_completion_gate_rejects_failed_verification_and_empty_diff(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    tracker = WorkspaceTracker(tmp_path)
    run(tracker.begin_turn())
    failed = VerificationEvidence(
        command='pytest',
        cwd='.',
        exit_code=1,
        duration_seconds=0.1,
        timed_out=False,
        workspace_revision=0,
    )
    gate = CompletionGate(
        tmp_path,
        TaskPolicy(require_changes=True, require_verification=True),
    )

    decision = run(
        gate.evaluate(tracker, failed, mutation_attempted=False)
    )

    assert decision.allowed is False
    assert any('final Diff is empty' in item for item in decision.reasons)
    assert any('verification failed' in item for item in decision.reasons)


def test_completion_gate_rejects_forbidden_and_out_of_scope_paths(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    hidden = tmp_path / 'tests' / 'hidden'
    hidden.mkdir(parents=True)
    (hidden / 'test_secret.py').write_text('old\n', encoding='utf-8')
    subprocess.run(['git', 'add', '.'], cwd=tmp_path, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'hidden baseline'],
        cwd=tmp_path,
        check=True,
    )
    tracker = WorkspaceTracker(tmp_path)
    run(tracker.begin_turn())
    (hidden / 'test_secret.py').write_text('disabled\n', encoding='utf-8')
    (tmp_path / 'user.txt').write_text('outside\n', encoding='utf-8')
    run(tracker.refresh())
    evidence = VerificationEvidence(
        command='pytest',
        cwd='.',
        exit_code=0,
        duration_seconds=0.1,
        timed_out=False,
        workspace_revision=1,
    )
    gate = CompletionGate(
        tmp_path,
        TaskPolicy(allowed_paths=('sample.txt',)),
    )

    decision = run(
        gate.evaluate(tracker, evidence, mutation_attempted=False)
    )

    assert decision.allowed is False
    assert any('Forbidden paths' in item for item in decision.reasons)
    assert any('outside the allowed scope' in item for item in decision.reasons)
