import asyncio

import pytest

from forge.runtime.model_budget import BudgetedModelClient
from forge.runtime.request_snapshot import RequestSnapshot
from forge.runtime.state import ModelResponseCompleted
from forge.runtime.turn_state import TurnState


def test_snapshot_is_frozen_reconstructible_and_excludes_client_credentials():
    class Client:
        model = 'test-model'
        api_key = 'never-store-this'
    messages = [{'role': 'user', 'content': 'original'}]
    snapshot = RequestSnapshot.capture(messages=messages, tools=[], system='rules', client=Client())
    messages[0]['content'] = 'changed later'
    assert snapshot.payload['messages'][0]['content'] == 'original'
    assert 'never-store-this' not in snapshot.payload_json
    assert RequestSnapshot.restore(snapshot.as_dict()) == snapshot
    damaged = snapshot.as_dict()
    damaged['request']['system'] = 'tampered'
    with pytest.raises(ValueError, match='digest'):
        RequestSnapshot.restore(damaged)


def test_recorded_snapshot_is_the_actual_input_despite_callback_mutation():
    async def run():
        original = [{'role': 'user', 'content': 'original'}]
        events = []
        class Client:
            async def stream(self, messages, **kwargs):
                assert messages[0]['content'] == 'original'
                yield ModelResponseCompleted('end_turn')
        def record(kind, payload):
            events.append((kind, payload))
            original[0]['content'] = 'modified by observer'
        state = TurnState(request_event_sink=record)
        async for _ in BudgetedModelClient(Client(), state).stream(original):
            pass
        snapshot = next(payload for kind, payload in events if kind == 'model_input_snapshot')
        attempt = next(payload for kind, payload in events if kind == 'model_request_started')
        assert attempt['input_sha256'] == snapshot['sha256']
        assert state.model_calls == 1
    asyncio.run(run())


def test_wire_audit_matches_native_request_and_marks_missing_usage():
    import json
    from hashlib import sha256
    import httpx
    from openai import AsyncOpenAI
    from forge.config import ForgeConfig
    from forge.runtime.model_budget import observe_wire_request
    from forge.runtime.providers import NativeModelClient

    async def run():
        records, sent = [], []
        def handler(request):
            sent.append(request.content)
            response = dict(id='r', object='chat.completion.chunk', created=1, model='test',
                choices=[dict(index=0, delta={'content': 'done'}, finish_reason='stop')])
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                text='data: ' + json.dumps(response) + '\n\ndata: [DONE]\n\n')
        async with AsyncOpenAI(api_key='secret-not-in-audit', base_url='https://offline.invalid/v1?token=private',
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler),
                event_hooks={'request': [observe_wire_request]})) as sdk:
            adapter = NativeModelClient(ForgeConfig(api_key='offline', model_id='test', provider='deepseek'), client=sdk)
            state = TurnState(request_event_sink=lambda kind, data: records.append((kind, data)))
            events = [event async for event in BudgetedModelClient(adapter, state).stream(
                [{'role': 'user', 'content': 'hello'}])]
        assert any(isinstance(event, ModelResponseCompleted) for event in events)
        wire = next(data for kind, data in records if kind == 'model_wire_snapshot')
        attempt = next(data for kind, data in records if kind == 'model_request_started')
        assert wire['body_utf8'].encode() == sent[0]
        assert wire['body_sha256'] == sha256(sent[0]).hexdigest()
        assert wire['request_id'] == attempt['request_id']
        assert wire['input_sha256'] == attempt['input_sha256']
        assert 'secret-not-in-audit' not in json.dumps(records)
        assert 'private' not in json.dumps(records)
        assert state.unknown_usage_requests == 1
    asyncio.run(run())
