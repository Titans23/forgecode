"""Compaction summaries belong to committed history, not a second session cache."""
import asyncio
import json

import pytest

from forge.context.compactor import CompactionConfig
from forge.context.manager import ContextManager
from forge.hooks.models import HookOutcome
from forge.runtime.agent_loop import Conversation
from forge.runtime.state import ModelTextDelta, ModelUsageUpdate, TokenUsage, ToolCall
from forge.sessions.checkpoint import CheckpointStore
from forge.sessions.store import SessionStore
from forge.tools.filesystem import ReadFileTool


SUMMARY_MARKER = 'source-only-compaction-finding'


class SummaryClient:
    model = 'offline-summary'

    def __init__(self):
        self.calls = 0
        self.valid = True

    async def stream(self, messages, tools=None, **kwargs):
        self.calls += 1
        assert tools is None
        yield ModelTextDelta(json.dumps({'goal': 'Initial task', 'findings': [SUMMARY_MARKER]})
            if self.valid else 'invalid summary JSON')
        yield ModelUsageUpdate(TokenUsage(10, 5))


def persisted_conversation(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    store = SessionStore(root, data_root=tmp_path / 'data')
    client = SummaryClient()
    journal = store.create(model=client.model)
    initial = {'role': 'user', 'content': 'Initial task before the later summary.'}
    journal.record_user_message(initial, None)
    conversation = Conversation(client=client, tools=[], context_root=root,
        initial_messages=[initial], session_store=store, session_journal=journal,
        checkpoint_store=CheckpointStore.for_session(root, journal.path, journal.session_id))
    return conversation, store, client


@pytest.mark.parametrize('operation', ['clear', 'resume', 'rewind'])
def test_history_switch_cannot_reinject_the_previous_compaction_summary(tmp_path, operation):
    async def run():
        conversation, store, client = persisted_conversation(tmp_path)
        source = conversation.session_journal
        checkpoint = conversation.checkpoint_store.begin()
        source.record_checkpoint_created(checkpoint, conversation.messages, None)
        memory = conversation.context.repository.memory.remember('shared-rule', 'Use the project virtual environment.')
        try:
            assert (await conversation.compact()).success
            assert SUMMARY_MARKER in json.dumps(conversation.context.prepare(conversation.messages))
            if operation == 'clear':
                conversation.session_clear()
            elif operation == 'resume':
                target = store.create(model=client.model)
                target.record_user_message({'role': 'user', 'content': 'Only the target session.'}, None)
                conversation.session_resume(target.session_id)
            else:
                conversation.checkpoint_rewind(checkpoint, mode='conversation')

            assert SUMMARY_MARKER not in json.dumps(conversation.messages)
            assert SUMMARY_MARKER not in json.dumps(conversation.context.prepare(conversation.messages))
            assert SUMMARY_MARKER in source.path.read_text(encoding='utf-8')
            assert conversation.context.repository.memory.get(memory.name).content == memory.content
            assert client.calls == 1
        finally:
            await conversation.runtime_close()
    asyncio.run(run())


async def seed_transient_history(conversation, client):
    root = conversation.task_manager.root
    (root / 'AGENTS.md').write_text('ROOT_RULE', encoding='utf-8')
    scoped = root / 'old-scope'
    scoped.mkdir()
    (scoped / 'AGENTS.md').write_text('OLD_SCOPE_RULE', encoding='utf-8')
    (scoped / 'file.py').write_text('old evidence\n', encoding='utf-8')
    code = root / 'code.py'
    code.write_text('before', encoding='utf-8')
    checkpoint = conversation.checkpoint_store.begin()
    conversation.session_journal.record_checkpoint_created(checkpoint, conversation.messages, None)
    conversation.checkpoint_store.capture_before(checkpoint, ('code.py',))
    code.write_text('after', encoding='utf-8')
    conversation.checkpoint_store.record_after(checkpoint, ('code.py',))
    assert (await conversation.compact()).success
    client.valid = False
    for _ in range(conversation.context.config.max_summary_failures):
        assert not (await conversation.compact()).success
    assert 'fuse is open' in (await conversation.compact()).reason
    conversation.context.repository.observe_paths(('old-scope/file.py',))
    memory = conversation.context.repository.memory.remember('project-rule', 'Keep the project virtual environment.')
    conversation._last_repository_context = conversation._repository_context('project')
    result = await ReadFileTool(root).run({'path': 'old-scope/file.py'})
    assert result.success
    conversation.working_state.observe(ToolCall(0, 'old-read', 'read_file', {'path': 'old-scope/file.py'}), result, 0)
    conversation._queue_hook_context(HookOutcome(additional_context=('OLD_HOOK_CONTEXT',)))
    return checkpoint, memory


@pytest.mark.parametrize('operation', ['clear', 'clear_unpersisted', 'resume', 'rewind_conversation', 'rewind_both'])
def test_replacing_history_resets_transient_context_and_compaction_fuse(tmp_path, operation):
    async def run():
        conversation, store, client = persisted_conversation(tmp_path)
        try:
            checkpoint, memory = await seed_transient_history(conversation, client)
            messages = conversation.messages
            verification = conversation.verification_history
            context = conversation.context
            calls = client.calls
            if operation.startswith('clear'):
                if operation == 'clear_unpersisted':
                    conversation.session_store = None
                    conversation.session_journal = None
                conversation.session_clear()
            elif operation == 'resume':
                target = store.create(model=client.model)
                target.record_user_message({'role': 'user', 'content': 'Target session.'}, None)
                conversation.session_resume(target.session_id)
            else:
                conversation.checkpoint_rewind(checkpoint, mode=operation.removeprefix('rewind_'))

            assert conversation.messages is messages
            assert conversation.verification_history is verification
            assert conversation.context is context
            assert context.summary_failures == 0
            assert context.last_compaction is None and context.last_report is None
            assert conversation.working_state.system_suffix() == ''
            assert conversation._pending_hook_context == []
            assert conversation._last_repository_context == ''
            rules = conversation._repository_context('project')
            assert 'ROOT_RULE' in rules and 'OLD_SCOPE_RULE' not in rules
            assert context.repository.memory.get(memory.name).content == memory.content
            if not messages:
                prompt = {'role': 'user', 'content': 'New history can be summarized.'}
                messages.append(prompt)
                if conversation.session_journal is not None:
                    conversation.session_journal.record_user_message(prompt, None)
            client.valid = True
            assert (await conversation.compact()).success
            assert client.calls == calls + 1
        finally:
            await conversation.runtime_close()
    asyncio.run(run())


@pytest.mark.parametrize('operation', ['branch', 'rewind_code', 'resume_same'])
def test_preserving_history_keeps_valid_context_state(tmp_path, operation):
    async def run():
        conversation, _, client = persisted_conversation(tmp_path)
        try:
            checkpoint, memory = await seed_transient_history(conversation, client)
            messages = conversation.messages
            original_messages = list(messages)
            context = conversation.context
            compaction = context.last_compaction
            report = context.last_report
            working = conversation.working_state
            repository_context = conversation._last_repository_context
            if operation == 'branch':
                conversation.session_branch('same-history')
            elif operation == 'rewind_code':
                conversation.checkpoint_rewind(checkpoint, mode='code')
                assert (conversation.task_manager.root / 'code.py').read_text(encoding='utf-8') == 'before'
            else:
                conversation.session_resume(conversation.session_journal.session_id)

            assert conversation.messages is messages and messages == original_messages
            assert conversation.context is context
            assert context.last_compaction is compaction and context.last_report is report
            assert context.summary_failures == context.config.max_summary_failures
            assert conversation.working_state is working and 'old-scope/file.py' in working.system_suffix()
            assert conversation._pending_hook_context == ['OLD_HOOK_CONTEXT']
            assert conversation._last_repository_context == repository_context
            assert 'OLD_SCOPE_RULE' in context.repository.instructions()
            assert context.repository.memory.get(memory.name).content == memory.content
        finally:
            await conversation.runtime_close()
    asyncio.run(run())


def test_reloaded_history_retains_its_summary_through_cheap_compaction(tmp_path):
    async def run():
        conversation, store, client = persisted_conversation(tmp_path)
        try:
            assert (await conversation.compact()).success
            restored = list(store.load(conversation.session_journal.session_id).messages)
            restored.extend({'role': 'user', 'content': f'later message {index}'} for index in range(12))
            context = ContextManager(restored, store.project_root,
                CompactionConfig(message_limit=2, keep_first_messages=0, keep_recent_messages=1))
            prepared = context.prepare(restored)
            summaries = [message for message in prepared if isinstance(message.get('content'), str)
                and message['content'].startswith('[ForgeCode structured task summary]')]
            assert len(summaries) == 1
            assert SUMMARY_MARKER in summaries[0]['content']
            assert len(prepared) < len(restored)
            assert client.calls == 1
        finally:
            await conversation.runtime_close()
    asyncio.run(run())
