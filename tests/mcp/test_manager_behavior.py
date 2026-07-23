'''Dynamic discovery and uncertain-result behavior for MCP clients.'''

import asyncio
from pathlib import Path

from mcp import types

from forge.mcp.config import StdioServerConfig
from forge.mcp.manager import MCPClientManager
from forge.permissions.approval import StaticApprovalHandler
from forge.permissions.policy import PermissionManager
from forge.tools.base import ToolRegistry


def permission_manager(root: Path) -> PermissionManager:
    return PermissionManager(
        root,
        mode='supervised',
        approval_handler=StaticApprovalHandler('allow_session'),
        user_path=root / 'missing-user-permissions.json',
    )


def test_tool_list_changed_refreshes_registry(tmp_path: Path) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.tools = [
                types.Tool(
                    name='first',
                    inputSchema={'type': 'object'},
                )
            ]

        async def list_tools(self, cursor=None):
            assert cursor is None
            return types.ListToolsResult(tools=self.tools)

    async def run() -> None:
        registry = ToolRegistry()
        manager = MCPClientManager(
            tmp_path,
            registry,
            {'dynamic': StdioServerConfig(command='unused')},
            permission_manager=permission_manager(tmp_path),
        )
        session = FakeSession()
        manager.connections['dynamic'].session = session
        await manager.refresh_tools('dynamic')
        assert 'mcp__dynamic__first' in registry.names

        session.tools = [
            types.Tool(
                name='second',
                inputSchema={'type': 'object'},
            )
        ]
        handler = manager._message_handler('dynamic')
        await handler(
            types.ServerNotification(
                root=types.ToolListChangedNotification()
            )
        )
        await asyncio.gather(*tuple(manager._notification_tasks))

        assert 'mcp__dynamic__first' not in registry.names
        assert 'mcp__dynamic__second' in registry.names
        await manager.close()

    asyncio.run(run())


def test_lost_tool_response_is_not_replayed(tmp_path: Path) -> None:
    class LostSession:
        def __init__(self) -> None:
            self.calls = 0

        async def call_tool(self, name, arguments):
            del name, arguments
            self.calls += 1
            raise ConnectionError('connection lost')

    async def run() -> None:
        registry = ToolRegistry()
        manager = MCPClientManager(
            tmp_path,
            registry,
            {'lost': StdioServerConfig(command='unused')},
            permission_manager=permission_manager(tmp_path),
        )
        session = LostSession()
        manager.connections['lost'].session = session

        result = await manager.call_tool(
            'lost',
            'mutate',
            {'value': 1},
            exposed_name='mcp__lost__mutate',
        )

        assert result.error is not None
        assert result.error.code == 'mcp_result_unknown'
        assert session.calls == 1
        assert manager.connections['lost'].session is None
        await manager.close()

    asyncio.run(run())
