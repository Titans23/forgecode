"""Durable session policy switches; file helpers only, no model calls or ACL setup."""
import asyncio

import pytest

from forge.api import HarnessSession, RunProfile, SessionOptions
from forge.config import ForgeConfig
from forge.runtime.state import ToolCall


class NoRequests:
    model = 'offline'
    provider = 'anthropic'
    async def stream(self, *args, **kwargs):
        raise AssertionError('Mode changes must not call a model')
        yield
    async def aclose(self):
        pass


def options(tmp_path, **changes):
    root = tmp_path / 'project'
    root.mkdir(exist_ok=True)
    return SessionOptions(root, data_root=tmp_path / 'data',
        config=ForgeConfig(api_key='offline-only', model_id='offline'),
        profile=RunProfile(intent_routing=False), **changes)


def remember_user(session):
    message = {'role': 'user', 'content': 'Offline state fixture'}
    session.journal.record_user_message(message, None)
    session.conversation.messages.append(message)


async def write(session, path, content='fixture'):
    return await session.conversation.tool_executor.execute(ToolCall(0, 'write', 'write_file',
        {'path': str(path), 'content': content}))


def test_switch_updates_backend_observer_scope_and_model_context(tmp_path):
    async def run():
        settings = options(tmp_path)
        async with await HarnessSession.open(settings, model_client_factory=lambda *a, **k: NoRequests()) as session:
            initial = session.backend
            assert session.conversation.permission_manager.mode == 'ask'
            await session.set_sandbox('read-only')
            assert not (await write(session, settings.root / 'no.txt')).result.success
            await session.set_sandbox('danger-full-access')
            outside = tmp_path / 'outside.txt'
            assert (await write(session, outside)).result.success
            assert session.backend.mode == 'local-trusted'
            assert session.conversation.workspace_tracker.observer is session.backend.observer
            assert session.conversation.registry.implementation('explore_repository').tool_backend is session.backend
            assert 'full-access' in session.conversation._permission_system_context()
            await session.set_sandbox('workspace-write')
            assert session.backend.mode == 'workspace-write'
            assert not (await write(session, outside, 'not allowed')).result.success
            assert outside.read_text() == 'fixture'
            assert initial._opening is None and initial._closing is not None
            assert session.backend._opening is None
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_resume_replays_mode_and_approval_without_leaking_to_new_session(tmp_path):
    async def run():
        settings = options(tmp_path)
        factory = lambda *a, **k: NoRequests()
        async with await HarnessSession.open(settings, model_client_factory=factory) as first:
            remember_user(first)
            await first.set_sandbox('full-access')
            first.conversation.permission_set_mode('never')
            identity = first.session_id
        async with await HarnessSession.open(settings, resume_identifier=identity, model_client_factory=factory) as restored:
            assert restored.backend.mode == 'local-trusted'
            assert restored.conversation.permission_manager.execution_mode == 'full-access'
            assert restored.conversation.permission_manager.mode == 'never'
            assert restored.conversation.session_options.sandbox == 'full-access'
        async with await HarnessSession.open(settings, model_client_factory=factory) as fresh:
            assert fresh.backend.mode == 'workspace-write'
            assert fresh.conversation.permission_manager.mode == 'ask'
    asyncio.run(run())


def test_branch_inherits_but_clear_resets_and_resume_restores(tmp_path):
    async def run():
        settings = options(tmp_path)
        async with await HarnessSession.open(settings, model_client_factory=lambda *a, **k: NoRequests()) as session:
            remember_user(session)
            await session.set_sandbox('full-access')
            session.conversation.permission_set_mode('never')
            original = session.session_id
            session.conversation.session_branch('policy-branch')
            branch = session.session_id
            state = session.conversation.session_store.load(branch)
            assert state.sandbox_mode == 'full-access' and state.approval_policy == 'never'
            await session.conversation.session_clear_with_hooks()
            assert session.backend.mode == 'workspace-write'
            assert session.conversation.permission_manager.mode == 'ask'
            assert session.session_id not in (original, branch)
            await session.conversation.session_resume_with_hooks(original)
            assert session.backend.mode == 'local-trusted'
            assert session.conversation.permission_manager.mode == 'never'
            assert session.session_id == original
    asyncio.run(run())


def test_scoped_writes_survive_read_only_switch(tmp_path):
    async def run():
        settings = options(tmp_path)
        target = settings.root / 'src'
        target.mkdir()
        from dataclasses import replace
        settings = replace(settings, write_roots=(target,))
        async with await HarnessSession.open(settings, model_client_factory=lambda *a, **k: NoRequests()) as session:
            await session.set_sandbox('read-only')
            assert session.backend.observer.policy['filesystem']['write_roots'] == []
            await session.set_sandbox('workspace-write')
            assert (await write(session, target / 'yes.txt')).result.success
            assert not (await write(session, settings.root / 'no.txt')).result.success
    asyncio.run(run())


def test_switch_cancellation_does_not_change_live_or_persisted_authority(tmp_path, monkeypatch):
    import forge.execution as execution
    async def run():
        session = await HarnessSession.open(options(tmp_path), model_client_factory=lambda *a, **k: NoRequests())
        original = execution.open_execution
        started = asyncio.Event()
        async def pending(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(execution, 'open_execution', pending)
        try:
            task = asyncio.create_task(session.set_sandbox('full-access'))
            await asyncio.wait_for(started.wait(), 2)
            with pytest.raises(RuntimeError):
                await session.set_sandbox('read-only')
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert session.backend.mode == 'workspace-write'
            assert session.conversation.session_store.load(session.session_id).sandbox_mode == 'workspace-write'
            assert session.backend._closing is None
        finally:
            await session.close()
    asyncio.run(run())


def test_failed_mode_event_does_not_publish_new_authority(tmp_path, monkeypatch):
    async def run():
        session = await HarnessSession.open(options(tmp_path), model_client_factory=lambda *a, **k: NoRequests())
        original = session.journal.append
        def fail_event(kind, payload):
            if kind == 'sandbox_mode_changed':
                raise OSError('injected journal failure')
            return original(kind, payload)
        monkeypatch.setattr(session.journal, 'append', fail_event)
        try:
            with pytest.raises(OSError, match='journal'):
                await session.set_sandbox('full-access')
            assert session.conversation.permission_manager.execution_mode == 'workspace-write'
            assert session.conversation.session_store.load(session.session_id).sandbox_mode == 'workspace-write'
        finally:
            await session.close()
    asyncio.run(run())


def test_failed_resume_before_binding_restores_original_authority(tmp_path, monkeypatch):
    async def run():
        settings = options(tmp_path)
        factory = lambda *a, **k: NoRequests()
        async with await HarnessSession.open(settings, model_client_factory=factory) as target:
            remember_user(target)
            await target.set_sandbox('full-access')
            identifier = target.session_id
        async with await HarnessSession.open(settings, model_client_factory=factory) as session:
            await session.set_sandbox('read-only')
            original_id = session.session_id
            def fail_resume(*args):
                raise ValueError('injected history binding failure')
            monkeypatch.setattr(session.conversation, 'session_resume', fail_resume)
            with pytest.raises(ValueError, match='binding failure'):
                await session.conversation.session_resume_with_hooks(identifier)
            assert session.session_id == original_id
            assert session.backend.mode == session.conversation.permission_manager.execution_mode == 'read-only'
            assert session.conversation.session_store.load(original_id).sandbox_mode == 'read-only'
    asyncio.run(run())
