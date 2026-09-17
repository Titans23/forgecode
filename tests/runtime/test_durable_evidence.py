import asyncio
from dataclasses import asdict, replace

import pytest

from forge.runtime.agent_loop import Conversation
from forge.runtime.check_contracts import inherit_check_arguments
from forge.runtime.completion import CompletionGate, TaskPolicy
from forge.runtime.runner import TurnRunner
from forge.runtime.state import VerificationEvidence
from forge.runtime.workspace import WorkspaceTracker
from forge.sessions.store import SessionStore
from forge.tasks.manager import TaskManager


def observation(task_id=''):
    return VerificationEvidence('python check.py', '.', 0, .1, False, 0,
        verification_id='check-original', task_id=task_id,
        output_checks=({'key': 'value', 'operator': 'eq', 'expected': 42,
                        'requirement': 'produce value 42',
                        'requirement_id': 'req-1', 'expected_source': 'user requirement'},))


def test_recovery_keeps_run_identity_without_completed_turn(tmp_path):
    store = SessionStore(tmp_path)
    journal = store.create(model='offline')
    task = TaskManager(tmp_path).start('produce value 42')
    journal.record_task_state(task)
    journal.record_turn_started(task.goal, task)
    original = observation(task.id)
    journal.append('verification_recorded', {'evidence': asdict(original)})
    recovered = store.load(journal.session_id)
    assert len(recovered.verification_history) == 1
    old = recovered.verification_history[0]
    assert old.verification_id == original.verification_id
    assert old.success  # Historical execution fact, not a current proof.
    assert old.freshness == 'unknown'
    assert old.task_id == task.id
    inherited = inherit_check_arguments(dict(command='python fixed.py',
        inherit_checks_from=[old.verification_id], revision_reason='repair'), [old])
    assert inherited['output_checks'][0]['expected'] == 42


def test_continuation_preserves_only_active_task_evidence(tmp_path):
    async def run():
        c = Conversation(client=object(), context_root=tmp_path, task_relation='active')
        task = c.task_manager.start('produce value 42')
        c.verification_history = [observation(task.id), observation('unrelated')]
        runner = TurnRunner(c)
        await runner._prepare_turn('continue')
        assert [e.task_id for e in runner.state.evidence.verification] == [task.id]
        assert runner.state.evidence.verification[0].freshness == 'unknown'
        assert not runner.state.evidence.current(workspace_revision=0, environment_epoch=0)
    asyncio.run(run())


def test_replayed_revision_zero_does_not_satisfy_current_gate(tmp_path):
    async def run():
        tracker = WorkspaceTracker(tmp_path)
        await tracker.begin_turn()
        old = replace(observation(), freshness='unknown')
        decision = await CompletionGate(tmp_path, TaskPolicy(require_verification=True)).evaluate(
            tracker, old, verification_history=(old,), mutation_attempted=False)
        assert not decision.allowed
        assert any('not been verified' in reason for reason in decision.reasons)
    asyncio.run(run())


def test_os_lock_prevents_two_concurrent_append_owners(tmp_path):
    from forge.sessions.locking import exclusive_append
    path = tmp_path / 'session.jsonl'
    with exclusive_append(path):
        with pytest.raises(OSError):
            with exclusive_append(path):
                pytest.fail('second writer acquired the lock')
    with exclusive_append(path):
        pass  # A failed contender does not leave a stale owned lock.


def test_stored_check_reruns_after_restore_without_copying_assertions(tmp_path):
    import sys
    from forge.tools.verify import VerifyTool, VerifyInput
    from forge.runtime.agent_loop import verification_from_result
    from forge.runtime.check_contracts import resolve_stored_check
    async def run():
        tracker = WorkspaceTracker(tmp_path)
        await tracker.begin_turn()
        tool = VerifyTool(tmp_path, tracker)
        original = await tool.execute(VerifyInput(command=f'"{sys.executable}" -',
            stdin='print("observed")'))
        evidence = verification_from_result(original)
        restored = VerificationEvidence.from_dict(asdict(evidence))
        arguments = resolve_stored_check({'check_id': restored.check_id, 'timeout_seconds': 60}, [restored])
        repeated = await tool.execute(VerifyInput.model_validate(arguments))
        assert repeated.metadata['check_id'] == evidence.check_id
        assert repeated.metadata['verification_id'] != evidence.verification_id
        with pytest.raises(ValueError, match='immutable'):
            resolve_stored_check({'check_id': restored.check_id, 'stdin': 'print("different")'}, [restored])
    asyncio.run(run())


def test_legacy_session_is_read_only_and_can_fork_without_rewriting_it(tmp_path):
    import json
    from forge.sessions.store import SessionError
    store = SessionStore(tmp_path, data_root=tmp_path / 'data')
    journal = store.create(model='offline')
    journal.record_user_message({'role': 'user', 'content': 'original'}, None)
    records = [json.loads(line) for line in journal.path.read_text().splitlines()]
    for record in records:
        record['schema_version'] = 1
    journal.path.write_text('\n'.join(json.dumps(record) for record in records) + '\n')
    original = journal.path.read_bytes()
    state, readonly = store.open(journal.session_id)
    with pytest.raises(SessionError, match='read-only'):
        readonly.record_resumed()
    fork = store.fork(state, messages=list(state.messages), task=None, model='offline')
    fork.record_resumed()
    assert store.load(fork.session_id).messages == state.messages
    assert journal.path.read_bytes() == original
