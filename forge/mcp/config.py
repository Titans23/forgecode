'''Load conservative user and project MCP server configuration.'''

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class MCPConfigurationError(ValueError):
    '''Raised when MCP configuration cannot be safely loaded.'''


class StdioServerConfig(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        populate_by_name=True,
    )

    type: Literal['stdio'] = 'stdio'
    command: str = Field(min_length=1)
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    connect_timeout_seconds: float = Field(
        default=10,
        alias='connectTimeoutSeconds',
        gt=0,
        le=120,
    )
    tool_timeout_seconds: float = Field(
        default=60,
        alias='toolTimeoutSeconds',
        gt=0,
        le=600,
    )


class HTTPServerConfig(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        populate_by_name=True,
    )

    type: Literal['http', 'streamable-http']
    url: str = Field(min_length=1)
    connect_timeout_seconds: float = Field(
        default=10,
        alias='connectTimeoutSeconds',
        gt=0,
        le=120,
    )
    tool_timeout_seconds: float = Field(
        default=60,
        alias='toolTimeoutSeconds',
        gt=0,
        le=600,
    )


MCPServerConfig = Annotated[
    StdioServerConfig | HTTPServerConfig,
    Field(discriminator='type'),
]


class MCPSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')

    mcp_servers: dict[str, MCPServerConfig] = Field(
        default_factory=dict,
        alias='mcpServers',
    )


_ENV_PATTERN = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')
_SERVER_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def load_mcp_servers(
    root: Path,
    *,
    user_path: Path | None = None,
) -> dict[str, MCPServerConfig]:
    '''Load user config then apply project entries by server name.'''
    paths = (
        user_path or Path.home() / '.forge' / 'mcp.json',
        root.resolve() / '.mcp.json',
    )
    merged: dict[str, object] = {}
    for path in paths:
        if not path.is_file():
            continue
        raw = _read_json(path)
        servers = raw.get('mcpServers', {})
        if not isinstance(servers, dict):
            raise MCPConfigurationError(
                f'{path}: mcpServers must be a JSON object.'
            )
        for name, value in servers.items():
            if not _SERVER_NAME_PATTERN.fullmatch(str(name)):
                raise MCPConfigurationError(
                    f'{path}: invalid MCP server name {name!r}.'
                )
            expanded = _expand_environment(value, path)
            if isinstance(expanded, dict) and 'type' not in expanded:
                if 'command' in expanded:
                    expanded['type'] = 'stdio'
                elif 'url' in expanded:
                    expanded['type'] = 'http'
            merged[str(name)] = expanded
    try:
        settings = MCPSettings.model_validate({'mcpServers': merged})
    except ValidationError as error:
        raise MCPConfigurationError(
            f'Invalid ForgeCode MCP configuration: {error}'
        ) from error
    for name, config in settings.mcp_servers.items():
        if not isinstance(config, HTTPServerConfig):
            continue
        parsed = urlsplit(config.url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise MCPConfigurationError(
                f'MCP server {name!r}: url must use http:// or https://.'
            )
        if parsed.username is not None or parsed.password is not None:
            raise MCPConfigurationError(
                f'MCP server {name!r}: credentials must not be embedded in url.'
            )
    return settings.mcp_servers


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MCPConfigurationError(
            f'Could not read MCP configuration {path}: {error}'
        ) from error
    if not isinstance(value, dict):
        raise MCPConfigurationError(
            f'{path}: top-level MCP configuration must be a JSON object.'
        )
    return value


def _expand_environment(value: object, path: Path) -> object:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            resolved = os.environ.get(name)
            if resolved is None:
                raise MCPConfigurationError(
                    f'{path}: environment variable {name} is not set.'
                )
            return resolved

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_environment(item, path) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _expand_environment(item, path)
            for key, item in value.items()
        }
    return value
