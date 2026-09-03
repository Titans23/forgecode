'''Contract tests for the unified runtime execution boundary.'''

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from forge.context.manager import ContextManager
from forge.hooks import HookEvent, HookOutcome
from forge.permissions.policy import PermissionManager
from forge.runtime.agent_loop import verification_from_result
from forge.runtime.completion import CompletionGate, TaskPolicy
from forge.runtime.executor import ToolExecutor
from forge.runtime.state import ToolCall
from forge.runtime.turn_state import TurnState
from forge.runtime.workspace import WorkspaceTracker
from forge.sessions.checkpoint import CheckpointStore
from forge.sessions.store import SessionStore
from forge.tools import create_default_registry


def run(coroutine: object):
    return asyncio.run(coroutine)  # type: ignore[arg-type]


def test_tool_executor_owns_hooks_checkpoint_and_observation(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        registry = create_default_registry(tmp_path)
        tracker = registry.workspace_tracker
        assert isinstance(tracker, WorkspaceTracker)
        await tracker.begin_turn()
        permission_manager = PermissionManager(
            tmp_path,
            user_path=tmp_path / 'user-permissions.json',
        )
        checkpoint_store = CheckpointStore(
            tmp_path,
            tmp_path.parent / f'{tmp_path.name}-checkpoints',
        )
        checkpoint_id = checkpoint_store.begin()
        hook_names: list[str] = []

        async def hooks(event: HookEvent) -> HookOutcome:
            hook_names.append(event.name)
            return HookOutcome(arguments=event.arguments)

        executor = ToolExecutor(
            registry,
            permission_manager,
            workspace_tracker=tracker,
            hook_runner=hooks,
            checkpoint_store=checkpoint_store,
        )
        call = ToolCall(
            0,
            'write-boundary',
            'write_file',
            {'path': 'result.txt', 'content': 'new\n'},
        )

        outcome = await executor.execute(
            call,
            checkpoint_id=checkpoint_id,
        )

        assert outcome.result.success is True
        assert outcome.record.status == 'executed'
        assert outcome.workspace_change is not None
        assert outcome.workspace_change.paths == ('result.txt',)
        assert hook_names == [
            'PreToolUse',
            'BeforeFileEdit',
            'AfterFileEdit',
            'PostToolUse',
        ]
        assert (tmp_path / 'result.txt').read_text(encoding='utf-8') == 'new\n'

        checkpoint_store.restore(checkpoint_id)
        assert not (tmp_path / 'result.txt').exists()

    run(exercise())


def test_process_and_verify_are_never_repeat_cached(tmp_path: Path) -> None:
    async def exercise() -> None:
        registry = create_default_registry(tmp_path)
        tracker = registry.workspace_tracker
        assert isinstance(tracker, WorkspaceTracker)
        await tracker.begin_turn()
        executor = ToolExecutor(
            registry,
            PermissionManager(
                tmp_path,
                user_path=tmp_path / 'user-permissions.json',
            ),
            workspace_tracker=tracker,
        )
        command = subprocess.list2cmdline(
            [sys.executable, '-c', 'print("ok")']
        )
        first = await executor.execute(
            ToolCall(0, 'command-1', 'run_command', {'command': command})
        )
        second = await executor.execute(
            ToolCall(1, 'command-2', 'run_command', {'command': command})
        )

        assert first.record.status == 'executed'
        assert second.record.status == 'executed'
        assert first.result.metadata.get('cache_hit') is None
        assert second.result.metadata.get('cache_hit') is None
        assert tracker.environment_epoch == 2

        verify_first = await executor.execute(
            ToolCall(2, 'verify-1', 'verify', {'command': command})
        )
        verify_second = await executor.execute(
            ToolCall(3, 'verify-2', 'verify', {'command': command})
        )
        assert verify_first.record.status == 'executed'
        assert verify_second.record.status == 'executed'
        assert verify_first.result.metadata.get('cache_hit') is None
        assert verify_second.result.metadata.get('cache_hit') is None
        assert tracker.environment_epoch == 4

    run(exercise())


def test_turn_state_keeps_execution_status_counts_and_budgets() -> None:
    state = TurnState(
        goal='test the boundary',
        max_model_calls=1,
        max_tool_calls=2,
    )
    assert state.can_request_model() is True
    assert state.can_request_tool_batch(2) is True
    state.record_model_request()
    state.record_tool_request()
    state.record_tool_request()
    assert state.can_request_model() is False
    assert state.can_request_tool_batch(1) is False
    assert state.statistics()['model_requests'] == 1
    assert state.statistics()['tool_requests'] == 2


def test_resumed_session_deduplicates_tool_lifecycle_events(tmp_path: Path) -> None:
    store = SessionStore(tmp_path, data_root=tmp_path / 'session-data')
    journal = store.create(model='test-model')
    journal.record_tool_started('tool-1', 'read_file', {'path': 'a.txt'})
    journal.record_tool_started('tool-1', 'read_file', {'path': 'a.txt'})
    journal.record_tool_completed('tool-1', 'read_file', True)
    journal.record_tool_completed('tool-1', 'read_file', True)

    _, resumed = store.open(journal.session_id)
    resumed.record_tool_started('tool-1', 'read_file', {'path': 'a.txt'})
    resumed.record_tool_completed('tool-1', 'read_file', True)

    history = store.history(journal.session_id)
    assert sum(item['summary'] == 'read_file (tool-1)' for item in history) == 1
    assert sum(item['summary'] == 'read_file success=True' for item in history) == 1


def test_resumed_session_exposes_unresolved_side_effects_without_replay(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path, data_root=tmp_path / 'session-data')
    journal = store.create(model='test-model')
    journal.record_tool_started(
        'tool-interrupted',
        'run_command',
        {'command': 'python -c "do_work()"'},
    )
    journal.record_tool_completed(
        'tool-interrupted',
        'run_command',
        False,
        status='indeterminate',
        error_code='execution_cancelled',
    )
    journal.record_tool_started(
        'tool-open',
        'run_command',
        {'command': 'python -c "maybe_work()"'},
    )

    state, _ = store.open(journal.session_id)

    assert [item['tool_call_id'] for item in state.indeterminate_tools] == [
        'tool-open',
        'tool-interrupted',
    ]
    assert all(
        item['status'] == 'indeterminate'
        and item['resolution_required'] is True
        for item in state.indeterminate_tools
    )


def test_large_tool_results_use_bounded_artifact_reads(tmp_path: Path) -> None:
    manager = ContextManager([], tmp_path)
    original = 'head\n' + ('payload\n' * 5_000) + 'tail\n'
    message = {
        'role': 'user',
        'content': [
            {
                'type': 'tool_result',
                'tool_use_id': 'large-output',
                'content': original,
            }
        ],
    }

    artifacts = manager.persist_tool_result_message(message)

    digest = hashlib.sha256(original.encode('utf-8')).hexdigest()
    assert artifacts == (
        str((tmp_path / '.forge' / 'context' / 'tool-results' / f'{digest}.txt').as_posix()),
    )
    rendered = str(message['content'][0]['content'])
    assert digest in rendered
    assert manager.read_artifact(
        digest,
        max_characters=len(original) + 1,
    ) == original
    bounded = manager.read_artifact(digest, max_characters=100)
    assert bounded.startswith('head\n')
    assert bounded.endswith('tail\n')
    with pytest.raises(ValueError):
        manager.read_artifact('../secret')


def test_verification_evidence_is_invalidated_by_environment_epoch(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        registry = create_default_registry(tmp_path)
        tracker = registry.workspace_tracker
        assert isinstance(tracker, WorkspaceTracker)
        await tracker.begin_turn()
        executor = ToolExecutor(
            registry,
            PermissionManager(
                tmp_path,
                user_path=tmp_path / 'user-permissions.json',
            ),
            workspace_tracker=tracker,
        )
        command = subprocess.list2cmdline(
            [sys.executable, '-c', 'print("verified")']
        )
        outcome = await executor.execute(
            ToolCall(0, 'verify-current', 'verify', {'command': command})
        )
        assert outcome.result.success is True
        assert outcome.result.metadata['environment_epoch'] == (
            tracker.environment_epoch
        )
        evidence = verification_from_result(outcome.result)
        assert evidence is not None
        gate = CompletionGate(
            tmp_path,
            TaskPolicy(require_verification=True),
        )
        accepted = await gate.evaluate(
            tracker,
            evidence,
            verification_history=(evidence,),
            mutation_attempted=False,
        )
        assert accepted.allowed is True

        tracker.mark_environment_change()
        rejected = await gate.evaluate(
            tracker,
            evidence,
            verification_history=(evidence,),
            mutation_attempted=False,
        )
        assert rejected.allowed is False
        assert any('changed after verification' in reason for reason in rejected.reasons)

    run(exercise())
