'''Narrow, auditable Feishu OpenAPI client for office MCP tools.'''

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


class FeishuAPIError(RuntimeError):
    '''Sanitized Feishu HTTP/OpenAPI failure.'''

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


SUPPORTED_BLOCKS = {
    2: 'text',
    3: 'heading1',
    4: 'heading2',
    5: 'heading3',
    6: 'heading4',
    7: 'heading5',
    8: 'heading6',
    9: 'heading7',
    10: 'heading8',
    11: 'heading9',
    12: 'bullet',
    13: 'ordered',
    14: 'code',
    15: 'quote',
    17: 'todo',
}
BLOCK_TYPES = {value: key for key, value in SUPPORTED_BLOCKS.items()}


@dataclass(slots=True)
class _Token:
    value: str = ''
    expires_at: float = 0


class FeishuOpenAPI:
    '''Application-identity client with token caching and no automatic writes.'''

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        base_url: str = 'https://open.feishu.cn/open-apis',
        timeout_seconds: float = 30,
    ) -> None:
        if not app_id.strip() or not app_secret.strip():
            raise ValueError('Feishu APP_ID and APP_SECRET are required.')
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self.base_url = base_url.rstrip('/')
        self.timeout_seconds = timeout_seconds
        self._token = _Token()
        self._token_lock = asyncio.Lock()

    @classmethod
    def from_env(cls) -> 'FeishuOpenAPI':
        return cls(
            os.environ.get('APP_ID', ''),
            os.environ.get('APP_SECRET', ''),
            base_url=os.environ.get(
                'FEISHU_OPENAPI_BASE_URL',
                'https://open.feishu.cn/open-apis',
            ),
        )

    async def _access_token(self) -> str:
        if self._token.value and monotonic() < self._token.expires_at:
            return self._token.value
        async with self._token_lock:
            if self._token.value and monotonic() < self._token.expires_at:
                return self._token.value
            payload = await self._request_raw(
                'POST',
                '/auth/v3/tenant_access_token/internal',
                {'app_id': self.app_id, 'app_secret': self.app_secret},
                authenticated=False,
            )
            value = str(payload.get('tenant_access_token', ''))
            if not value:
                raise FeishuAPIError('authentication_failed', 'Feishu did not return an access token.')
            expires = int(payload.get('expire', 7200))
            self._token = _Token(value, monotonic() + max(60, expires - 120))
            return value

    async def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        query: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        token = await self._access_token()
        return await self._request_raw(
            method,
            path,
            body,
            query=query,
            token=token,
        )

    async def _request_raw(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        *,
        query: dict[str, object] | None = None,
        authenticated: bool = True,
        token: str = '',
    ) -> dict[str, Any]:
        del authenticated
        url = self.base_url + path
        if query:
            url += '?' + urlencode(
                {key: value for key, value in query.items() if value is not None}
            )
        data = (
            json.dumps(body, ensure_ascii=False).encode('utf-8')
            if body is not None
            else None
        )
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        request = Request(url, data=data, headers=headers, method=method)

        def execute() -> dict[str, Any]:
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read()
            except HTTPError as error:
                raw = error.read()
                message = _safe_api_message(raw) or f'Feishu HTTP {error.code}'
                raise FeishuAPIError('http_error', message, status=error.code) from error
            except (URLError, TimeoutError, OSError) as error:
                raise FeishuAPIError(
                    'network_error',
                    f'Feishu request failed: {type(error).__name__}',
                ) from error
            try:
                payload = json.loads(raw.decode('utf-8')) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise FeishuAPIError('invalid_response', 'Feishu returned invalid JSON.') from error
            if not isinstance(payload, dict):
                raise FeishuAPIError('invalid_response', 'Feishu returned a non-object response.')
            code = int(payload.get('code', 0))
            if code != 0:
                raise FeishuAPIError(
                    str(code),
                    str(payload.get('msg', 'Feishu OpenAPI request failed.'))[:500],
                )
            data_value = payload.get('data', payload)
            return data_value if isinstance(data_value, dict) else {'value': data_value}

        return await asyncio.to_thread(execute)

    async def read_document(self, document_id: str) -> dict[str, Any]:
        document_id = _identifier(document_id, 'document_id')
        metadata = await self.request(
            'GET', f'/docx/v1/documents/{quote(document_id, safe="")}'
        )
        blocks: list[dict[str, Any]] = []
        page_token = ''
        while True:
            query: dict[str, object] = {'page_size': 500}
            if page_token:
                query['page_token'] = page_token
            page = await self.request(
                'GET',
                f'/docx/v1/documents/{quote(document_id, safe="")}/blocks',
                query=query,
            )
            blocks.extend(item for item in page.get('items', []) if isinstance(item, dict))
            if not page.get('has_more'):
                break
            page_token = str(page.get('page_token', ''))
            if not page_token:
                break
        document = metadata.get('document', {})
        normalized = [_normalize_block(block) for block in blocks]
        return {
            'document_id': document_id,
            'title': document.get('title', ''),
            'revision_id': int(document.get('revision_id', 0)),
            'blocks': normalized,
            'unsupported_block_ids': [
                item['block_id'] for item in normalized if not item['supported']
            ],
        }

    async def create_document(
        self,
        title: str,
        blocks: list[dict[str, Any]],
        *,
        folder_token: str = '',
    ) -> dict[str, Any]:
        clean_title = title.strip()
        if not clean_title:
            raise FeishuAPIError(
                'invalid_title',
                'Document title must not be empty.',
            )
        children = [_new_block(item) for item in blocks]
        body: dict[str, Any] = {'title': clean_title}
        if folder_token:
            body['folder_token'] = _identifier(folder_token, 'folder_token')
        created = await self.request('POST', '/docx/v1/documents', body)
        document = created.get('document', {})
        document_id = str(document.get('document_id', ''))
        if not document_id:
            raise FeishuAPIError('invalid_response', 'Created document has no document_id.')
        if children:
            for start in range(0, len(children), 50):
                await self.request(
                    'POST',
                    f'/docx/v1/documents/{quote(document_id, safe="")}/blocks/'
                    f'{quote(document_id, safe="")}/children',
                    {'children': children[start : start + 50], 'index': -1},
                    query={'client_token': str(uuid4())},
                )
        return await self.read_document(document_id)

    async def update_document(
        self,
        document_id: str,
        expected_revision_id: int,
        updates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        document_id = _identifier(document_id, 'document_id')
        if len(updates) > 200:
            raise FeishuAPIError(
                'update_limit',
                'A safe document update is limited to 200 blocks.',
            )
        current = await self.read_document(document_id)
        if current['revision_id'] != expected_revision_id:
            raise FeishuAPIError(
                'revision_conflict',
                f'Document revision changed from {expected_revision_id} to '
                f'{current["revision_id"]}; read it again before updating.',
            )
        supported = {
            item['block_id'] for item in current['blocks'] if item['supported']
        }
        requests: list[dict[str, Any]] = []
        seen_block_ids: set[str] = set()
        for update in updates:
            block_id = _identifier(str(update.get('block_id', '')), 'block_id')
            if block_id in seen_block_ids:
                raise FeishuAPIError(
                    'duplicate_block',
                    f'Block {block_id} appears more than once in the update.',
                )
            seen_block_ids.add(block_id)
            if block_id not in supported:
                raise FeishuAPIError(
                    'unsupported_block',
                    f'Block {block_id} is unsupported or not in this document.',
                )
            content = str(update.get('content', ''))
            requests.append(
                {
                    'block_id': block_id,
                    'update_text_elements': {
                        'elements': [{'text_run': {'content': content}}]
                    },
                }
            )
        if requests:
            await self.request(
                'PATCH',
                f'/docx/v1/documents/{quote(document_id, safe="")}/blocks/batch_update',
                {'requests': requests},
                query={
                    'document_revision_id': expected_revision_id,
                    'client_token': str(uuid4()),
                },
            )
        return await self.read_document(document_id)

    async def send_message(self, chat_ids: list[str], content: str) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_chat_id in chat_ids:
            chat_id = _identifier(raw_chat_id, 'chat_id')
            if chat_id in seen:
                continue
            seen.add(chat_id)
            try:
                data = await self.request(
                    'POST',
                    '/im/v1/messages',
                    {
                        'receive_id': chat_id,
                        'msg_type': 'text',
                        'content': json.dumps({'text': content}, ensure_ascii=False),
                    },
                    query={'receive_id_type': 'chat_id'},
                )
                message_id = str(data.get('message_id', data.get('message', {}).get('message_id', '')))
                results.append({'chat_id': chat_id, 'success': True, 'message_id': message_id})
            except FeishuAPIError as error:
                results.append(
                    {
                        'chat_id': chat_id,
                        'success': False,
                        'error_code': error.code,
                        'result_unknown': error.code == 'network_error',
                    }
                )
        return {
            'target_count': len(seen),
            'succeeded': sum(1 for item in results if item['success']),
            'failed': sum(1 for item in results if not item['success']),
            'result_unknown': any(
                item.get('result_unknown', False) for item in results
            ),
            'results': results,
        }


def _safe_api_message(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ''
    if not isinstance(payload, dict):
        return ''
    return str(payload.get('msg', ''))[:500]


def _identifier(value: str, name: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 256 or not all(
        character.isalnum() or character in '_-' for character in cleaned
    ):
        raise FeishuAPIError('invalid_identifier', f'Invalid {name}.')
    return cleaned


def _normalize_block(block: dict[str, Any]) -> dict[str, Any]:
    block_type = int(block.get('block_type', 0))
    kind = SUPPORTED_BLOCKS.get(block_type, f'unsupported:{block_type}')
    content = ''
    payload = block.get(kind, {}) if kind in BLOCK_TYPES else {}
    if isinstance(payload, dict):
        parts: list[str] = []
        for element in payload.get('elements', []):
            if not isinstance(element, dict):
                continue
            run = element.get('text_run')
            if isinstance(run, dict):
                parts.append(str(run.get('content', '')))
            elif 'mention_user' in element:
                parts.append('@' + str(element.get('mention_user', {}).get('user_id', '')))
            elif 'mention_doc' in element:
                parts.append(str(element.get('mention_doc', {}).get('title', '')))
        content = ''.join(parts)
    return {
        'block_id': str(block.get('block_id', '')),
        'parent_id': str(block.get('parent_id', '')),
        'type': kind,
        'content': content,
        'children': [str(value) for value in block.get('children', [])],
        'supported': block_type in SUPPORTED_BLOCKS,
    }


def _new_block(value: dict[str, Any]) -> dict[str, Any]:
    kind = str(value.get('type', 'text'))
    if kind not in BLOCK_TYPES:
        raise FeishuAPIError('unsupported_block', f'Cannot create block type {kind!r}.')
    content = str(value.get('content', ''))
    return {
        'block_type': BLOCK_TYPES[kind],
        kind: {'elements': [{'text_run': {'content': content}}]},
    }
