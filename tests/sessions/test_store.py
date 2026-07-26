'''Tests for crash-tolerant M4 session persistence.'''

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.runtime.state import TokenUsage, ToolCall, TurnResult
from forge.sessions.store import (
    SessionCorruptError,
    SessionStore,
)
from forge.tasks.state import ActiveTask


def test_session_round_trip_restores_messages_and_task(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = SessionStore(root, data_root=tmp_path / 'data')
    journal = store.create(model='test-model', name='m4')
    task = ActiveTask(id='task-123456789abc', goal='Implement M4')
    user = {'role': 'user', 'content': 'Start M4'}
    assistant = {'role': 'assistant', 'content': 'Done'}

    journal.record_user_message(user, task)
    journal.record_assistant_message(assistant)
    journal.record_turn_completed(
        [user, assistant],
        task,
        TurnResult(
            text='Done',
            usage=TokenUsage(input_tokens=10, output_tokens=2),
        ),
    )

    state, reopened = store.open('m4')

    assert list(state.messages) == [user, assistant]
    assert state.active_task == task
    assert state.info.model == 'test-model'
    assert state.info.title == 'm4'
    assert state.info.status == 'completed'
    assert reopened.sequence == state.info.sequence


def test_replay_preserves_completed_turn_paths_for_same_legacy_task(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = SessionStore(root, data_root=tmp_path / 'data')
    journal = store.create(model='test-model')
    task = ActiveTask(
        id='task-resume000001',
        goal='继续修改 play 游戏',
        status='stuck',
        requires_change=True,
        scope_hints=('play/**',),
        scope_source='explicit',
    )
    journal.record_turn_completed(
        [],
        task,
        TurnResult(
            text='stuck',
            usage=TokenUsage(input_tokens=0, output_tokens=0),
            status='stuck',
            changed_paths=(
                'play/src/main.js',
                'play/.touch',
                'play/.tmp/probe.txt',
                'outside.txt',
            ),
        ),
    )
    journal.append(
        'task_state',
        {
            'task': {
                'id': task.id,
                'goal': task.goal,
                'status': 'in_progress',
                'requires_change': True,
                'scope_hints': ['play/**'],
            }
        },
    )

    restored = store.latest().active_task

    assert restored is not None
    assert restored.workspace_paths == ('play/src/main.js',)


def test_legacy_anaphoric_task_inherits_previous_scope_on_replay(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = SessionStore(root, data_root=tmp_path / 'data')
    journal = store.create(model='test-model')
    previous = ActiveTask(
        id='task-previous0001',
        goal='查看 play 目录',
        scope_hints=('play/**',),
        scope_source='explicit',
    )
    journal.record_task_state(previous)
    legacy = {
        'id': 'task-legacy000001',
        'goal': '帮我将其工程化，并升级为复杂游戏',
        'status': 'stuck',
        'requires_change': True,
        'planned': False,
        'current_step_id': None,
        'steps': [],
        'constraints': [],
        'scope_hints': [],
        'blocked_reasons': ['legacy failure'],
    }
    journal.append('task_state', {'task': legacy})
    legacy['status'] = 'in_progress'
    legacy['blocked_reasons'] = []
    journal.append('task_state', {'task': legacy})

    restored = store.latest().active_task

    assert restored is not None
    assert restored.scope_hints == ('play/**',)
    assert restored.scope_source == 'inherited'


def test_legacy_unscoped_repository_task_does_not_inherit_scope(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = SessionStore(root, data_root=tmp_path / 'data')
    journal = store.create(model='test-model')
    journal.record_task_state(
        ActiveTask(
            id='task-previous0001',
            goal='查看 play 目录',
            scope_hints=('play/**',),
            scope_source='explicit',
        )
    )
    journal.append(
        'task_state',
        {
            'task': {
                'id': 'task-repository01',
                'goal': '重构整个仓库',
                'scope_hints': [],
            }
        },
    )

    restored = store.latest().active_task

    assert restored is not None
    assert restored.scope_hints == ()
    assert restored.scope_source == 'repository'


def test_unnamed_session_uses_first_prompt_as_title(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = SessionStore(root, data_root=tmp_path / 'data')
    journal = store.create(model='test-model')
    journal.record_user_message(
        {'role': 'user', 'content': '修复登录状态恢复问题'},
        None,
    )

    assert store.latest().info.title == '修复登录状态恢复问题'


def test_incomplete_tool_pair_is_not_restored_or_replayed(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = SessionStore(root, data_root=tmp_path / 'data')
    journal = store.create(model='test-model')
    user = {'role': 'user', 'content': 'Edit the file'}
    call = ToolCall(
        index=0,
        id='toolu_write',
        name='write_file',
        arguments={'path': 'a.py', 'content': 'x = 1'},
    )
    assistant = {
        'role': 'assistant',
        'content': [
            {
                'type': 'tool_use',
                'id': call.id,
                'name': call.name,
                'input': call.arguments,
            }
        ],
    }
    journal.record_user_message(user, None)
    journal.record_assistant_message(assistant)
    journal.record_tool_started(call.id, call.name, call.arguments)

    state = store.latest()

    assert list(state.messages) == [user]
    assert state.indeterminate_tools == (
        {
            'tool_call_id': 'toolu_write',
            'name': 'write_file',
            'arguments': {'path': 'a.py', 'content': 'x = 1'},
        },
    )


def test_complete_tool_pair_is_restored_atomically(tmp_path: Path) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = SessionStore(root, data_root=tmp_path / 'data')
    journal = store.create(model='test-model')
    user = {'role': 'user', 'content': 'Read README'}
    assistant = {
        'role': 'assistant',
        'content': [
            {
                'type': 'tool_use',
                'id': 'toolu_read',
                'name': 'read_file',
                'input': {'path': 'README.md'},
            }
        ],
    }
    result = {
        'role': 'user',
        'content': [
            {
                'type': 'tool_result',
                'tool_use_id': 'toolu_read',
                'content': 'README',
                'is_error': False,
            }
        ],
    }
    journal.record_user_message(user, None)
    journal.record_assistant_message(assistant)
    journal.record_tool_started(
        'toolu_read',
        'read_file',
        {'path': 'README.md'},
    )
    journal.record_tool_completed('toolu_read', 'read_file', True)
    journal.record_tool_result_message(result, None)

    state = store.latest()

    assert list(state.messages) == [user, assistant, result]
    assert state.indeterminate_tools == ()


def test_truncated_last_line_is_ignored(tmp_path: Path) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = SessionStore(root, data_root=tmp_path / 'data')
    journal = store.create(model='test-model')
    journal.record_user_message(
        {'role': 'user', 'content': 'Persist me'},
        None,
    )
    with journal.path.open('ab') as file:
        file.write(b'{incomplete:')

    state = store.latest()

    assert state.messages == (
        {'role': 'user', 'content': 'Persist me'},
    )


def test_corrupt_middle_event_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = SessionStore(root, data_root=tmp_path / 'data')
    journal = store.create(model='test-model')
    journal.record_user_message(
        {'role': 'user', 'content': 'one'},
        None,
    )
    journal.record_assistant_message(
        {'role': 'assistant', 'content': 'two'}
    )
    lines = journal.path.read_text(encoding='utf-8').splitlines()
    lines[1] = '{broken'
    journal.path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    with pytest.raises(SessionCorruptError):
        store._build_state(journal.path)


def test_large_payload_is_stored_as_verified_artifact(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = SessionStore(root, data_root=tmp_path / 'data')
    journal = store.create(model='test-model')
    journal.inline_payload_bytes = 32
    message = {'role': 'user', 'content': 'x' * 1_000}

    journal.record_user_message(message, None)

    records = [
        json.loads(line)
        for line in journal.path.read_text(encoding='utf-8').splitlines()
    ]
    assert 'payload_ref' in records[1]
    assert store.latest().messages == (message,)


def test_session_index_is_rebuilt_when_journal_changes(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = SessionStore(root, data_root=tmp_path / 'data')
    journal = store.create(model='test-model')

    assert store.list()[0].sequence == 1
    assert store.index_path.is_file()

    journal.record_user_message(
        {'role': 'user', 'content': 'new event'},
        None,
    )

    assert store.list()[0].sequence == 3
