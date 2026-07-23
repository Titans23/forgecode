'''Adapt dynamically discovered MCP tools to ForgeCode Tool contracts.'''

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping, TYPE_CHECKING

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from mcp import types

from forge.permissions.policy import PermissionRequest
from forge.tools.base import Tool, ToolInput, ToolResult

if TYPE_CHECKING:
    from forge.mcp.manager import MCPClientManager


MAX_MCP_RESULT_CHARACTERS = 1_000_000
_NAME_CHARACTER = re.compile(r'[^A-Za-z0-9_-]+')


class _UnusedInput(ToolInput):
    pass


def mcp_tool_name(server_name: str, remote_name: str) -> str:
    '''Build a provider-safe, collision-resistant MCP tool name.'''
    server = _NAME_CHARACTER.sub('_', server_name).strip('_') or 'server'
    tool = _NAME_CHARACTER.sub('_', remote_name).strip('_') or 'tool'
    return f'mcp__{server[:20]}__{tool[:36]}'


class MCPToolAdapter(Tool[_UnusedInput]):
    '''Expose one remote MCP tool through the ordinary ToolRegistry.'''

    input_model = _UnusedInput
    effect = 'process'

    def __init__(
        self,
        root: Path,
        *,
        manager: MCPClientManager,
        server_name: str,
        remote_tool: types.Tool,
    ) -> None:
        super().__init__(root)
        self.manager = manager
        self.server_name = server_name
        self.remote_name = remote_tool.name
        self.name = mcp_tool_name(server_name, remote_tool.name)
        self.description = (
            f'[MCP: {server_name}/{remote_tool.name}] '
            f'{remote_tool.description or "External MCP tool."}'
        )
        self.input_schema = dict(remote_tool.inputSchema)
        try:
            Draft202012Validator.check_schema(self.input_schema)
        except SchemaError as error:
            raise ValueError(
                f'Invalid input schema for MCP tool '
                f'{server_name}/{remote_tool.name}: {error.message}'
            ) from error
        self._validator = Draft202012Validator(self.input_schema)

    @property
    def provenance(self) -> dict[str, Any]:
        return {
            'source': 'mcp',
            'server': self.server_name,
            'remote_tool': self.remote_name,
        }

    @property
    def definition(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'description': self.description,
            'input_schema': self.input_schema,
        }

    def permission_request(
        self,
        arguments: Mapping[str, Any],
    ) -> PermissionRequest:
        preview = json.dumps(
            dict(arguments),
            ensure_ascii=False,
            default=str,
        )[:500]
        return PermissionRequest(
            tool_name=self.name,
            capability='mcp.call',
            risk='high',
            targets=(f'{self.server_name}/{self.remote_name}',),
            reason='External MCP tools are untrusted and may have side effects.',
            preview=preview,
        )

    async def run(self, arguments: Mapping[str, Any]) -> ToolResult:
        try:
            self._validator.validate(dict(arguments))
        except ValidationError as error:
            return ToolResult.fail(
                'invalid_arguments',
                f'Invalid arguments for MCP tool {self.name}: {error.message}',
                details={
                    'path': list(error.absolute_path),
                    'schema_path': list(error.absolute_schema_path),
                },
            )
        return await self.manager.call_tool(
            self.server_name,
            self.remote_name,
            dict(arguments),
            exposed_name=self.name,
        )

    async def execute(self, arguments: _UnusedInput) -> ToolResult:
        raise AssertionError('MCPToolAdapter.run performs dynamic validation.')


def mcp_result_to_tool_result(
    result: types.CallToolResult,
    *,
    server_name: str,
    remote_name: str,
    exposed_name: str,
) -> ToolResult:
    '''Convert all MCP content variants into bounded model-visible text.'''
    rendered: list[str] = []
    for block in result.content:
        if isinstance(block, types.TextContent):
            rendered.append(block.text)
        elif isinstance(block, types.ImageContent):
            rendered.append(
                f'[MCP image: {block.mimeType}; '
                f'{len(block.data)} base64 characters]'
            )
        elif isinstance(block, types.AudioContent):
            rendered.append(
                f'[MCP audio: {block.mimeType}; '
                f'{len(block.data)} base64 characters]'
            )
        else:
            rendered.append(
                json.dumps(
                    block.model_dump(mode='json', by_alias=True),
                    ensure_ascii=False,
                    default=str,
                )
            )
    if result.structuredContent is not None:
        rendered.append(
            '[structuredContent]\n'
            + json.dumps(
                result.structuredContent,
                ensure_ascii=False,
                default=str,
            )
        )
    content = '\n'.join(rendered)
    truncated = len(content) > MAX_MCP_RESULT_CHARACTERS
    if truncated:
        content = (
            content[:MAX_MCP_RESULT_CHARACTERS]
            + '\n[ForgeCode truncated MCP output at 1000000 characters.]'
        )
    metadata = {
        'source': 'mcp',
        'server': server_name,
        'remote_tool': remote_name,
        'tool': exposed_name,
        'truncated': truncated,
    }
    if result.isError:
        return ToolResult.fail(
            'mcp_tool_error',
            f'MCP tool {server_name}/{remote_name} returned an error.',
            content=content,
            details={
                'server': server_name,
                'remote_tool': remote_name,
            },
            metadata=metadata,
        )
    return ToolResult.ok(
        f'MCP tool {server_name}/{remote_name} completed.',
        content=content,
        metadata=metadata,
    )
