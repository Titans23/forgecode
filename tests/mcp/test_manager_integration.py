'''Real stdio and Streamable HTTP integration tests for MCPClientManager.'''

import asyncio
from pathlib import Path
import socket
import sys

from forge.mcp.config import (
    HTTPServerConfig,
    InternalStdioServerConfig,
    StdioServerConfig,
)
from forge.mcp.manager import MCPClientManager
from forge.permissions.approval import StaticApprovalHandler
from forge.permissions.policy import PermissionManager
from forge.tools.base import ToolRegistry


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / 'examples' / 'mcp_server.py'


def permission_manager(root: Path) -> PermissionManager:
    return PermissionManager(
        root,
        mode='supervised',
        approval_handler=StaticApprovalHandler('allow_session'),
        user_path=root / 'missing-user-permissions.json',
    )


def test_real_stdio_server_lists_and_calls_tools(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ToolRegistry()
        manager = MCPClientManager(
            tmp_path,
            registry,
            {
                'example': StdioServerConfig(
                    command=sys.executable,
                    args=(str(SERVER),),
                )
            },
            permission_manager=permission_manager(tmp_path),
        )
        try:
            await manager.ensure_connected()
            assert 'mcp__example__echo' in registry.names
            result = await registry.execute(
                'mcp__example__echo',
                {'message': 'stdio works'},
            )
            assert result.success
            assert 'stdio works' in result.content
            assert 'ready' in manager.status()
        finally:
            await manager.close()

    asyncio.run(run())


def test_bundled_office_sidecar_lists_only_narrow_tools(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        registry = ToolRegistry()
        manager = MCPClientManager(
            tmp_path,
            registry,
            {
                'office': InternalStdioServerConfig(
                    command=sys.executable,
                    args=('-m', 'forge.office.mcp_server'),
                    env={'APP_ID': 'test-id', 'APP_SECRET': 'test-secret'},
                )
            },
            permission_manager=PermissionManager(
                tmp_path,
                mode='auto',
                user_path=tmp_path / 'missing-user-permissions.json',
            ),
        )
        try:
            await manager.ensure_connected()
            assert set(registry.names) == {
                'mcp__office__feishu_document_create',
                'mcp__office__feishu_document_read',
                'mcp__office__feishu_document_update',
                'mcp__office__feishu_message_send',
            }
        finally:
            await manager.close()

    asyncio.run(run())


def test_real_streamable_http_server_lists_and_calls_tools(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        port = available_port()
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SERVER),
            '--transport',
            'streamable-http',
            '--port',
            str(port),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        manager = None
        try:
            await wait_for_port(port)
            registry = ToolRegistry()
            manager = MCPClientManager(
                tmp_path,
                registry,
                {
                    'example-http': HTTPServerConfig(
                        type='http',
                        url=f'http://127.0.0.1:{port}/mcp',
                    )
                },
                permission_manager=permission_manager(tmp_path),
            )
            await manager.ensure_connected()
            assert 'mcp__example-http__add' in registry.names
            result = await registry.execute(
                'mcp__example-http__add',
                {'left': 2, 'right': 5},
            )
            assert result.success
            assert '7' in result.content
        finally:
            if manager is not None:
                await manager.close()
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()

    asyncio.run(run())


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return int(listener.getsockname()[1])


async def wait_for_port(port: int) -> None:
    for _ in range(100):
        try:
            reader, writer = await asyncio.open_connection(
                '127.0.0.1',
                port,
            )
        except OSError:
            await asyncio.sleep(0.05)
            continue
        writer.close()
        await writer.wait_closed()
        del reader
        return
    raise AssertionError(f'MCP HTTP server did not start on port {port}.')
