'''Local stdio MCP exposing a deliberately narrow Feishu office surface.'''

from __future__ import annotations

import json
from typing import Any, NoReturn

from mcp import types
from mcp.server.fastmcp import FastMCP

from forge.office.feishu import FeishuAPIError, FeishuOpenAPI


def build_server(client: FeishuOpenAPI | None = None) -> FastMCP:
    server = FastMCP('ForgeCode Office', log_level='ERROR')
    api = client or FeishuOpenAPI.from_env()

    @server.tool(
        name='feishu_document_read',
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def document_read(document_id: str) -> dict[str, Any]:
        '''Read metadata and the stable block tree of one Feishu document.'''
        return await api.read_document(document_id)

    @server.tool(
        name='feishu_document_create',
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def document_create(
        title: str,
        blocks_json: str = '[]',
        folder_token: str = '',
    ) -> dict[str, Any]:
        '''Create a Feishu document using supported text block definitions.'''
        blocks = _json_list(blocks_json, 'blocks_json')
        try:
            return await api.create_document(
                title,
                blocks,
                folder_token=folder_token,
            )
        except FeishuAPIError as error:
            _raise_write_error(error)

    @server.tool(
        name='feishu_document_update',
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def document_update(
        document_id: str,
        expected_revision_id: int,
        updates_json: str,
    ) -> dict[str, Any]:
        '''Update supported text blocks only when the document revision matches.'''
        updates = _json_list(updates_json, 'updates_json')
        try:
            return await api.update_document(
                document_id,
                expected_revision_id,
                updates,
            )
        except FeishuAPIError as error:
            _raise_write_error(error)

    @server.tool(
        name='feishu_message_send',
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def message_send(chat_ids: list[str], content: str) -> dict[str, Any]:
        '''Send one exact text message once to each distinct Feishu chat ID.'''
        if not chat_ids:
            raise ValueError('chat_ids must not be empty')
        if not content.strip():
            raise ValueError('content must not be empty')
        return await api.send_message(chat_ids, content)

    return server


def _json_list(value: str, name: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f'{name} must be valid JSON') from error
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise ValueError(f'{name} must be a JSON array of objects')
    return parsed


def _raise_write_error(error: FeishuAPIError) -> NoReturn:
    if error.code == 'network_error':
        raise RuntimeError(
            '[mcp_result_unknown] The Feishu write may have completed; '
            'do not automatically replay it.'
        ) from error
    raise error


def main() -> None:
    build_server().run(transport='stdio')


if __name__ == '__main__':
    main()
