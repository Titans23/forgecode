'''End-to-end session lifecycle tests for M4.'''

from pathlib import Path

from forge.runtime.agent_loop import Conversation
from forge.cli import create_session_runtime
from forge.runtime.state import (
    TokenUsage,
    TurnResult,
    VerificationEvidence,
)
from forge.sessions.checkpoint import CheckpointStore
from forge.sessions.store import SessionStore
from forge.tasks.state import ActiveTask


class DummyClient:
    model = 'test-model'
    context_window = None
    max_tokens = 1_000


def completed_session(
    root: Path,
    data_root: Path,
) -> tuple[SessionStore, str]:
    store = SessionStore(root, data_root=data_root)
    journal = store.create(model='test-model', name='original')
    task = ActiveTask(id='task-123456789abc', goal='Persist M4')
    messages = [
        {'role': 'user', 'content': 'Implement M4'},
        {'role': 'assistant', 'content': 'Implemented'},
    ]
    journal.record_user_message(messages[0], task)
    journal.record_assistant_message(messages[1])
    journal.record_turn_completed(
        messages,
        task,
        TurnResult(
            text='Implemented',
            usage=TokenUsage(input_tokens=5, output_tokens=2),
            changed_paths=('forge/sessions/store.py',),
            verification=VerificationEvidence(
                command='pytest',
                cwd='.',
                exit_code=0,
                duration_seconds=1.0,
                timed_out=False,
                workspace_revision=1,
            ),
        ),
    )
    return store, journal.session_id


def conversation_for(
    store: SessionStore,
    session_id: str,
) -> Conversation:
    state, journal = store.open(session_id)
    return Conversation(
        client=DummyClient(),  # type: ignore[arg-type]
        tools=[],
        context_root=store.project_root,
        initial_messages=list(state.messages),
        active_task=state.active_task,
        session_journal=journal,
        session_store=store,
        checkpoint_store=CheckpointStore.for_session(
            store.project_root,
            journal.path,
            journal.session_id,
        ),
    )


def test_rename_branch_clear_and_resume_session(tmp_path: Path) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store, source_id = completed_session(root, tmp_path / 'data')
    conversation = conversation_for(store, source_id)

    assert conversation.session_rename('stable') == (
        'Renamed session to stable.'
    )
    assert store.load('stable').info.session_id == source_id
    assert source_id in conversation.session_status()
    history = conversation.session_history()
    assert 'Persist M4' in history
    assert 'changed=forge/sessions/store.py' in history
    assert 'verify=pytest exit=0' in history
    assert 'stable' in conversation.session_candidates()

    branch_notice = conversation.session_branch('experiment')
    branch_id = conversation.session_journal.session_id  # type: ignore[union-attr]
    assert source_id in branch_notice
    assert branch_id != source_id
    assert store.load(branch_id).messages == store.load(source_id).messages
    assert store.load(branch_id).info.name == 'experiment'

    clear_notice = conversation.session_clear()
    cleared_id = conversation.session_journal.session_id  # type: ignore[union-attr]
    assert branch_id in clear_notice
    assert cleared_id not in {source_id, branch_id}
    assert conversation.messages == []

    resume_notice = conversation.session_resume(source_id)
    assert source_id in resume_notice
    assert conversation.messages == list(store.load(source_id).messages)


def test_rewind_restores_conversation_and_code(tmp_path: Path) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store, session_id = completed_session(root, tmp_path / 'data')
    conversation = conversation_for(store, session_id)
    checkpoint_store = conversation.checkpoint_store
    journal = conversation.session_journal
    assert checkpoint_store is not None
    assert journal is not None
    target = root / 'app.py'
    target.write_text('before', encoding='utf-8')
    before_messages = list(conversation.messages)
    checkpoint_id = checkpoint_store.begin()
    journal.record_checkpoint_created(
        checkpoint_id,
        before_messages,
        conversation.task_manager.active,
    )
    checkpoint_store.capture_before(checkpoint_id, ('app.py',))
    target.write_text('after', encoding='utf-8')
    checkpoint_store.record_after(checkpoint_id, ('app.py',))
    conversation.messages.extend(
        [
            {'role': 'user', 'content': 'A later direction'},
            {'role': 'assistant', 'content': 'A later result'},
        ]
    )

    notice = conversation.checkpoint_rewind(
        checkpoint_id,
        mode='both',
    )

    assert 'restored 1 file(s)' in notice
    assert target.read_text(encoding='utf-8') == 'before'
    assert conversation.messages == before_messages
    assert list(store.load(session_id).messages) == before_messages


def test_cli_runtime_restores_recorded_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    data_root = tmp_path / 'data'
    monkeypatch.setenv('FORGE_DATA_DIR', str(data_root))
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setenv('MODEL_ID', 'current-model')
    journal = SessionStore(root, data_root=data_root).create(
        model='recorded-model'
    )

    conversation, resumed, state = create_session_runtime(
        root,
        resume_identifier=journal.session_id,
    )

    assert conversation.client.model == 'recorded-model'
    assert resumed.session_id == journal.session_id
    assert state is not None


def test_in_process_resume_switches_model_and_context(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store, source_id = completed_session(root, tmp_path / 'data')
    conversation = conversation_for(store, source_id)
    target = store.create(model='alternate-model', name='alternate')
    target.record_user_message(
        {'role': 'user', 'content': 'Alternate context'},
        None,
    )

    conversation.session_resume(target.session_id)

    assert conversation.client.model == 'alternate-model'
    assert conversation.messages == [
        {'role': 'user', 'content': 'Alternate context'}
    ]
