'''Account for actual provider attempts across routing, compaction and turns.'''

from __future__ import annotations

from contextvars import ContextVar
from time import monotonic
from typing import Callable
from uuid import uuid4
from dataclasses import asdict
import asyncio

from forge.runtime.state import (ModelUsageUpdate, ModelRetryScheduled, ModelTextDelta,
    ModelToolCallStarted, ModelToolCallArgumentsDelta, ModelToolCallCompleted, ModelResponseCompleted)
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
        request = None
        request_started = started

        def emit(kind, payload):
            if self.state.request_event_sink is not None:
                self.state.request_event_sink(kind, payload)

        def end_request(outcome, reason=''):
            nonlocal request
            if request is not None:
                emit('model_request_finished', {
                    **request, 'outcome': outcome, 'reason': reason,
                    'duration_seconds': monotonic() - request_started,
                    'usage': asdict(usage) if usage is not None else None,
                })
                request = None

        def begin_request() -> None:
            nonlocal usage, request, request_started
            end_request('indeterminate', 'missing_attempt_boundary')
            if usage is not None:
                self.state.record_usage(usage)
                usage = None
            reason = self.state.budget_reason()
            if reason:
                raise BudgetExhausted(reason)
            self.state.record_model_request(stage=self.stage)
            request_started = monotonic()
            request = dict(request_id=uuid4().hex, ordinal=self.state.model_calls,
                           stage=self.stage, text_characters=0, tool_blocks={}, stop_reason=None)
            emit('model_request_started', dict(request))

        observer_token = request_observer.set(begin_request)
        try:
            if not getattr(self.client, 'observes_request_budget', False):
                begin_request()
            async for event in self.client.stream(messages=messages, tools=tools, system=system):
                if isinstance(event, ModelUsageUpdate):
                    # Provider events contain cumulative usage for this request.
                    usage = event.request_usage or event.usage
                if request is not None:
                    if isinstance(event, ModelTextDelta):
                        request['text_characters'] += len(event.text)
                    elif isinstance(event, ModelToolCallStarted):
                        request['tool_blocks'][str(event.index)] = {'id': event.id, 'name': event.name, 'argument_characters': 0, 'complete': False}
                    elif isinstance(event, ModelToolCallArgumentsDelta):
                        block = request['tool_blocks'].setdefault(str(event.index), {'complete': False, 'argument_characters': 0})
                        block['argument_characters'] += len(event.partial_json)
                    elif isinstance(event, ModelToolCallCompleted):
                        block = request['tool_blocks'].setdefault(str(event.tool_call.index), {})
                        block.update(id=event.tool_call.id, name=event.tool_call.name, complete=True)
                    elif isinstance(event, ModelResponseCompleted):
                        request['stop_reason'] = event.stop_reason
                    elif isinstance(event, ModelRetryScheduled):
                        end_request('retrying', event.reason)
                yield event
            end_request('completed')
        except (asyncio.CancelledError, GeneratorExit):
            end_request('cancelled', 'request_cancelled')
            raise
        except BaseException as error:
            end_request('failed', getattr(error, 'reason', type(error).__name__))
            raise
        finally:
            end_request('indeterminate', 'stream_closed')
            if usage is not None:
                self.state.record_usage(usage)
            self.state.add_time(self.stage, monotonic() - started)
            request_observer.reset(observer_token)
