'''Feishu Channel SDK adapter using the official WebSocket transport.'''

from __future__ import annotations

import os
from typing import Any

from forge.channels.base import ApprovalHandler, ChannelAdapter, MessageHandler
from forge.channels.config import ChannelConfig
from forge.channels.models import ApprovalAction, Attachment, InboundMessage, OutboundMessage
from forge.permissions.policy import PermissionRequest


class FeishuChannelUnavailable(RuntimeError):
    '''Raised when the optional official SDK is not installed.'''


class FeishuChannelAdapter(ChannelAdapter):
    '''Normalize the official lark-channel-sdk for ForgeCode.'''

    def __init__(self, config: ChannelConfig, *, channel: Any | None = None) -> None:
        if config.platform != 'feishu':
            raise ValueError('FeishuChannelAdapter requires platform=feishu')
        self.config = config
        self._channel = channel
        self._started = False

    def _build_channel(self) -> Any:
        if self._channel is not None:
            return self._channel
        try:
            from lark_channel import FeishuChannel, PolicyConfig, SecurityConfig
        except ImportError as error:
            raise FeishuChannelUnavailable(
                'Install lark-channel-sdk or run `uv sync` before starting '
                'the Feishu gateway.'
            ) from error
        app_id = os.environ.get(self.config.app_id_env, '').strip()
        app_secret = os.environ.get(self.config.app_secret_env, '').strip()
        if not app_id or not app_secret:
            raise FeishuChannelUnavailable(
                f'Set {self.config.app_id_env} and '
                f'{self.config.app_secret_env} before starting Feishu.'
            )
        self._channel = FeishuChannel(
            app_id=app_id,
            app_secret=app_secret,
            policy=PolicyConfig(
                dm_policy=(
                    'allowlist' if self.config.allowed_users else 'disabled'
                ),
                group_policy=(
                    'allowlist' if self.config.allowed_chats else 'open'
                ),
                require_mention=self.config.require_mention,
                allow_from=list(self.config.allowed_users) or None,
                group_allowlist=list(self.config.allowed_chats) or None,
            ),
            security=SecurityConfig(
                mode='strict',
                strict_content_text=True,
            ),
        )
        return self._channel

    async def start(
        self,
        on_message: MessageHandler,
        on_approval: ApprovalHandler,
    ) -> None:
        channel = self._build_channel()

        async def message_handler(value: Any) -> None:
            await on_message(self._normalize_message(value))

        async def approval_handler(value: Any) -> None:
            action = self._normalize_approval(value)
            if action is not None:
                await on_approval(action)

        channel.on('message', message_handler)
        channel.on('cardAction', approval_handler)
        self._started = True
        await channel.connect()

    async def stop(self) -> None:
        channel = self._channel
        self._started = False
        if channel is not None:
            disconnect = getattr(channel, 'disconnect', None)
            if disconnect is not None:
                result = disconnect()
                if hasattr(result, '__await__'):
                    await result

    async def send(self, message: OutboundMessage) -> None:
        channel = self._build_channel()
        options: dict[str, object] = {}
        if message.reply_to_message_id:
            options['reply_to'] = message.reply_to_message_id
        if message.thread_id:
            options['reply_in_thread'] = True
        await channel.send(
            message.chat_id,
            {'markdown': message.text},
            options or None,
        )

    async def request_approval(
        self,
        *,
        chat_id: str,
        requester_id: str,
        approval_id: str,
        arguments_hash: str,
        request: PermissionRequest,
    ) -> None:
        del requester_id
        channel = self._build_channel()
        detail = request.preview.strip() or request.reason
        card = {
            'schema': '2.0',
            'header': {
                'title': {'tag': 'plain_text', 'content': 'ForgeCode 操作确认'},
                'template': 'orange',
            },
            'body': {
                'elements': [
                    {
                        'tag': 'markdown',
                        'content': (
                            f'**操作**：{request.tool_name}\n'
                            f'**目标**：{", ".join(request.targets) or "外部平台"}\n'
                            f'**风险**：{request.risk}\n\n{detail}'
                        ),
                    },
                    {
                        'tag': 'column_set',
                        'columns': [
                            {
                                'tag': 'column',
                                'elements': [
                                    self._approval_button(
                                        '确认执行',
                                        'primary',
                                        approval_id,
                                        arguments_hash,
                                        'approve',
                                    )
                                ],
                            },
                            {
                                'tag': 'column',
                                'elements': [
                                    self._approval_button(
                                        '拒绝',
                                        'danger',
                                        approval_id,
                                        arguments_hash,
                                        'deny',
                                    )
                                ],
                            },
                        ],
                    },
                ]
            },
        }
        await channel.send(chat_id, {'card': card})

    @staticmethod
    def _approval_button(
        text: str,
        button_type: str,
        approval_id: str,
        arguments_hash: str,
        decision: str,
    ) -> dict[str, object]:
        return {
            'tag': 'button',
            'text': {'tag': 'plain_text', 'content': text},
            'type': button_type,
            'value': {
                'forge_approval_id': approval_id,
                'forge_arguments_hash': arguments_hash,
                'forge_decision': decision,
            },
        }

    def _normalize_message(self, value: Any) -> InboundMessage:
        resources = getattr(value, 'resources', ()) or ()
        attachments = tuple(
            Attachment(
                kind=str(getattr(item, 'type', getattr(item, 'kind', 'file'))),
                name=str(
                    getattr(item, 'file_name', '')
                    or getattr(item, 'name', '')
                ),
                key=str(getattr(item, 'key', getattr(item, 'file_key', ''))),
                mime_type=str(getattr(item, 'mime_type', '')),
            )
            for item in resources
        )
        conversation = getattr(value, 'conversation', None)
        return InboundMessage(
            platform='feishu',
            tenant_id=self.config.tenant_id,
            message_id=str(getattr(value, 'message_id', getattr(value, 'id', ''))),
            sender_id=str(getattr(value, 'sender_id', '')),
            sender_name=str(getattr(value, 'sender_name', '')),
            chat_id=str(getattr(value, 'chat_id', '')),
            chat_type=str(getattr(value, 'chat_type', 'unknown')),
            thread_id=str(
                getattr(conversation, 'thread_id', '')
                or getattr(value, 'thread_id', '')
                or getattr(value, 'reply_to_message_id', '')
            ),
            text=str(
                getattr(value, 'body_text', '')
                or getattr(value, 'content_text', '')
            ),
            mentioned_bot=bool(getattr(value, 'mentioned_bot', False)),
            attachments=attachments,
            raw=dict(getattr(value, 'raw', {}) or {}),
        )

    @staticmethod
    def _normalize_approval(value: Any) -> ApprovalAction | None:
        raw = getattr(value, 'raw', value)
        action_value = getattr(getattr(value, 'action', None), 'value', None)
        payload = action_value if isinstance(action_value, dict) else {}
        if not payload:
            payload = _find_mapping(raw, 'value')
        if not payload:
            payload = raw if isinstance(raw, dict) else {}
        approval_id = str(payload.get('forge_approval_id', ''))
        arguments_hash = str(payload.get('forge_arguments_hash', ''))
        decision = str(payload.get('forge_decision', ''))
        actor = (
            str(getattr(getattr(value, 'operator', None), 'open_id', ''))
            or str(getattr(getattr(value, 'operator', None), 'user_id', ''))
            or
            str(getattr(value, 'operator_id', ''))
            or str(getattr(value, 'sender_id', ''))
            or str(_deep_value(raw, ('operator', 'open_id')) or '')
            or str(_deep_value(raw, ('operator', 'operator_id', 'open_id')) or '')
        )
        if not approval_id or not arguments_hash or not actor:
            return None
        return ApprovalAction(approval_id, actor, decision, arguments_hash)


def _find_mapping(value: Any, key: str) -> dict[str, Any]:
    if isinstance(value, dict):
        candidate = value.get(key)
        if isinstance(candidate, dict) and 'forge_approval_id' in candidate:
            return candidate
        for item in value.values():
            found = _find_mapping(item, key)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_mapping(item, key)
            if found:
                return found
    return {}


def _deep_value(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
