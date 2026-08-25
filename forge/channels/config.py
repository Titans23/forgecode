'''Validated user/project configuration for official chat channels.'''

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Literal

from dotenv import load_dotenv
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class ChannelConfigurationError(ValueError):
    '''Raised when channel configuration is unsafe or malformed.'''


_ENV_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_CHANNEL_NAME = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})
_FALSE_VALUES = frozenset({'0', 'false', 'no', 'off'})
_FEISHU_ENV_FIELDS = {
    'FEISHU_CHANNEL_NAME',
    'FEISHU_ENABLED',
    'FEISHU_TRANSPORT',
    'FEISHU_TENANT_ID',
    'FEISHU_ALLOWED_USERS',
    'FEISHU_ALLOWED_CHATS',
    'FEISHU_REQUIRE_MENTION',
    'FEISHU_APPROVAL_TIMEOUT_SECONDS',
    'FEISHU_APP_ID',
    'FEISHU_APP_SECRET',
}


def _env_list(name: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r'[,\n]', os.environ[name])
        if item.strip()
    ]


def _env_bool(name: str) -> bool:
    value = os.environ[name].strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ChannelConfigurationError(
        f'{name} must be one of: true, false, 1, 0, yes, no, on, off.'
    )


def _env_float(name: str) -> float:
    try:
        return float(os.environ[name].strip())
    except ValueError as error:
        raise ChannelConfigurationError(f'{name} must be a number.') from error


def _apply_feishu_environment(merged: dict[str, object]) -> None:
    if not any(name in os.environ for name in _FEISHU_ENV_FIELDS):
        return
    channel_name = (
        os.environ.get('FEISHU_CHANNEL_NAME', '').strip() or 'feishu-main'
    )
    if not _CHANNEL_NAME.fullmatch(channel_name):
        raise ChannelConfigurationError(
            f'FEISHU_CHANNEL_NAME is invalid: {channel_name!r}.'
        )
    if channel_name not in merged and not any(
        name in os.environ
        for name in ('FEISHU_ALLOWED_USERS', 'FEISHU_ALLOWED_CHATS')
    ):
        return
    if channel_name not in merged:
        feishu_names = [
            name
            for name, value in merged.items()
            if isinstance(value, dict) and value.get('platform') == 'feishu'
        ]
        if 'FEISHU_CHANNEL_NAME' not in os.environ and len(feishu_names) == 1:
            channel_name = feishu_names[0]
    raw_current = merged.get(channel_name, {})
    current = dict(raw_current) if isinstance(raw_current, dict) else {}
    if current and current.get('platform') not in (None, 'feishu'):
        raise ChannelConfigurationError(
            f'Environment channel {channel_name!r} conflicts with its platform.'
        )
    current.update(
        {
            'platform': 'feishu',
            'appIdEnv': 'FEISHU_APP_ID',
            'appSecretEnv': 'FEISHU_APP_SECRET',
        }
    )
    if 'FEISHU_ENABLED' in os.environ:
        current['enabled'] = _env_bool('FEISHU_ENABLED')
    if 'FEISHU_TRANSPORT' in os.environ:
        current['transport'] = os.environ['FEISHU_TRANSPORT'].strip()
    if 'FEISHU_TENANT_ID' in os.environ:
        current['tenantId'] = os.environ['FEISHU_TENANT_ID'].strip()
    if 'FEISHU_ALLOWED_USERS' in os.environ:
        current['allowedUsers'] = _env_list('FEISHU_ALLOWED_USERS')
    if 'FEISHU_ALLOWED_CHATS' in os.environ:
        current['allowedChats'] = _env_list('FEISHU_ALLOWED_CHATS')
    if 'FEISHU_REQUIRE_MENTION' in os.environ:
        current['requireMention'] = _env_bool('FEISHU_REQUIRE_MENTION')
    if 'FEISHU_APPROVAL_TIMEOUT_SECONDS' in os.environ:
        current['approvalTimeoutSeconds'] = _env_float(
            'FEISHU_APPROVAL_TIMEOUT_SECONDS'
        )
    merged[channel_name] = current


class ChannelConfig(BaseModel):
    '''One official bot/channel connection; secrets are environment-only.'''

    model_config = ConfigDict(extra='forbid', frozen=True, populate_by_name=True)

    platform: Literal['feishu', 'wecom', 'qq']
    enabled: bool = True
    transport: Literal['websocket', 'webhook'] = 'websocket'
    app_id_env: str = Field(default='APP_ID', alias='appIdEnv')
    app_secret_env: str = Field(default='APP_SECRET', alias='appSecretEnv')
    tenant_id: str = Field(default='default', alias='tenantId', min_length=1)
    allowed_users: tuple[str, ...] = Field(default=(), alias='allowedUsers')
    allowed_chats: tuple[str, ...] = Field(default=(), alias='allowedChats')
    require_mention: bool = Field(default=True, alias='requireMention')
    approval_timeout_seconds: float = Field(
        default=600,
        alias='approvalTimeoutSeconds',
        ge=10,
        le=3600,
    )

    @field_validator('app_id_env', 'app_secret_env')
    @classmethod
    def validate_environment_name(cls, value: str) -> str:
        if not _ENV_NAME.fullmatch(value):
            raise ValueError('credential fields must name environment variables')
        return value

    @model_validator(mode='after')
    def require_an_allowlist(self) -> 'ChannelConfig':
        if self.enabled and not self.allowed_users and not self.allowed_chats:
            raise ValueError(
                'enabled channels require allowedUsers or allowedChats'
            )
        return self

    def credential_status(self) -> tuple[bool, tuple[str, ...]]:
        missing = tuple(
            name
            for name in (self.app_id_env, self.app_secret_env)
            if not os.environ.get(name, '').strip()
        )
        return not missing, missing

    def accepts(self, message: 'InboundMessage') -> bool:
        from forge.channels.models import InboundMessage

        if not isinstance(message, InboundMessage):
            return False
        if message.platform != self.platform or message.tenant_id != self.tenant_id:
            return False
        if self.allowed_users and message.sender_id not in self.allowed_users:
            return False
        if self.allowed_chats and message.chat_id not in self.allowed_chats:
            return False
        if (
            self.require_mention
            and message.chat_type != 'p2p'
            and not message.mentioned_bot
        ):
            return False
        return True


class ChannelSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')

    channels: dict[str, ChannelConfig] = Field(default_factory=dict)


def load_channel_settings(
    root: Path,
    *,
    user_path: Path | None = None,
) -> ChannelSettings:
    '''Load user config and apply project entries by channel name.'''
    load_dotenv(dotenv_path=root.resolve() / '.env', override=False)
    paths = (
        user_path or Path.home() / '.forge' / 'channels.json',
        root.resolve() / '.forge' / 'channels.json',
    )
    merged: dict[str, object] = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ChannelConfigurationError(
                f'Could not read channel configuration {path}: {error}'
            ) from error
        if not isinstance(raw, dict) or not isinstance(raw.get('channels', {}), dict):
            raise ChannelConfigurationError(
                f'{path}: top-level channels must be a JSON object.'
            )
        for name, value in raw.get('channels', {}).items():
            if not _CHANNEL_NAME.fullmatch(str(name)):
                raise ChannelConfigurationError(
                    f'{path}: invalid channel name {name!r}.'
                )
            merged[str(name)] = value
    _apply_feishu_environment(merged)
    try:
        return ChannelSettings.model_validate({'channels': merged})
    except ValidationError as error:
        raise ChannelConfigurationError(
            f'Invalid ForgeCode channel configuration: {error}'
        ) from error
