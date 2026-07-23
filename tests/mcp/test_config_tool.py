'''Configuration and adapter tests for ForgeCode MCP support.'''

import asyncio
import json
from pathlib import Path

from mcp import types

from forge.mcp.config import load_mcp_servers
from forge.mcp.tool import (
    MAX_MCP_RESULT_CHARACTERS,
    MCPToolAdapter,
    mcp_result_to_tool_result,
    mcp_tool_name,
)


class FakeManager:
    async def call_tool(self, *args, **kwargs):
        from forge.tools.base import ToolResult

        return ToolResult.ok('called')


def test_mcp_configuration_merges_project_over_user(
    tmp_path: Path,
    monkeypatch,
) -> None:
    user = tmp_path / 'user.json'
    project = tmp_path / '.mcp.json'
    monkeypatch.setenv('MCP_TEST_COMMAND', 'python')
    user.write_text(
        json.dumps(
            {
                'mcpServers': {
                    'shared': {
                        'type': 'stdio',
                        'command': 'old-command',
                    },
                    'user-only': {
                        'type': 'http',
                        'url': 'https://example.test/mcp',
                    },
                }
            }
        ),
        encoding='utf-8',
    )
    project.write_text(
        json.dumps(
            {
                'mcpServers': {
                    'shared': {
                        'command': '${MCP_TEST_COMMAND}',
                        'args': ['server.py'],
                    }
                }
            }
        ),
        encoding='utf-8',
    )

    servers = load_mcp_servers(tmp_path, user_path=user)

    assert set(servers) == {'shared', 'user-only'}
    assert servers['shared'].command == 'python'
    assert servers['shared'].args == ('server.py',)


def test_mcp_tool_adapter_validates_json_schema(tmp_path: Path) -> None:
    adapter = MCPToolAdapter(
        tmp_path,
        manager=FakeManager(),
        server_name='demo',
        remote_tool=types.Tool(
            name='echo',
            description='Echo text.',
            inputSchema={
                'type': 'object',
                'properties': {'message': {'type': 'string'}},
                'required': ['message'],
                'additionalProperties': False,
            },
        ),
    )

    invalid = asyncio.run(adapter.run({'message': 3}))
    valid = asyncio.run(adapter.run({'message': 'hello'}))

    assert adapter.name == 'mcp__demo__echo'
    assert adapter.provenance == {
        'source': 'mcp',
        'server': 'demo',
        'remote_tool': 'echo',
    }
    assert invalid.error is not None
    assert invalid.error.code == 'invalid_arguments'
    assert valid.success
    request = adapter.permission_request({'message': 'hello'})
    assert request.capability == 'mcp.call'
    assert request.risk == 'high'


def test_mcp_result_conversion_preserves_source_and_bounds_output() -> None:
    result = mcp_result_to_tool_result(
        types.CallToolResult(
            content=[
                types.TextContent(
                    type='text',
                    text='x' * (MAX_MCP_RESULT_CHARACTERS + 10),
                )
            ],
            structuredContent={'ok': True},
        ),
        server_name='demo',
        remote_name='large',
        exposed_name='mcp__demo__large',
    )

    assert result.success
    assert result.metadata['source'] == 'mcp'
    assert result.metadata['truncated'] is True
    assert len(result.content) < MAX_MCP_RESULT_CHARACTERS + 200


def test_mcp_tool_name_normalizes_remote_names() -> None:
    assert mcp_tool_name('my server', 'issue/create') == (
        'mcp__my_server__issue_create'
    )
