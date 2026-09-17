import asyncio
import pytest

from forge.runtime.agent_loop import Conversation
from forge.runtime.completion import TaskPolicy
from forge.runtime.runner import TurnRunner
from forge.tools import create_default_registry
from forge.tools.base import ToolResult


@pytest.mark.parametrize('declaration', [False, True])
def test_model_receives_feedback_and_can_submit_honest_partial(tmp_path, declaration):
    from forge.runtime.state import ModelTextDelta, ModelToolCallCompleted, ToolCall, TurnCompleted, ModelUsageUpdate, TokenUsage
    class Client:
        provider = 'fake'
        def __init__(self):
            self.calls = 0
        async def stream(self, messages, tools=None, system=None):
            self.calls += 1
            yield ModelUsageUpdate(usage=TokenUsage(input_tokens=10, output_tokens=2))
            if self.calls == 1 and not declaration:
                yield ModelTextDelta(text='Done.')
            else:
                if self.calls == 2:
                    assert any('Completion was not accepted' in str(message) for message in messages)
                yield ModelToolCallCompleted(tool_call=ToolCall(index=0, id=f'finish-{self.calls}',
                    name='finish_task', arguments={'status': 'completed' if self.calls == 1 else 'partial',
                    'task_kind': 'change', 'summary': 'Done' if self.calls == 1 else 'Verification remains missing'}))
    client = Client()
    conversation = Conversation(client=client, context_root=tmp_path,
        registry=create_default_registry(tmp_path), max_iterations=3,
        task_policy=TaskPolicy(require_verification=True, max_delivery_repairs=2))
    async def run():
        return [event async for event in conversation.stream('Verify the delivery.')]
    events = asyncio.run(run())
    result = next(event.result for event in events if isinstance(event, TurnCompleted))
    assert client.calls == result.model_calls == 2
    assert result.status == 'partial'
    assert result.stop_reason == 'partial'


def test_rejected_completion_gets_one_chance_per_gap_with_unchanged_budget(tmp_path):
    conversation = Conversation(client=object(), context_root=tmp_path,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_verification=True, max_delivery_repairs=2))
    runner = TurnRunner(conversation)
    declaration = ToolResult.ok('finish', metadata={
        'finish_task': True, 'status': 'completed', 'task_kind': 'change', 'summary': 'Done'})
    async def run():
        await runner.tracker.begin_turn()
        original = (runner.state.max_model_calls, runner.state.max_tool_calls, runner.state.max_seconds)
        first = await runner._finish_declaration(declaration)
        assert not first.success and first.error.code == 'delivery_repair_required'
        assert runner.terminal is None
        assert 'Runtime feedback' in runner.messages[-1]['content']
        second = await runner._finish_declaration(declaration)
        assert second.metadata['status'] == 'partial'
        assert runner.terminal[:2] == ('partial', 'acceptance_unmet')
        assert (runner.state.max_model_calls, runner.state.max_tool_calls, runner.state.max_seconds) == original
    asyncio.run(run())


def test_repair_cap_and_explicit_partial_are_terminal(tmp_path):
    conversation = Conversation(client=object(), context_root=tmp_path,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(max_delivery_repairs=2))
    runner = TurnRunner(conversation)
    assert runner._offer_delivery_repair(('first gap',))
    assert runner._offer_delivery_repair(('second gap',))
    assert not runner._offer_delivery_repair(('third gap',))
    result = asyncio.run(runner._finish_declaration(ToolResult.ok('finish', metadata={
        'finish_task': True, 'status': 'partial', 'task_kind': 'change', 'summary': 'Still incomplete'})))
    assert result.success
    assert runner.terminal[:2] == ('partial', 'partial')
