'''Working evidence indexes do not intercept actual read and search execution.'''
import asyncio
import json

from forge.permissions.policy import PermissionManager
from forge.runtime.agent_loop import Conversation
from forge.runtime.state import ModelTextDelta, ModelToolCallCompleted, ModelUsageUpdate, TokenUsage, ToolCall
from forge.runtime.workspace import WorkspaceTracker
from forge.tools.base import ToolRegistry
from forge.tools.filesystem import ReadFileTool
from forge.tools.search import GrepTool


def test_actual_loop_preserves_read_cache_freshness_and_search_after_covered_read(tmp_path):
    path = tmp_path / 'sample.py'
    path.write_text('old_value = 1\n', encoding='utf-8')
    reads = []

    class Model:
        calls = 0

        async def stream(self, messages, **kwargs):
            request = self.calls
            self.calls += 1
            if request:
                result = json.loads(messages[-1]['content'][0]['content'])
                assert result['success']
                if request <= 3:
                    reads.append(result)
                else:
                    assert 'new_value = 2' in result['content']
                assert 'sample.py @ revision 0: lines 1-1' in kwargs['system']
            yield ModelUsageUpdate(TokenUsage(1, 1))
            if request < 3:
                if request == 2:
                    path.write_text('new_value = 2\n', encoding='utf-8')
                yield ModelToolCallCompleted(ToolCall(0, f'read-{request}', 'read_file', {'path': 'sample.py'}))
            elif request == 3:
                yield ModelToolCallCompleted(ToolCall(0, 'grep', 'grep', {'path': 'sample.py', 'pattern': 'new_value'}))
            else:
                yield ModelTextDelta('Read and search reflect the current file.')

    model = Model()
    registry = ToolRegistry([ReadFileTool(tmp_path), GrepTool(tmp_path)], workspace_tracker=WorkspaceTracker(tmp_path))
    conversation = Conversation(client=model, registry=registry, context_root=tmp_path,
        permission_manager=PermissionManager(tmp_path, user_path=tmp_path / 'permissions.json'),
        include_task_tools=False)

    async def run():
        return [event async for event in conversation.stream('Inspect sample.py')]

    events = asyncio.run(run())
    assert events[-1].result.status == 'completed'
    assert model.calls == 5
    assert 'old_value = 1' in reads[0]['content']
    assert reads[1]['content'] == reads[0]['content']
    assert reads[1]['metadata']['cache_hit'] is True
    assert 'new_value = 2' in reads[2]['content']
    assert not reads[2]['metadata'].get('cache_hit')
    assert conversation.working_state.evidence_paths == ('sample.py',)
