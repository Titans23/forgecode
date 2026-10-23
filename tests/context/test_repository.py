'''Tests for repository instructions and durable memory.'''

from pathlib import Path

import pytest

from forge.context.repository import MemoryStore, RepositoryContext


def test_memory_round_trip_index_and_forget(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)

    record = store.remember(
        'testing',
        'Run uv run pytest after code changes.',
        description='Project test command',
    )

    assert record.path.exists()
    assert store.get('testing') is not None
    assert MemoryStore(tmp_path).get('testing') is not None
    assert '[testing](testing.md)' in store.index_path.read_text(encoding='utf-8')
    assert store.forget('testing') is True
    assert store.get('testing') is None


def test_memory_selection_is_relevant_and_bounded(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, max_selected=1, max_file_characters=40)
    store.remember('testing', 'Use pytest for calculator verification.')
    store.remember('style', 'Use single quotes in Python source.')

    selection = store.select('How should I run calculator tests?')

    assert [record.name for record in selection.records] == ['testing']
    assert 'pytest' in selection.rendered
    assert 'single quotes' not in selection.rendered


def test_memory_rejects_secrets(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)

    with pytest.raises(ValueError, match='secret'):
        store.remember('credentials', 'API_KEY=sk-supersecret123')


def test_repository_context_loads_rules_and_relevant_memory(
    tmp_path: Path,
) -> None:
    (tmp_path / 'AGENTS.md').write_text('Always run tests.', encoding='utf-8')
    (tmp_path / 'FORGE.md').write_text('Project-wide conventions.', encoding='utf-8')
    rules = tmp_path / '.forge' / 'rules'
    rules.mkdir(parents=True)
    (rules / 'python.md').write_text('Use Python 3.12.', encoding='utf-8')
    nested = tmp_path / 'src'
    nested.mkdir()
    (nested / 'AGENTS.md').write_text('Nested instructions.', encoding='utf-8')
    (nested / 'FORGE.md').write_text('Nested conventions.', encoding='utf-8')
    context = RepositoryContext(tmp_path)
    context.memory.remember('calculator', 'Calculator tests use pytest.')

    suffix = context.system_suffix('fix calculator tests')

    assert 'Always run tests.' in suffix
    assert 'Project-wide conventions.' in suffix
    assert 'Use Python 3.12.' in suffix
    assert 'Calculator tests use pytest.' in suffix
    assert 'Nested instructions.' not in suffix
    assert 'Nested conventions.' not in suffix


def test_consolidation_removes_exact_duplicate_content(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.remember('first', 'Same durable fact.')
    store.remember('second', 'Same durable fact.')

    removed = store.consolidate()

    assert removed == 1
    assert len(store.list()) == 1


def test_explicit_user_memory_is_captured_but_secret_is_ignored(
    tmp_path: Path,
) -> None:
    from forge.context.manager import ContextManager

    manager = ContextManager([], tmp_path)

    saved = manager.capture_explicit_memory('请记住：项目测试使用 uv run pytest')
    rejected = manager.capture_explicit_memory('记住：API_KEY=sk-secret1234')

    assert saved is not None
    assert rejected is None
    assert len(MemoryStore(tmp_path).list()) == 1


def test_hardlinked_instructions_cannot_read_another_file(tmp_path):
    import os
    secret = tmp_path / '.env'
    secret.write_text('synthetic protected content')
    os.link(secret, tmp_path / 'AGENTS.md')
    with pytest.raises(ValueError, match='Linked repository instructions'):
        RepositoryContext(tmp_path).instructions()


def test_accessed_ancestor_rules_are_ordered_refreshed_and_scoped(tmp_path):
    (tmp_path / 'AGENTS.md').write_text('root rule')
    (tmp_path / 'src' / 'nested').mkdir(parents=True)
    (tmp_path / 'other').mkdir()
    rule = tmp_path / 'src' / 'AGENTS.md'
    rule.write_text('src rule')
    (tmp_path / 'src' / 'nested' / 'FORGE.md').write_text('nested rule')
    (tmp_path / 'other' / 'AGENTS.md').write_text('unrelated rule')
    context = RepositoryContext(tmp_path)
    assert 'src rule' not in context.instructions()
    context.observe_paths(['src/nested/new.py', '../outside/file.py'])
    text = context.instructions()
    assert text.index('root rule') < text.index('src rule') < text.index('nested rule')
    assert 'Applies to src/nested/' in text
    assert 'unrelated rule' not in text
    rule.write_text('updated rule')
    assert 'updated rule' in context.instructions()


def test_nested_rules_are_injected_after_read_and_refreshed_next_turn(tmp_path):
    import asyncio
    from forge.runtime.agent_loop import Conversation
    from forge.runtime.state import ModelToolCallCompleted, ModelResponseCompleted, ModelTextDelta, ToolCall
    from forge.permissions.policy import PermissionManager
    from forge.tools.base import ToolRegistry
    from forge.tools.filesystem import ReadFileTool, WriteFileTool

    root_rule = tmp_path / 'AGENTS.md'
    root_rule.write_text('Root project rule.', encoding='utf-8')
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'AGENTS.md').write_text('Nested project notice.', encoding='utf-8')
    (tmp_path / 'src' / 'input.txt').write_text('input', encoding='utf-8')

    class Model:
        calls = 0

        async def stream(self, messages, system, **kwargs):
            self.calls += 1
            assert ('Nested project notice.' in system) == (self.calls > 1)
            assert ('Updated root rule.' if self.calls == 4 else 'Root project rule.') in system
            if self.calls == 1:
                yield ModelToolCallCompleted(ToolCall(0, 'read-input', 'read_file', {'path': 'src/input.txt'}))
            elif self.calls == 2:
                yield ModelToolCallCompleted(ToolCall(0, 'write-result', 'write_file',
                    {'path': 'src/result.txt', 'content': 'result'}))
            else:
                assert (tmp_path / 'src' / 'result.txt').read_text() == 'result'
                yield ModelTextDelta('done')
            yield ModelResponseCompleted('tool_use' if self.calls <= 2 else 'end_turn')

    async def run():
        model = Model()
        conversation = Conversation(client=model, context_root=tmp_path,
            registry=ToolRegistry([ReadFileTool(tmp_path), WriteFileTool(tmp_path)]), include_task_tools=False,
            permission_manager=PermissionManager(tmp_path, mode='auto', load_stored_rules=False))
        try:
            async for _ in conversation.stream('Read the input, then create src/result.txt.'):
                pass
            assert model.calls == 3
            assert conversation.turn_state.stop_reason == 'completed'
            root_rule.write_text('Updated root rule.', encoding='utf-8')
            async for _ in conversation.stream('Summarize the previous work.'):
                pass
            assert model.calls == 4
        finally:
            await conversation.runtime_close()
    asyncio.run(run())


@pytest.mark.parametrize('read_in_same_batch', [False, True])
def test_unseen_rules_block_write_until_next_model_request(tmp_path, read_in_same_batch):
    import asyncio
    from forge.runtime.agent_loop import Conversation
    from forge.runtime.state import ModelToolCallCompleted, ModelResponseCompleted, ModelTextDelta, ToolCall
    from forge.permissions.policy import PermissionManager
    from forge.tools.base import ToolRegistry
    from forge.tools.filesystem import ReadFileTool, WriteFileTool
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'AGENTS.md').write_text('Include the project notice in new files.')
    (tmp_path / 'src' / 'input.txt').write_text('input')
    target = tmp_path / 'src' / 'result.txt'

    class Model:
        calls = 0

        async def stream(self, messages, system, **kwargs):
            self.calls += 1
            if self.calls == 1:
                if read_in_same_batch:
                    yield ModelToolCallCompleted(ToolCall(0, 'read', 'read_file', {'path': 'src/input.txt'}))
                yield ModelToolCallCompleted(ToolCall(1, 'unseen-write', 'write_file',
                    {'path': 'src/result.txt', 'content': 'missing notice'}))
            elif self.calls == 2:
                assert not target.exists()
                assert 'Include the project notice' in system
                assert 'scoped_instructions_required' in str(messages[-1])
                yield ModelToolCallCompleted(ToolCall(0, 'informed-write', 'write_file',
                    {'path': 'src/result.txt', 'content': 'project notice'}))
            else:
                yield ModelTextDelta('done')
            yield ModelResponseCompleted('tool_use' if self.calls < 3 else 'end_turn')

    async def run():
        conversation = Conversation(client=Model(), context_root=tmp_path,
            registry=ToolRegistry([ReadFileTool(tmp_path), WriteFileTool(tmp_path)]), include_task_tools=False,
            permission_manager=PermissionManager(tmp_path, mode='auto', load_stored_rules=False))
        try:
            async for _ in conversation.stream('Create src/result.txt'):
                pass
            assert target.read_text() == 'project notice'
            # This boundary-only fixture supplies no independent delivery
            # evidence to clear the rejected write; do not claim acceptance.
            assert conversation.turn_state.stop_reason == 'acceptance_unmet'
        finally:
            await conversation.runtime_close()
    asyncio.run(run())
