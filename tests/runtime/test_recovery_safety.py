import asyncio
import json

from forge.runtime.agent_loop import Conversation
from forge.runtime.model_client import ModelProtocolError
from forge.runtime.state import (ModelToolCallStarted, ModelToolCallArgumentsDelta,
    ModelTextDelta, ModelUsageUpdate, TokenUsage, TurnCompleted)
from forge.sessions.store import SessionStore


class Client:
    provider = 'offline'

    def __init__(self, events):
        self.events = events
        self.calls = 0

    async def stream(self, **kwargs):
        self.calls += 1
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event


def test_partial_arguments_are_never_automatically_replayed(tmp_path):
    client = Client([
        ModelUsageUpdate(TokenUsage(5, 3)),
        ModelToolCallStarted(0, 'partial', 'write_file'),
        ModelToolCallArgumentsDelta(0, '{"path":"out.txt"'),
        ModelProtocolError('missing stop', reason='incomplete_tool_call'),
    ])
    from forge.tools import create_default_registry
    store = SessionStore(tmp_path, data_root=tmp_path / 'sessions')
    journal = store.create(model='offline')
    conversation = Conversation(client=client, registry=create_default_registry(tmp_path),
                                context_root=tmp_path, session_journal=journal)

    async def run():
        return [event async for event in conversation.stream('Create out.txt')]
    events = asyncio.run(run())
    assert client.calls == 1
    assert not (tmp_path / 'out.txt').exists()
    final = next(e.result for e in events if isinstance(e, TurnCompleted))
    assert final.stop_reason == 'incomplete_tool_call'
    history = [json.loads(line) for line in journal.path.read_text(encoding='utf-8').splitlines()]
    attempts = [e for e in history if e['type'] == 'model_request_finished']
    assert len(attempts) == 1
    assert attempts[0]['payload']['tool_blocks']['0']['complete'] is False
    assert attempts[0]['payload']['tool_blocks']['0']['argument_characters'] > 0


def test_partial_text_protocol_error_is_not_replayed(tmp_path):
    client = Client([ModelUsageUpdate(TokenUsage(5, 3)), ModelTextDelta('partial'),
                     ModelProtocolError('missing stop', reason='stream_termination_missing')])
    conversation = Conversation(client=client, context_root=tmp_path)

    async def run():
        return [e async for e in conversation.stream('Explain')]
    events = asyncio.run(run())
    assert client.calls == 1
    assert events[-1].result.status == 'failed'
    assert any(m.get('content') == 'partial' for m in conversation.messages)
