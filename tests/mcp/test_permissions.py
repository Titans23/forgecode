'''Permission boundaries specific to MCP connections and tools.'''

import asyncio
from pathlib import Path

from forge.mcp.config import StdioServerConfig
from forge.mcp.manager import MCPClientManager
from forge.permissions.approval import StaticApprovalHandler
from forge.permissions.policy import PermissionManager
from forge.tools.base import ToolRegistry


def test_plan_mode_does_not_start_mcp_server(tmp_path: Path) -> None:
    registry = ToolRegistry()
    permissions = PermissionManager(
        tmp_path,
        mode='plan',
        user_path=tmp_path / 'missing.json',
    )
    manager = MCPClientManager(
        tmp_path,
        registry,
        {'blocked': StdioServerConfig(command='command-that-must-not-run')},
        permission_manager=permissions,
    )

    asyncio.run(manager.ensure_connected())

    assert manager.connections['blocked'].state == 'disconnected'
    assert not registry.names


def test_denied_connection_is_not_retried_automatically(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        registry = ToolRegistry()
        permissions = PermissionManager(
            tmp_path,
            mode='supervised',
            approval_handler=StaticApprovalHandler('deny'),
            user_path=tmp_path / 'missing.json',
        )
        manager = MCPClientManager(
            tmp_path,
            registry,
            {'blocked': StdioServerConfig(command='command-that-must-not-run')},
            permission_manager=permissions,
        )

        await manager.ensure_connected()
        permissions.approval_handler = StaticApprovalHandler('allow_once')
        await manager.ensure_connected()

        connection = manager.connections['blocked']
        assert connection.state == 'disabled'
        assert connection.authorization_denied is True
        assert connection.session is None
        await manager.close()

    asyncio.run(run())
