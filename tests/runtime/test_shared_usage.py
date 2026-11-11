import asyncio

import pytest

from forge.runtime.model_observation import ObservedModelClient
from forge.runtime.state import ModelResponseCompleted, ModelUsageUpdate, TokenUsage
from forge.runtime.turn_state import TurnState


def test_child_requests_and_usage_accumulate_without_a_shared_ceiling():
    async def run():
        root = TurnState()
        children = [TurnState(parent=root), TurnState(parent=root)]
        class Client:
            async def stream(self, **kwargs):
                yield ModelUsageUpdate(TokenUsage(4, 2))
                yield ModelResponseCompleted('end_turn')
        for child in children * 100:
            async for _ in ObservedModelClient(Client(), child).stream([]):
                pass
        assert root.model_calls == 200
        assert root.usage == TokenUsage(800, 400)
        assert root.request_counts == {'subagent/model': 200}
        assert all(child.model_calls == 100 for child in children)
    asyncio.run(run())


def test_child_tool_usage_accumulates_after_a_long_turn():
    root = TurnState()
    child = TurnState(parent=root)
    root.record_tool_request()  # The exploration tool itself.
    root.started_at -= 7200
    for _ in range(250):
        child.record_tool_request()
    assert root.tool_requests == 251 and child.tool_requests == 250
    assert root.statistics()['elapsed_seconds'] >= 7200
