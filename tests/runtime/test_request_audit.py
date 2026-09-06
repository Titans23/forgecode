import asyncio

from forge.runtime.model_budget import BudgetedModelClient
from forge.runtime.model_client import ModelCallError
from forge.runtime.state import ModelRetryScheduled, ModelTextDelta, ModelUsageUpdate, TokenUsage
from forge.runtime.turn_state import TurnState


def test_actual_attempt_lifecycle_survives_failure_without_event_consumer():
    events = []
    state = TurnState(max_model_calls=4)
    state.request_event_sink = lambda kind, payload: events.append((kind, payload))

    class Client:
        observes_request_budget = True

        async def stream(self, **kwargs):
            from forge.runtime.model_budget import request_observer
            request_observer.get()()
            yield ModelUsageUpdate(TokenUsage(3, 1))
            yield ModelRetryScheduled(2, 'server_error', 0)
            request_observer.get()()
            yield ModelTextDelta('partial')
            raise ModelCallError('stream_interrupted', 'interrupted', retryable=False)

    async def run():
        try:
            async for _ in BudgetedModelClient(Client(), state, 'compaction').stream([]):
                pass
        except ModelCallError:
            pass
    asyncio.run(run())
    starts = [p for k, p in events if k == 'model_request_started']
    ends = [p for k, p in events if k == 'model_request_finished']
    assert len(starts) == len(ends) == 2
    assert {p['request_id'] for p in starts} == {p['request_id'] for p in ends}
    assert ends[0]['outcome'] == 'retrying'
    assert ends[1]['reason'] == 'stream_interrupted'
    assert ends[1]['text_characters'] == 7
    assert starts[0]['stage'] == 'compaction'
    assert state.model_calls == 2 and state.usage.input_tokens == 3
