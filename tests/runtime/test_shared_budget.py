import asyncio

import pytest

from forge.runtime.model_budget import BudgetedModelClient
from forge.runtime.state import ModelResponseCompleted, ModelUsageUpdate, TokenUsage
from forge.runtime.turn_state import BudgetExhausted, TurnState


def test_children_compete_for_one_root_request_budget():
    async def run():
        root = TurnState(max_model_calls=2)
        children = [TurnState(parent=root), TurnState(parent=root)]
        class Client:
            async def stream(self, **kwargs):
                yield ModelUsageUpdate(TokenUsage(4, 2))
                yield ModelResponseCompleted('end_turn')
        for child in children:
            async for _ in BudgetedModelClient(Client(), child).stream([]):
                pass
        assert root.model_calls == 2
        assert root.usage == TokenUsage(8, 4)
        assert root.request_counts == {'subagent/model': 2}
        with pytest.raises(BudgetExhausted):
            async for _ in BudgetedModelClient(Client(), children[0]).stream([]):
                pass
        assert root.model_calls == 2
    asyncio.run(run())


def test_child_tool_and_deadline_limits_include_parent():
    root = TurnState(max_tool_calls=2, max_seconds=1)
    child = TurnState(parent=root)
    root.record_tool_request()  # The exploration tool itself.
    assert child.can_request_tool_batch(1)
    child.record_tool_request()
    assert not child.can_request_tool_batch(1)
    root.started_at -= 2
    assert child.budget_reason() == 'time_budget_exhausted'
