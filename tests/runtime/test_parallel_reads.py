import asyncio

from forge.runtime.agent_loop import Conversation
from forge.runtime.state import ModelToolCallCompleted, ModelResponseCompleted, ModelTextDelta, ToolCall
from forge.tools.base import ToolRegistry
from forge.tools.filesystem import ReadFileTool


def test_independent_reads_overlap_but_results_keep_model_order(tmp_path):
    for index in range(4):
        (tmp_path / f'{index}.txt').write_text(f'file {index}')

    async def run():
        tool = ReadFileTool(tmp_path)
        execute = tool.execute
        active = maximum = 0
        paired = asyncio.Event()
        async def observed(arguments):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                paired.set()
            await asyncio.wait_for(paired.wait(), 2)
            try:
                return await execute(arguments)
            finally:
                active -= 1
        tool.execute = observed

        class Model:
            calls = 0
            async def stream(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    for index in range(4):
                        yield ModelToolCallCompleted(ToolCall(index, str(index), 'read_file', {'path': f'{index}.txt'}))
                else:
                    assert [block['tool_use_id'] for block in messages[-1]['content']] == ['0', '1', '2', '3']
                    yield ModelTextDelta('read all files')
                yield ModelResponseCompleted('tool_use' if self.calls == 1 else 'end_turn')

        conversation = Conversation(client=Model(), registry=ToolRegistry([tool]), context_root=tmp_path,
            include_task_tools=False, max_parallel_reads=2)
        events = [event async for event in conversation.stream('Read the four files')]
        assert events[-1].result.text == 'read all files'
        assert maximum == 2
        assert len(conversation.turn_state.execution_records) == 4
    asyncio.run(run())


def test_native_backend_without_parallel_contract_stays_serial(tmp_path):
    from types import SimpleNamespace
    from forge.runtime.read_pool import independent_reads
    tool = ReadFileTool(tmp_path)
    conversation = Conversation(client=object(), registry=ToolRegistry([tool]), context_root=tmp_path,
        tool_backend=SimpleNamespace(mode='workspace-write'), include_task_tools=False)
    calls = [ToolCall(i, str(i), 'read_file', {'path': f'{i}.txt'}) for i in range(2)]
    assert independent_reads(conversation, calls, 0, 4) == []
