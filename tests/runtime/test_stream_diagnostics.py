"""Streaming stays immediate while durable diagnostic writes are coalesced."""
import asyncio
from contextlib import aclosing

import pytest

from forge.runtime import model_budget
from forge.runtime.model_budget import BudgetedModelClient
from forge.runtime.state import ModelTextDelta, ModelToolCallStarted, ModelToolCallArgumentsDelta
from forge.runtime.turn_state import TurnState
from forge.sessions.store import SessionStore
from forge.sessions.trajectory import journal_events


@pytest.mark.parametrize('ending', ['complete', 'cancel', 'error', 'consumer_close'])
def test_coalesced_stream_retains_content_and_flushes_on_every_exit(tmp_path, monkeypatch, ending):
    monkeypatch.setattr(model_budget, 'monotonic', lambda: 1.0)
    journal = SessionStore(tmp_path, data_root=tmp_path / 'data').create(model='offline')
    writes = []
    def record(kind, payload):
        writes.append(kind)
        journal.append(kind, payload)
    state = TurnState(max_model_calls=1, request_event_sink=record)
    fragments = ['中' * 7] * 3000
    class Client:
        async def stream(self, **kwargs):
            yield ModelTextDelta('Before.')
            yield ModelToolCallStarted(0, 'call', 'write_file')
            for fragment in fragments:
                yield ModelToolCallArgumentsDelta(0, fragment)
            if ending == 'cancel':
                raise asyncio.CancelledError
            if ending == 'error':
                raise RuntimeError('offline stream failure')
    async def run():
        seen = 0
        try:
            async with aclosing(BudgetedModelClient(Client(), state).stream([])) as stream:
                async for event in stream:
                    if isinstance(event, ModelToolCallArgumentsDelta):
                        seen += 1
                        # Chunks reach consumers before the aggregate is persisted.
                        if seen == 1:
                            assert writes.count('model_tool_chunk') == 1  # start only
                        if ending == 'consumer_close' and seen == len(fragments):
                            break
        except (RuntimeError, asyncio.CancelledError):
            assert ending in ('cancel', 'error')
        assert seen == len(fragments)
    asyncio.run(run())
    records = journal_events(journal.path)
    chunks = [e['payload'] for e in records if e['type'] == 'model_tool_chunk' and 'partial_json' in e['payload']]
    assert ''.join(e['partial_json'] for e in chunks) == ''.join(fragments)
    assert sum(e['delta_count'] for e in chunks) == len(fragments)
    assert len(chunks) == 4
    assert records[-1]['type'] == 'model_request_finished'
    assert records[-1]['payload']['outcome'] == ('completed' if ending == 'complete' else
        'failed' if ending == 'error' else 'cancelled')
    assert state.model_calls == 1


def test_stream_diagnostics_flush_while_fragments_arrive(monkeypatch):
    now = 0.0
    monkeypatch.setattr(model_budget, 'monotonic', lambda: now)
    events = []
    class Client:
        async def stream(self, **kwargs):
            nonlocal now
            yield ModelTextDelta('first')
            assert not any(k == 'model_request_chunk' for k, _ in events)
            now = 1.1
            yield ModelTextDelta(' second')
            assert next(p for k, p in events if k == 'model_request_chunk')['text'] == 'first second'
    async def run():
        state = TurnState(request_event_sink=lambda k, p: events.append((k, p)))
        async for _ in BudgetedModelClient(Client(), state).stream([]):
            pass
    asyncio.run(run())
