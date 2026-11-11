"""Malformed tool JSON is observed, rejected, and corrected without execution."""
import asyncio
import json

import httpx
import pytest
from anthropic import AsyncAnthropic

from forge.api import AgentDefinition, HarnessSession, RunProfile, SessionOptions
from forge.config import ForgeConfig
from forge.runtime.model_client import AnthropicModelClient
from forge.runtime.state import ModelProtocolError, ModelToolCallArgumentsDelta
from forge.sessions.trajectory import journal_events


def response(chunks, *, final=False):
    events = [{'type': 'message_start', 'message': {'id': 'msg', 'type': 'message', 'role': 'assistant',
        'model': 'offline', 'content': [], 'stop_reason': None, 'stop_sequence': None,
        'usage': {'input_tokens': 10, 'output_tokens': 0}}}]
    if final:
        events += [{'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': 'Done.'}},
                   {'type': 'content_block_stop', 'index': 0}]
    else:
        events += [{'type': 'content_block_start', 'index': 0,
                    'content_block': {'type': 'thinking', 'thinking': '', 'signature': ''}},
                   {'type': 'content_block_delta', 'index': 0,
                    'delta': {'type': 'thinking_delta', 'thinking': 'Create the requested file.'}},
                   {'type': 'content_block_delta', 'index': 0,
                    'delta': {'type': 'signature_delta', 'signature': 'opaque'}},
                   {'type': 'content_block_stop', 'index': 0},
                   {'type': 'content_block_start', 'index': 1,
                    'content_block': {'type': 'tool_use', 'id': 'write', 'name': 'write_file', 'input': {}}}]
        events += [{'type': 'content_block_delta', 'index': 1,
                    'delta': {'type': 'input_json_delta', 'partial_json': chunk}} for chunk in chunks]
        events.append({'type': 'content_block_stop', 'index': 1})
    events += [{'type': 'message_delta', 'delta': {'stop_reason': 'end_turn' if final else 'tool_use',
                 'stop_sequence': None}, 'usage': {'output_tokens': 8}}, {'type': 'message_stop'}]
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
        text=''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events))


def test_raw_sdk_transport_preserves_invalid_fragment_until_block_end():
    chunks = ['{"path":"out.txt","content":', 'not_json', '}']
    async def run():
        async with AsyncAnthropic(api_key='offline', http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: response(chunks)))) as sdk:
            client = AnthropicModelClient('offline', client=sdk, max_retries=0)
            events = []
            with pytest.raises(ModelProtocolError) as raised:
                async for event in client.stream([], tools=[{'name': 'write_file'}]):
                    events.append(event)
            assert raised.value.reason == 'invalid_tool_arguments'
            assert [e.partial_json for e in events if isinstance(e, ModelToolCallArgumentsDelta)] == chunks
    asyncio.run(run())


def test_malformed_sse_is_a_protocol_error_not_an_unhandled_decoder_error():
    async def run():
        async with AsyncAnthropic(api_key='offline', http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200,
                headers={'content-type': 'text/event-stream'}, text='event: message_start\ndata: {broken\n\n')))) as sdk:
            client = AnthropicModelClient('offline', client=sdk, max_retries=0)
            with pytest.raises(ModelProtocolError) as raised:
                async for _ in client.stream([]):
                    pass
            assert raised.value.reason == 'invalid_response_json'
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['corrected', 'repeated', 'budget'])
def test_protocol_correction_is_explicit_bounded_and_never_executes_invalid_input(tmp_path, mode):
    requests = []
    project = tmp_path / 'project'
    project.mkdir()
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) > 1:
            assert 'No tool was executed' in json.dumps(body['messages'])
        if mode == 'repeated' or len(requests) == 1:
            assert not (project / 'out.txt').exists()
            return response(['{"path":"out.txt","content":', 'INVALID', '}'])
        if len(requests) == 2:
            assert not (project / 'out.txt').exists()
            return response(['{"path":"out.txt","content":"one write"}'])
        assert (project / 'out.txt').read_text() == 'one write'
        return response([], final=True)
    def factory(config, **kwargs):
        sdk = AsyncAnthropic(api_key='offline', max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        return AnthropicModelClient(config.model_id, config=config, client=sdk, max_retries=0)
    async def run():
        options = SessionOptions(project, data_root=tmp_path / 'data',
            config=ForgeConfig(api_key='offline', model_id='offline', max_tokens=1024),
            profile=RunProfile(max_model_calls=1 if mode == 'budget' else 4, max_tool_calls=3),
            agent=AgentDefinition(tools=('write_file',)))
        async with await HarnessSession.open(options, model_client_factory=factory) as session:
            result = await session.run('Create out.txt with one write.')
            records = journal_events(session.journal.path)
            assert any(e['type'] == 'model_tool_chunk' and 'INVALID' in e['payload'].get('partial_json', '') for e in records)
            executed = [e for e in records if e['type'] == 'tool_completed' and e['payload']['status'] == 'executed']
            if mode == 'corrected':
                assert result.status == 'completed' and len(requests) == 3 and len(executed) == 1
            else:
                assert result.status == 'failed' and not (project / 'out.txt').exists() and not executed
                assert len(requests) == (1 if mode == 'budget' else 3)
            assert result.model_calls == len(requests)
    asyncio.run(run())
