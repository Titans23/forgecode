"""Completion evidence transitions only; real native proof uses repository sources."""
import asyncio
from hashlib import sha256
import pytest

from forge.runtime.completion import CompletionGate, TaskPolicy
from forge.runtime.workspace import WorkspaceTracker


class Observer:
    def __init__(self, root, *, complete):
        self.root, self.complete = root, complete
        self.snapshot_complete = True
        self.scans = 0

    def value(self, path):
        target = self.root / path
        return {'exists': target.exists(), 'is_file': target.is_file(),
                'sha256': sha256(target.read_bytes()).hexdigest() if target.is_file() else None,
                'file_identity': path if target.exists() else None}

    async def scan(self):
        self.scans += 1
        return {'complete': self.complete, 'git_available': True,
                'files': {path.name: self.value(path.name) for path in self.root.iterdir()}}

    async def snapshot(self, paths):
        return {'complete': self.snapshot_complete, 'files': {path: self.value(path) for path in paths}}


def files(root):
    (root / 'target.py').write_text('old\n')
    (root / 'user.py').write_text('preexisting user edit\n')


def test_confirmed_target_change_survives_incomplete_scan_without_claiming_complete(tmp_path):
    files(tmp_path)
    observer = Observer(tmp_path, complete=False)
    tracker = WorkspaceTracker(tmp_path, observer=observer)
    async def run():
        await tracker.begin_turn()
        assert not tracker.git_available  # Native observations must not enable host diff subprocesses.
        await tracker.watch_paths_async(('target.py',))
        (tmp_path / 'target.py').write_text('new\n')
        assert (await tracker.refresh_paths(('target.py',))).paths == ('target.py',)
        assert not tracker.available and tracker.changed_paths == ('target.py',)
        decision = CompletionGate(tmp_path, policy=TaskPolicy(require_changes=True)).evaluate(
            tracker, None, mutation_attempted=True)
        decision = await decision
        assert not decision.allowed
        assert any('unavailable' in reason for reason in decision.reasons)
        assert not any('Diff is empty' in reason for reason in decision.reasons)
        observer.complete = True
        (tmp_path / 'user.py').write_text('later user edit\n')
        await tracker.refresh()
        assert not tracker.available and tracker.changed_paths == ('target.py',)
        assert observer.scans == 1
        (tmp_path / 'target.py').write_text('changed by a later command\n')
        assert (await tracker.refresh()).paths == ('target.py',)
        assert observer.scans == 1
    asyncio.run(run())


def test_targeted_refresh_preserves_full_baseline_and_does_not_rehabilitate_unknown_commands(tmp_path):
    files(tmp_path)
    observer = Observer(tmp_path, complete=True)
    tracker = WorkspaceTracker(tmp_path, observer=observer)
    async def run():
        await tracker.begin_turn()
        await tracker.watch_paths_async(('target.py',))
        observer.complete = False
        (tmp_path / 'target.py').write_text('new\n')
        await tracker.refresh_paths(('target.py',))
        assert tracker.available and tracker.changed_paths == ('target.py',) and observer.scans == 1
        await tracker.refresh()  # An arbitrary command may touch unobserved paths.
        assert not tracker.available
        (tmp_path / 'target.py').write_text('newer\n')
        await tracker.refresh_paths(('target.py',))
        assert not tracker.available and tracker.changed_paths == ('target.py',)
        observer.complete = True
        await tracker.refresh()
        assert tracker.available and tracker.changed_paths == ('target.py',)
        assert (tmp_path / 'user.py').read_text() == 'preexisting user edit\n'
    asyncio.run(run())


def test_incomplete_target_snapshot_and_unwatched_paths_never_authorize_completion(tmp_path):
    files(tmp_path)
    observer = Observer(tmp_path, complete=True)
    tracker = WorkspaceTracker(tmp_path, observer=observer)
    async def run():
        await tracker.begin_turn()
        observer.snapshot_complete = False
        assert await tracker.refresh_paths(('target.py',)) is None
        assert not tracker.available
        observer.snapshot_complete = True
        await tracker.refresh_paths(('user.py',))
        assert not tracker.available and tracker.changed_paths == ()
    asyncio.run(run())


@pytest.mark.parametrize('strict', [False, True])
def test_confirmed_delivery_distinguishes_normal_task_from_explicit_acceptance(tmp_path, strict):
    from forge.runtime.agent_loop import Conversation
    from forge.runtime.state import ModelTextDelta, ModelToolCallCompleted, ModelUsageUpdate, TokenUsage, ToolCall, ToolExecutionCompleted
    from forge.permissions.policy import PermissionManager
    from forge.sessions.store import SessionStore
    from forge.sessions.trajectory import journal_events
    from forge.tools import create_default_registry
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'user.py').write_text('preexisting user edit\n')
    registry = create_default_registry(root)
    registry.workspace_tracker.observer = Observer(root, complete=False)
    class Client:
        calls = 0
        async def stream(self, **kwargs):
            self.calls += 1
            yield ModelUsageUpdate(TokenUsage(10, 2))
            if self.calls == 1:
                yield ModelToolCallCompleted(ToolCall(0, 'edit', 'write_file', {'path': 'target.py', 'content': 'new'}))
            else:
                yield ModelTextDelta('Created the requested file; no tests were requested or run.')
    store = SessionStore(root, data_root=tmp_path / 'sessions')
    journal = store.create(model='fixture')
    conversation = Conversation(client=Client(), registry=registry, context_root=root, session_journal=journal,
        permission_manager=PermissionManager(root, mode='auto', load_stored_rules=False),
        task_policy=TaskPolicy(require_changes=True) if strict else None)
    async def run():
        events = [event async for event in conversation.stream('Create target.py. Do not run tests.')]
        assert next(event.result for event in events if isinstance(event, ToolExecutionCompleted)).success
        result = events[-1].result
        assert result.status == ('partial' if strict else 'completed')
        assert result.changed_paths == ('target.py',)
        assert result.completion_report.workspace_tracking_complete is False
        assert result.completion_report.verification_status == 'unverified'
        assert result.completion_report.acceptance_status == ('unmet' if strict else 'not_configured')
        assert any('unavailable' in reason for reason in result.completion_reasons) == strict
        assert not registry.workspace_tracker.available
        assert (root / 'target.py').read_text() == 'new'
        assert (root / 'user.py').read_text() == 'preexisting user edit\n'
        completed = [row for row in journal_events(journal.path) if row['type'] == 'turn_completed'][-1]
        assert completed['payload']['completion_report']['workspace_tracking_complete'] is False
    asyncio.run(run())


def test_no_confirmed_change_still_cannot_satisfy_mutation_claim(tmp_path):
    files(tmp_path)
    tracker = WorkspaceTracker(tmp_path, observer=Observer(tmp_path, complete=False))
    async def run():
        await tracker.begin_turn()
        decision = await CompletionGate(tmp_path).evaluate(tracker, None, mutation_attempted=True)
        assert not decision.allowed
    asyncio.run(run())
