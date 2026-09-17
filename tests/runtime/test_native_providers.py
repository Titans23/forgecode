import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI

from forge.config import ForgeConfig, ConfigurationError
from forge.runtime.model_client import ModelProtocolError
from forge.runtime.providers import NativeModelClient, chat_messages, responses_input
from forge.runtime.state import ModelToolCallCompleted, ModelProviderState, ModelUsageUpdate


def chunks_response(events):
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
        text=''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n')


@pytest.mark.parametrize('provider,url', [
    ('anthropic', 'https://api.anthropic.com'),
    ('deepseek', 'https://api.deepseek.com'),
    ('openai_responses', 'https://api.openai.com/v1'),
])
def test_direct_config_uses_provider_default_url(provider, url):
    assert ForgeConfig(api_key='offline', model_id='test', provider=provider).base_url == url


def chat_chunk(delta=None, finish=None, usage=None):
    return dict(id='response-1', object='chat.completion.chunk', created=1, model='test',
        choices=[] if usage else [dict(index=0, delta=delta or {}, finish_reason=finish)], usage=usage)


def run_stream(provider, chunks):
    async def run():
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return chunks_response(chunks)
        async with AsyncOpenAI(api_key='offline-test', base_url='https://offline.invalid/v1',
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as sdk:
            config = ForgeConfig(api_key='offline-test', model_id='test', provider=provider,
                                 base_url='https://offline.invalid/v1')
            adapter = NativeModelClient(config, client=sdk)
            events = [event async for event in adapter.stream([{'role': 'user', 'content': 'test'}],
                tools=[{'name': 'read', 'input_schema': {'type': 'object'}}], system='rules')]
        return events, requests
    return asyncio.run(run())


def test_deepseek_stream_reassembles_arguments_and_preserves_reasoning():
    events, requests = run_stream('deepseek', [
        chat_chunk({'reasoning_content': 'opaque state'}),
        chat_chunk({'tool_calls': [{'index': 0, 'id': 'call-1', 'type': 'function',
                                   'function': {'name': 'read', 'arguments': '{"path":'}}]}),
        chat_chunk({'tool_calls': [{'index': 0, 'function': {'arguments': '"a.py"}'}}]}, 'tool_calls'),
        chat_chunk(usage={'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110,
                          'prompt_cache_hit_tokens': 60}),
    ])
    call = next(e.tool_call for e in events if isinstance(e, ModelToolCallCompleted))
    assert call.arguments == {'path': 'a.py'}
    state = next(e for e in events if isinstance(e, ModelProviderState))
    history = [{'role': 'assistant', 'content': [{'type': 'tool_use', 'id': call.id,
        'name': call.name, 'input': call.arguments}],
        'provider_state': {'provider': state.provider, 'data': state.data}}]
    assert chat_messages(history)[0]['reasoning_content'] == 'opaque state'
    usage = next(e.usage for e in events if isinstance(e, ModelUsageUpdate))
    assert usage.total_input_tokens == 100
    assert usage.cache_read_input_tokens == 60
    assert requests[0]['tools'][0]['function']['name'] == 'read'


@pytest.mark.parametrize('chunks', [
    [chat_chunk({'content': 'partial'})],
    [chat_chunk({'tool_calls': [{'index': 0, 'id': 'call-1', 'function': {
        'name': 'read', 'arguments': '{"path":'}}]}, 'tool_calls')],
])
def test_deepseek_rejects_incomplete_stream_or_tool_json(chunks):
    with pytest.raises(ModelProtocolError):
        run_stream('deepseek', chunks)


def test_responses_native_call_and_encrypted_state_round_trip():
    reason = {'id': 'rs-1', 'type': 'reasoning', 'summary': [], 'encrypted_content': 'opaque'}
    final = dict(id='resp-1', object='response', created_at=1, status='completed', model='test',
        output=[reason, {'id': 'fc-1', 'type': 'function_call', 'status': 'completed',
                        'call_id': 'call-1', 'name': 'read', 'arguments': '{"path":"b.py"}'}],
        usage={'input_tokens': 100, 'output_tokens': 5, 'total_tokens': 105,
               'input_tokens_details': {'cached_tokens': 20}, 'output_tokens_details': {'reasoning_tokens': 2}})
    events, requests = run_stream('openai_responses', [{'type': 'response.completed', 'response': final}])
    call = next(e.tool_call for e in events if isinstance(e, ModelToolCallCompleted))
    assert call.arguments == {'path': 'b.py'}
    state = next(e for e in events if isinstance(e, ModelProviderState))
    restored = responses_input([{'role': 'assistant', 'content': [],
        'provider_state': {'provider': state.provider, 'data': state.data}}])
    assert restored[0]['encrypted_content'] == 'opaque'
    assert requests[0]['store'] is False
    assert requests[0]['tools'][0]['parameters'] == {'type': 'object'}
    assert next(e.usage for e in events if isinstance(e, ModelUsageUpdate)).total_input_tokens == 100


def test_responses_missing_terminal_does_not_submit_a_tool():
    with pytest.raises(ModelProtocolError, match='Missing completed'):
        run_stream('openai_responses', [{'type': 'response.output_text.delta', 'delta': 'unfinished',
                                       'item_id': 'item-1', 'output_index': 0, 'content_index': 0}])


def test_explicit_provider_selects_credentials_and_endpoint_without_guessing():
    config = ForgeConfig.from_env({'FORGE_PROVIDER': 'deepseek', 'FORGE_MODEL': 'custom-name',
                                   'DEEPSEEK_API_KEY': 'test'})
    assert config.base_url == 'https://api.deepseek.com'
    assert config.provider == 'deepseek'
    with pytest.raises(ConfigurationError, match='provider'):
        ForgeConfig(api_key='test', model_id='test', provider='guess-from-model')
