'''Account for actual provider attempts across routing, compaction and turns.'''

from __future__ import annotations

from contextvars import ContextVar
from time import monotonic
from typing import Callable

from forge.runtime.state import ModelUsageUpdate
from forge.runtime.turn_state import BudgetExhausted, TurnState


request_observer: ContextVar[Callable[[], None] | None] = ContextVar(
    'forge_model_request_observer', default=None,
)


class BudgetedModelClient:
    '''A turn-scoped adapter; the provider invokes the observer per HTTP attempt.

    Older embedded clients have no retry notification boundary, so one stream
    invocation is one request for them. No model request is replayed here.
    '''

    def __init__(self, client, state: TurnState, stage: str = 'model') -> None:
        self.client = client
        self.state = state
        self.stage = stage

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def stream(self, messages, tools=None, system=None):
        usage = None
        started = monotonic()

        def begin_request() -> None:
            nonlocal usage
            if usage is not None:
                self.state.record_usage(usage)
                usage = None
            reason = self.state.budget_reason()
            if reason:
                raise BudgetExhausted(reason)
            self.state.record_model_request(stage=self.stage)

        observer_token = request_observer.set(begin_request)
        try:
            if not getattr(self.client, 'observes_request_budget', False):
                begin_request()
            async for event in self.client.stream(messages=messages, tools=tools, system=system):
                if isinstance(event, ModelUsageUpdate):
                    # Provider events contain cumulative usage for this request.
                    usage = event.request_usage or event.usage
                yield event
        finally:
            if usage is not None:
                self.state.record_usage(usage)
            self.state.add_time(self.stage, monotonic() - started)
            request_observer.reset(observer_token)
