'''Tests for M2 workspace tracking and deterministic completion checks.'''

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

from forge.runtime.completion import (
    CompletionGate,
    TaskPolicy,
    matches_any,
)
from forge.runtime.state import VerificationEvidence
from forge.runtime.workspace import WorkspaceTracker


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
    (root / 'user.txt').write_text('baseline\n', encoding='utf-8')
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'baseline'],
        cwd=root,
        check=True,
    )


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


def run(coroutine: object) -> Any:
    return asyncio.run(coroutine)  # type: ignore[arg-type]


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


def test_completion_gate_requires_current_successful_verification(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    tracker = WorkspaceTracker(tmp_path)
    run(tracker.begin_turn())
    (tmp_path / 'sample.txt').write_text('changed\n', encoding='utf-8')
    run(tracker.refresh())
    gate = CompletionGate(tmp_path)

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
