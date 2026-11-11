"""Ordinary coding uses direct model calls and per-target observations."""
import asyncio
from dataclasses import replace

import pytest

from forge.api import HarnessSession, SessionOptions
from forge.config import ForgeConfig
from forge.permissions.policy import ApprovalResponse
from forge.runtime.completion import TaskPolicy
from forge.runtime.state import ModelResponseCompleted, ModelTextDelta, ModelToolCallCompleted, ToolCall


def options(tmp_path, **changes):
    root = tmp_path / 'project'
    root.mkdir()
    return SessionOptions(root, data_root=tmp_path / 'data',
        config=ForgeConfig(api_key='offline', model_id='offline'), **changes)


class Model:
    model = 'offline'
    async def stream(self, messages, **kwargs):
        yield ModelTextDelta('done')
        yield ModelResponseCompleted('end_turn')
    async def aclose(self): pass


def test_defaults_are_full_access_without_router_and_keep_explicit_scope(tmp_path):
    settings = options(tmp_path)
    assert settings.sandbox == 'full-access' and settings.approval == 'never'
    assert not settings.profile.intent_routing
    assert replace(settings, sandbox='read-only', approval=None).approval == 'ask'
    assert replace(settings, sandbox=None, write_roots=(), approval=None).sandbox == 'workspace-write'


def test_normal_turn_edits_large_file_and_runs_command_without_inventory_or_gate(tmp_path):
    settings = options(tmp_path)
    target = settings.root / 'large.txt'
    target.write_text(('original\n' * 80000), encoding='utf-8')  # Exceeds the old checkpoint cap.
    factories, calls = [], []
    class EditingModel(Model):
        async def stream(self, messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                yield ModelToolCallCompleted(ToolCall(0, 'read', 'read_file', {'path': 'large.txt', 'end_line': 2}))
            elif len(calls) == 2:
                yield ModelToolCallCompleted(ToolCall(0, 'write', 'write_file', {'path': 'large.txt', 'content': 'x' * 40000}))
                yield ModelToolCallCompleted(ToolCall(1, 'command', 'run_command', {'command': 'echo DIRECT_CODING'}))
            else:
                yield ModelTextDelta('Written without a mandatory verification pass.')
            yield ModelResponseCompleted('tool_use' if len(calls) < 3 else 'end_turn')
    def factory(*args, **kwargs):
        factories.append(kwargs)
        return EditingModel()
    async def run():
        async with await HarnessSession.open(settings, model_client_factory=factory) as session:
            c = session.conversation
            assert c.completion_gate is None and c.intent_router is None
            async def no_scan(): pytest.fail('Ordinary coding must not scan the whole workspace')
            c.workspace_tracker._capture = no_scan
            result = await session.run('Replace large.txt and echo a marker; no tests are requested.')
            assert result.status == 'completed' and result.model_calls == 3
            assert target.read_text() == 'x' * 40000
            assert result.changed_paths == ('large.txt',)
            assert not c.checkpoint_store.list()
            assert not c.workspace_tracker.available
            assert result.completion_report.workspace_tracking_complete is None
            assert c.workspace_tracker.environment_epoch == 1
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())
    assert len(factories) == 1


def test_explicit_contract_still_enforces_acceptance_and_baseline(tmp_path):
    from forge.runtime.factory import create_runtime
    settings = options(tmp_path)
    def runtime(root, **kwargs):
        return create_runtime(root, task_policy=TaskPolicy(require_changes=True), **kwargs)
    async def run():
        async with await HarnessSession.open(settings, runtime_factory=runtime,
                model_client_factory=lambda *a, **k: Model()) as session:
            assert session.conversation.completion_gate is not None
            result = await session.run('Make the required change.')
            assert result.status == 'partial'
            assert any('Diff is empty' in reason for reason in result.completion_reasons)
    asyncio.run(run())


def test_unread_and_stale_versions_are_not_overwritten_and_fresh_read_allows_replace(tmp_path):
    settings = options(tmp_path)
    target = settings.root / 'file.txt'
    target.write_text('original')
    async def run():
        async with await HarnessSession.open(settings, model_client_factory=lambda *a, **k: Model()) as session:
            executor = session.conversation.tool_executor
            async def call(name, arguments):
                return (await executor.execute(ToolCall(0, name, name, arguments))).result
            denied = await call('write_file', {'path': 'file.txt', 'content': 'new'})
            assert denied.error.code == 'FS_NOT_OBSERVED' and target.read_text() == 'original'
            assert (await call('read_file', {'path': 'file.txt'})).success
            target.write_text('external edit')
            stale = await call('write_file', {'path': 'file.txt', 'content': 'new'})
            assert stale.error.code == 'STALE_FILE' and target.read_text() == 'external edit'
            assert (await call('read_file', {'path': 'file.txt'})).success
            assert (await call('write_file_chunk', {'path': 'file.txt', 'content': 'new',
                'offset': 0, 'truncate': True, 'final': True})).success
            assert target.read_text() == 'new'
    asyncio.run(run())


def test_read_only_and_explicit_deny_still_apply(tmp_path):
    settings = options(tmp_path, sandbox='read-only')
    async def deny(request): return ApprovalResponse('deny')
    async def run():
        async with await HarnessSession.open(settings, approval_handler=deny,
                model_client_factory=lambda *a, **k: Model()) as session:
            outcome = await session.conversation.tool_executor.execute(ToolCall(0, 'write', 'write_file',
                {'path': 'blocked.txt', 'content': 'no'}))
            assert not outcome.result.success and not (settings.root / 'blocked.txt').exists()
            assert session.conversation.permission_manager.execution_mode == 'read-only'
    asyncio.run(run())


def test_file_formats_empty_content_and_single_chunk_are_not_harness_policy(tmp_path):
    from forge.tools.filesystem import WriteFileTool, WriteFileChunkTool, ReplaceTextTool
    async def run():
        write = WriteFileTool(tmp_path)
        assert (await write.run({'path': 'config.json', 'content': '{unfinished'})).success
        assert (await ReplaceTextTool(tmp_path).run({'path': 'config.json',
            'old_text': 'unfinished', 'new_text': 'still unfinished'})).success
        assert (await write.run({'path': 'assets', 'content': ''})).success
        assert (tmp_path / 'assets').is_file()
        chunk = await WriteFileChunkTool(tmp_path).run({'path': 'config.json',
            'content': 'x' * 1_000_001, 'offset': 0, 'truncate': True, 'final': True})
        assert chunk.success and (tmp_path / 'config.json').stat().st_size == 1_000_001
    asyncio.run(run())


def test_direct_conversation_also_has_no_implicit_completion_gate(tmp_path):
    from forge.runtime.agent_loop import Conversation
    from forge.tools import create_default_registry
    c = Conversation(client=Model(), registry=create_default_registry(tmp_path), context_root=tmp_path)
    assert c.completion_gate is None and not c.workspace_tracker.full_inventory


def test_new_full_access_default_does_not_replace_saved_workspace_permissions(tmp_path):
    original = options(tmp_path, sandbox='workspace-write')
    factory = lambda *a, **k: Model()
    async def run():
        async with await HarnessSession.open(original, model_client_factory=factory) as session:
            await session.run('Remember this session.')
            identity = session.session_id
        defaults = replace(original, sandbox=None, approval=None)
        assert defaults.sandbox == 'full-access' and defaults.approval == 'never'
        async with await HarnessSession.open(defaults, resume_identifier=identity,
                model_client_factory=factory) as restored:
            assert restored.conversation.permission_manager.execution_mode == 'workspace-write'
            assert restored.conversation.permission_manager.mode == 'ask'
            await restored.conversation.session_clear_with_hooks()
            assert restored.conversation.permission_manager.execution_mode == 'full-access'
            assert restored.conversation.permission_manager.mode == 'never'
    asyncio.run(run())


def test_failed_check_is_reported_without_forcing_an_ordinary_repair_loop(tmp_path):
    settings = options(tmp_path)
    class Checking(Model):
        calls = 0
        async def stream(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield ModelToolCallCompleted(ToolCall(0, 'check', 'verify', {
                    'command': 'python -c "raise SystemExit(1)"'}))
            else:
                yield ModelTextDelta('The diagnostic check failed.')
            yield ModelResponseCompleted('tool_use' if self.calls == 1 else 'end_turn')
    model = Checking()
    async def run():
        async with await HarnessSession.open(settings, model_client_factory=lambda *a, **k: model) as session:
            async def no_scan(): pytest.fail('A check does not enable full inventory')
            session.conversation.workspace_tracker._capture = no_scan
            result = await session.run('Run the failing diagnostic once and report it.')
            assert result.status == 'completed' and result.model_calls == 2
            assert result.completion_report.verification_status == 'failed_checks'
            assert result.completion_report.acceptance_status == 'not_configured'
    asyncio.run(run())
