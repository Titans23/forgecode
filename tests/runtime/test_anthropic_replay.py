"""Actual SDK stream assembly, tool continuation and Journal restore."""
import asyncio
import json

from anthropic import AsyncAnthropic
import httpx

from forge.api import AgentDefinition, HarnessSession, SessionOptions
from forge.config import ForgeConfig
from forge.runtime.model_client import AnthropicModelClient


def test_signed_thinking_survives_tool_continuation_and_session_resume(tmp_path):
    requests = []
    original = [
        {'type': 'thinking', 'thinking': '检查目录。', 'signature': 'signature-complete'},
        {'type': 'text', 'text': 'Checking.'},
        {'type': 'redacted_thinking', 'data': 'opaque'},
        {'type': 'tool_use', 'id': 'list-1', 'name': 'list_directory', 'input': {'path': '.'}},
    ]
    def handler(request):
        requests.append(json.loads(request.content))
        first = len(requests) == 1
        blocks = original if first else [
            {'type': 'thinking', 'thinking': '完成。', 'signature': 'final-signature'},
            {'type': 'text', 'text': 'Done.'},
        ]
        events = [{'type': 'message_start', 'message': {'id': 'msg-' + str(len(requests)),
            'type': 'message', 'role': 'assistant', 'model': 'test', 'content': [],
            'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 20, 'output_tokens': 0}}}]
        for i, block in enumerate(blocks):
            start = dict(block)
            if block['type'] == 'thinking':
                start['thinking'] = ''
                start.pop('signature')  # Some compatible endpoints send it only in deltas.
            events.append({'type': 'content_block_start', 'index': i, 'content_block': start})
            if block['type'] == 'thinking':
                events.append({'type': 'content_block_delta', 'index': i,
                    'delta': {'type': 'thinking_delta', 'thinking': block['thinking']}})
                # Signatures can arrive separately, after all visible text.
                for fragment in [block['signature'][:4], block['signature'][4:]]:
                    events.append({'type': 'content_block_delta', 'index': i,
                        'delta': {'type': 'signature_delta', 'signature': fragment}})
            events.append({'type': 'content_block_stop', 'index': i})
        events.extend([{'type': 'message_delta', 'delta': {'stop_reason': 'tool_use' if first else 'end_turn',
            'stop_sequence': None}, 'usage': {'output_tokens': 10}}, {'type': 'message_stop'}])
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            text=''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events))

    def factory(config, **kwargs):
        sdk = AsyncAnthropic(api_key='offline', max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        return AnthropicModelClient(model=config.model_id, config=config, client=sdk, max_retries=0)

    async def run():
        project = tmp_path / 'project'
        project.mkdir()
        opts = SessionOptions(project, data_root=tmp_path / 'data',
            config=ForgeConfig(api_key='offline', model_id='test', max_tokens=1024),
            agent=AgentDefinition(tools=('list_directory',)))
        async with await HarnessSession.open(opts, model_client_factory=factory) as session:
            result = await session.run('List this directory.')
            assert result.status == 'completed' and result.text == 'Done.'
            assert result.model_calls == 2 and tuple(c.name for c in result.tool_calls) == ('list_directory',)
            identity = session.session_id
            assert next(m for m in requests[1]['messages'] if m['role'] == 'assistant')['content'] == original
        async with await HarnessSession.open(opts, model_client_factory=factory, resume_identifier=identity) as resumed:
            assert (await resumed.run('Continue.')).text == 'Done.'
            assistants = [m for m in requests[2]['messages'] if m['role'] == 'assistant']
            assert assistants[0]['content'] == original
            assert assistants[1]['content'][0]['signature'] == 'final-signature'
        assert len(requests) == 3
    asyncio.run(run())
