import asyncio
import base64

import pytest

from forge.messages import image_block, normalize_input, MAX_TEXT_BYTES
from forge.runtime.agent_loop import Conversation
from forge.runtime.providers import responses_input, chat_messages
from forge.runtime.state import ModelTextDelta, ModelResponseCompleted, ToolCall, build_tool_result_message
from forge.tools.base import ToolResult

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


def test_multimodal_input_is_detached_and_reaches_responses():
    original = [{'type': 'text', 'text': 'Inspect'}, image_block(PNG)]
    validated = normalize_input(original)
    original[0]['text'] = 'changed'
    wire = responses_input([{'role': 'user', 'content': validated}])
    assert wire[0]['content'] == 'Inspect'
    assert wire[1]['content'][0]['image_url'].startswith('data:image/png;base64,')
    with pytest.raises(Exception, match='Unsupported message part'):
        chat_messages([{'role': 'user', 'content': validated}])


def test_input_limits_and_media_validation():
    with pytest.raises(ValueError, match='256 KiB'):
        normalize_input('a' * (MAX_TEXT_BYTES + 1))
    with pytest.raises(ValueError, match='budget'):
        normalize_input([image_block(PNG) for _ in range(9)])
    with pytest.raises(ValueError, match='matching'):
        image_block(PNG, 'image/jpeg')


def test_any_tool_can_return_visual_blocks_without_name_special_cases():
    result = ToolResult.ok('screenshot', metadata={'content_blocks': [image_block(PNG)]})
    message = build_tool_result_message([(ToolCall(0, 'shot', 'mcp_screenshot', {}), result)])
    body = message['content'][0]['content']
    assert body[1]['type'] == 'image'
    assert 'base64' not in body[0]['text']


def test_legacy_message_builders_preserve_order_and_failure_payload():
    import json
    from forge.runtime.agent_loop import build_assistant_message, build_tool_result_message as legacy_results

    first, second = ToolCall(0, 'first', 'read_file', {'path': 'a.py'}), ToolCall(1, 'second', 'write_file', {})
    assistant = build_assistant_message('Inspect first', [second, first])
    assert [block['id'] for block in assistant['content'][1:]] == ['first', 'second']
    result = ToolResult.fail('permission_denied', 'Write requires approval')
    message = legacy_results([(second, result)])
    assert message == build_tool_result_message([(second, result)])
    assert message['content'][0]['is_error'] is True
    assert json.loads(message['content'][0]['content'])['error']['code'] == 'permission_denied'


def test_conversation_accepts_image_only_and_rejects_text_only_adapter(tmp_path):
    class Model:
        provider = 'offline'
        async def stream(self, messages, **kwargs):
            assert messages[-1]['content'][0]['type'] == 'image'
            yield ModelTextDelta('visible')
            yield ModelResponseCompleted('end_turn')

    async def run():
        model = Model()
        conversation = Conversation(client=model, context_root=tmp_path)
        events = [event async for event in conversation.stream([image_block(PNG)])]
        assert events[-1].result.text == 'visible'
        model.provider = 'deepseek'
        with pytest.raises(ValueError, match='does not support image'):
            async for _ in conversation.stream([image_block(PNG)]):
                pass
    asyncio.run(run())


def test_cli_image_mention_and_mcp_image_share_the_same_wire_content(tmp_path):
    from forge.cli import expand_file_mentions
    from forge.mcp.tool import mcp_result_to_tool_result
    from mcp import types
    (tmp_path / 'screen.png').write_bytes(PNG)
    attachment = expand_file_mentions('Inspect @screen.png', tmp_path)
    assert attachment[1] == image_block(PNG)
    result = mcp_result_to_tool_result(types.CallToolResult(content=[types.ImageContent(
        type='image', mimeType='image/png', data=base64.b64encode(PNG).decode())]),
        server_name='offline', remote_name='screenshot', exposed_name='mcp_screenshot')
    message = build_tool_result_message([(ToolCall(0, 'mcp-image', 'mcp_screenshot', {}), result)])
    assert message['content'][0]['content'][1] == attachment[1]


def test_integration_environment_expansion_is_workspace_scoped(tmp_path, monkeypatch):
    import json
    import os
    from forge.integrations import load_runtime_mcp_servers
    monkeypatch.delenv('FORGE_PROBE_ENDPOINT', raising=False)
    (tmp_path / '.env').write_text('FORGE_PROBE_ENDPOINT=https://offline.invalid/mcp\n')
    (tmp_path / '.mcp.json').write_text(json.dumps({'mcpServers': {
        'probe': {'type': 'http', 'url': '${FORGE_PROBE_ENDPOINT}'}}}))
    assert load_runtime_mcp_servers(tmp_path)['probe'].url == 'https://offline.invalid/mcp'
    assert 'FORGE_PROBE_ENDPOINT' not in os.environ
