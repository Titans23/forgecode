from forge.context.repository import RepositoryContext


def test_scoped_rules_follow_accessed_ancestors_and_refresh(tmp_path):
    (tmp_path / 'AGENTS.md').write_text('root rule')
    (tmp_path / 'src' / 'nested').mkdir(parents=True)
    (tmp_path / 'other').mkdir()
    rule = tmp_path / 'src' / 'AGENTS.md'
    rule.write_text('src rule')
    (tmp_path / 'src' / 'nested' / 'AGENTS.md').write_text('nested rule')
    (tmp_path / 'other' / 'AGENTS.md').write_text('unrelated rule')
    context = RepositoryContext(tmp_path)
    assert 'src rule' not in context.instructions()
    context.observe_paths(['src/nested/new.py', '../outside/file.py'])
    text = context.instructions()
    assert text.index('root rule') < text.index('src rule') < text.index('nested rule')
    assert 'unrelated rule' not in text
    rule.write_text('updated rule')
    assert 'updated rule' in context.instructions()


def test_reported_usage_can_raise_compaction_estimate_without_erasing_history(tmp_path):
    from forge.context.manager import ContextManager
    messages = [{'role': 'user', 'content': '中文内容' * 500}]
    context = ContextManager(messages, tmp_path)
    assert not context.compaction_required(messages, context_window_tokens=4096, reserved_output_tokens=1024)
    context.observe_usage(messages, '', None, 3000)
    assert context.token_scale > 1
    assert context.compaction_required(messages, context_window_tokens=4096, reserved_output_tokens=1024)
    assert messages[0]['content'].startswith('中文内容')


def test_new_scoped_rules_are_seen_before_a_write_executes(tmp_path):
    import asyncio
    from forge.runtime.agent_loop import Conversation
    from forge.runtime.state import ModelToolCallCompleted, ModelResponseCompleted, ModelTextDelta, ToolCall
    from forge.permissions.policy import PermissionManager
    from forge.tools.base import ToolRegistry
    from forge.tools.filesystem import WriteFileTool
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'AGENTS.md').write_text('Include the project notice in every new file.')

    class Model:
        calls = 0
        async def stream(self, messages, system, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield ModelToolCallCompleted(ToolCall(0, 'before-rule', 'write_file',
                    {'path': 'src/result.txt', 'content': 'missing notice'}))
            elif self.calls == 2:
                assert not (tmp_path / 'src' / 'result.txt').exists()
                assert 'Include the project notice' in system
                assert 'scoped_instructions_required' in str(messages[-1])
                yield ModelToolCallCompleted(ToolCall(0, 'after-rule', 'write_file',
                    {'path': 'src/result.txt', 'content': 'project notice'}))
            else:
                yield ModelTextDelta('done')
            yield ModelResponseCompleted('tool_use' if self.calls < 3 else 'end_turn')

    async def run():
        conversation = Conversation(client=Model(), context_root=tmp_path,
            registry=ToolRegistry([WriteFileTool(tmp_path)]), include_task_tools=False,
            permission_manager=PermissionManager(tmp_path, mode='auto', load_stored_rules=False))
        async for _ in conversation.stream('Create src/result.txt'):
            pass
        assert (tmp_path / 'src' / 'result.txt').read_text() == 'project notice'
    asyncio.run(run())


def test_hardlinked_instructions_cannot_read_another_file(tmp_path):
    import os
    import pytest
    secret = tmp_path / '.env'
    secret.write_text('synthetic protected content')
    os.link(secret, tmp_path / 'AGENTS.md')
    with pytest.raises(ValueError, match='Linked repository instructions'):
        RepositoryContext(tmp_path).instructions()
