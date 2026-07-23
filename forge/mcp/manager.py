'''Long-lived MCP client connections and dynamic ToolRegistry integration.'''

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
import os
from pathlib import Path
import re
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from forge.mcp.config import (
    HTTPServerConfig,
    MCPServerConfig,
    StdioServerConfig,
)
from forge.mcp.tool import MCPToolAdapter, mcp_result_to_tool_result
from forge.permissions.policy import PermissionManager, PermissionRequest
from forge.tools.base import ToolRegistry, ToolResult
from forge.tools.shell import sanitized_process_environment


@dataclass(slots=True)
class MCPConnection:
    name: str
    config: MCPServerConfig
    state: str = 'disconnected'
    session: ClientSession | None = None
    stack: AsyncExitStack | None = None
    tool_names: set[str] = field(default_factory=set)
    server_version: str = ''
    protocol_version: str = ''
    last_error: str = ''
    authorization_denied: bool = False
    connected_once: bool = False


class MCPClientManager:
    '''Own MCP transports for one ForgeCode process and one repository root.'''

    def __init__(
        self,
        root: Path,
        registry: ToolRegistry,
        servers: dict[str, MCPServerConfig],
        *,
        permission_manager: PermissionManager | None = None,
        journal: Any | None = None,
    ) -> None:
        self.root = root.resolve()
        self.registry = registry
        self.permission_manager = permission_manager
        self.journal = journal
        self.connections = {
            name: MCPConnection(name, config)
            for name, config in servers.items()
        }
        self._notification_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    def bind(
        self,
        permission_manager: PermissionManager,
        journal: Any | None,
    ) -> None:
        self.permission_manager = permission_manager
        self.journal = journal

    async def ensure_connected(self) -> None:
        '''Connect configured servers once permission mode allows it.'''
        if self._closed or not self.connections:
            return
        manager = self.permission_manager
        if manager is None:
            return
        if manager.mode == 'plan':
            for connection in self.connections.values():
                if connection.session is not None:
                    await self._disconnect(connection, state='disabled')
            return
        for connection in self.connections.values():
            if connection.session is not None or connection.authorization_denied:
                continue
            await self._connect(connection)

    async def _connect(self, connection: MCPConnection) -> None:
        decision = await self._authorize_connection(connection)
        if not decision:
            connection.state = 'disabled'
            connection.authorization_denied = True
            return
        connection.state = 'connecting'
        connection.last_error = ''
        self._audit(
            'mcp_connecting',
            self._connection_payload(connection),
        )
        stack = AsyncExitStack()
        try:
            async with asyncio.timeout(
                connection.config.connect_timeout_seconds
            ):
                if isinstance(connection.config, StdioServerConfig):
                    environment = sanitized_process_environment()
                    environment.update(connection.config.env)
                    parameters = StdioServerParameters(
                        command=connection.config.command,
                        args=list(connection.config.args),
                        env=environment,
                        cwd=self.root,
                    )
                    errlog = stack.enter_context(
                        open(os.devnull, 'w', encoding='utf-8')
                    )
                    read, write = await stack.enter_async_context(
                        stdio_client(parameters, errlog=errlog)
                    )
                else:
                    read, write, _ = await stack.enter_async_context(
                        streamable_http_client(connection.config.url)
                    )
                session = await stack.enter_async_context(
                    ClientSession(
                        read,
                        write,
                        read_timeout_seconds=timedelta(
                            seconds=connection.config.tool_timeout_seconds
                        ),
                        message_handler=self._message_handler(connection.name),
                    )
                )
                initialized = await session.initialize()
                connection.session = session
                connection.stack = stack
                connection.protocol_version = initialized.protocolVersion
                connection.server_version = (
                    f'{initialized.serverInfo.name} '
                    f'{initialized.serverInfo.version}'
                ).strip()
                await self.refresh_tools(connection.name)
            if connection.state != 'degraded':
                connection.state = 'ready'
            event = (
                'mcp_reconnected'
                if connection.connected_once
                else 'mcp_connected'
            )
            connection.connected_once = True
            self._audit(event, self._connection_payload(connection))
        except Exception as error:
            connection.state = 'failed'
            connection.last_error = self._safe_error(error)
            self._audit(
                'mcp_error',
                {
                    **self._connection_payload(connection),
                    'phase': 'connect',
                    'error': connection.last_error,
                },
            )
            connection.session = None
            connection.stack = None
            await stack.aclose()

    async def _authorize_connection(self, connection: MCPConnection) -> bool:
        manager = self.permission_manager
        if manager is None:
            return False
        config = connection.config
        if isinstance(config, StdioServerConfig):
            capability = 'mcp.connect.process'
            preview = ' '.join((config.command, *config.args))[:500]
            reason = 'Starting a local MCP server launches a subprocess.'
        else:
            capability = 'mcp.connect.network'
            preview = config.url[:500]
            reason = 'Connecting to an MCP server performs network access.'
        decision = await manager.authorize(
            PermissionRequest(
                tool_name=f'mcp_connect__{connection.name}',
                capability=capability,
                risk='high',
                targets=(connection.name,),
                reason=reason,
                preview=preview,
            )
        )
        return decision.action == 'allow'

    def _message_handler(self, server_name: str):
        async def handle(message: Any) -> None:
            if isinstance(message, Exception):
                connection = self.connections[server_name]
                connection.last_error = self._safe_error(message)
                connection.state = 'degraded'
                return
            if (
                isinstance(message, types.ServerNotification)
                and isinstance(
                    message.root,
                    types.ToolListChangedNotification,
                )
            ):
                task = asyncio.create_task(self.refresh_tools(server_name))
                self._notification_tasks.add(task)
                task.add_done_callback(self._notification_tasks.discard)

        return handle

    async def refresh_tools(self, server_name: str) -> None:
        '''Fetch every tools/list page and replace this server's adapters.'''
        connection = self.connections[server_name]
        session = connection.session
        if session is None:
            return
        discovered: list[types.Tool] = []
        cursor: str | None = None
        try:
            while True:
                page = await session.list_tools(cursor)
                discovered.extend(page.tools)
                cursor = page.nextCursor
                if not cursor:
                    break
            adapters = [
                MCPToolAdapter(
                    self.root,
                    manager=self,
                    server_name=server_name,
                    remote_tool=tool,
                )
                for tool in discovered
            ]
            new_names = {adapter.name for adapter in adapters}
            if len(new_names) != len(adapters):
                raise ValueError(
                    f'MCP server {server_name!r} exposes tool names that '
                    'collide after normalization.'
                )
            unavailable = (
                set(self.registry.names) - connection.tool_names
            ) & new_names
            if unavailable:
                raise ValueError(
                    'MCP tool names collide with registered tools: '
                    + ', '.join(sorted(unavailable))
                )
            for name in connection.tool_names:
                self.registry.unregister(name)
            for adapter in adapters:
                self.registry.register(adapter)
            connection.tool_names = new_names
            self._audit(
                'mcp_tools_refreshed',
                {
                    **self._connection_payload(connection),
                    'tools': sorted(new_names),
                },
            )
        except Exception as error:
            connection.state = 'degraded'
            connection.last_error = self._safe_error(error)
            self._audit(
                'mcp_error',
                {
                    **self._connection_payload(connection),
                    'phase': 'tools/list',
                    'error': connection.last_error,
                },
            )

    async def call_tool(
        self,
        server_name: str,
        remote_name: str,
        arguments: dict[str, Any],
        *,
        exposed_name: str,
    ) -> ToolResult:
        connection = self.connections.get(server_name)
        if connection is None:
            return ToolResult.fail(
                'mcp_server_unknown',
                f'Unknown MCP server: {server_name}',
            )
        if connection.session is None:
            await self._connect(connection)
        session = connection.session
        if session is None:
            return ToolResult.fail(
                'mcp_unavailable',
                f'MCP server {server_name} is unavailable.',
                content=connection.last_error,
                details={'server': server_name},
                metadata={'source': 'mcp', 'server': server_name},
            )
        try:
            async with asyncio.timeout(
                connection.config.tool_timeout_seconds
            ):
                result = await session.call_tool(remote_name, arguments)
            return mcp_result_to_tool_result(
                result,
                server_name=server_name,
                remote_name=remote_name,
                exposed_name=exposed_name,
            )
        except Exception as error:
            message = self._safe_error(error)
            await self._disconnect(
                connection,
                state='disconnected',
                error=message,
            )
            return ToolResult.fail(
                'mcp_result_unknown',
                (
                    f'MCP tool {server_name}/{remote_name} lost its response. '
                    'The remote operation may have completed and will not be '
                    'automatically replayed.'
                ),
                content=message,
                details={
                    'server': server_name,
                    'remote_tool': remote_name,
                },
                metadata={
                    'source': 'mcp',
                    'server': server_name,
                    'remote_tool': remote_name,
                    'result_unknown': True,
                },
            )

    async def _disconnect(
        self,
        connection: MCPConnection,
        *,
        state: str,
        error: str = '',
    ) -> None:
        for name in connection.tool_names:
            self.registry.unregister(name)
        connection.tool_names.clear()
        stack = connection.stack
        connection.stack = None
        connection.session = None
        connection.state = state
        connection.last_error = error
        if stack is not None:
            try:
                await stack.aclose()
            except Exception as close_error:
                if not connection.last_error:
                    connection.last_error = self._safe_error(close_error)
        self._audit(
            'mcp_disconnected',
            {
                **self._connection_payload(connection),
                'error': connection.last_error,
            },
        )

    async def reset_session(self) -> None:
        '''Drop connections so session-scoped approvals never cross sessions.'''
        for connection in self.connections.values():
            await self._disconnect(connection, state='disconnected')
            connection.authorization_denied = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = tuple(self._notification_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for connection in reversed(tuple(self.connections.values())):
            await self._disconnect(connection, state='closed')

    def status(self) -> str:
        if not self.connections:
            return 'No MCP servers configured.'
        lines: list[str] = []
        for connection in self.connections.values():
            transport = (
                'stdio'
                if isinstance(connection.config, StdioServerConfig)
                else 'http'
            )
            line = (
                f'{connection.name}: {connection.state} · {transport} · '
                f'{len(connection.tool_names)} tool(s)'
            )
            if connection.server_version:
                line += f' · {connection.server_version}'
            if connection.last_error:
                line += f'\n  error: {connection.last_error}'
            lines.append(line)
        return '\n'.join(lines)

    def _connection_payload(
        self,
        connection: MCPConnection,
    ) -> dict[str, Any]:
        return {
            'server': connection.name,
            'transport': (
                'stdio'
                if isinstance(connection.config, StdioServerConfig)
                else 'http'
            ),
            'state': connection.state,
            'tool_count': len(connection.tool_names),
            'protocol_version': connection.protocol_version,
            'server_version': connection.server_version,
        }

    def _audit(self, event: str, payload: dict[str, Any]) -> None:
        if self.journal is not None:
            self.journal.append(event, payload)

    @staticmethod
    def _safe_error(error: Exception) -> str:
        text = str(error).replace('\r', ' ').replace('\n', ' ')
        text = re.sub(
            r'(?i)\b(Bearer)\s+\S+',
            r'\1 [REDACTED]',
            text,
        )
        text = re.sub(
            r'(?i)\b(token|key|secret|password)=([^\s&;,]+)',
            r'\1=[REDACTED]',
            text,
        )
        return text[:1_000] or type(error).__name__
