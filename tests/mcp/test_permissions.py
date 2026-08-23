'''Permission boundaries specific to MCP connections and tools.'''

import asyncio
from pathlib import Path

from forge.mcp.config import StdioServerConfig
from forge.mcp.manager import MCPClientManager
from forge.permissions.approval import StaticApprovalHandler
from forge.permissions.policy import (
    ApprovalResponse,
    PermissionManager,
    PermissionRequest,
    PermissionRule,
)
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


def test_supervised_mode_allows_explicit_mcp_reads_without_approval(
    tmp_path: Path,
) -> None:
    permissions = PermissionManager(
        tmp_path,
        mode='supervised',
        user_path=tmp_path / 'missing.json',
    )

    decision = asyncio.run(
        permissions.authorize(
            PermissionRequest(
                tool_name='mcp__office__read',
                capability='mcp.read',
                risk='low',
            )
        )
    )

    assert decision.action == 'allow'
    assert decision.source == 'supervised'


def test_mcp_writes_always_require_exact_one_shot_approval(
    tmp_path: Path,
) -> None:
    approvals: list[str] = []

    async def approve(request: PermissionRequest) -> ApprovalResponse:
        approvals.append(request.arguments_hash)
        return ApprovalResponse('allow_session')

    permissions = PermissionManager(
        tmp_path,
        mode='auto',
        approval_handler=approve,
        user_path=tmp_path / 'missing.json',
    )
    permissions.session_rules.append(
        PermissionRule(
            action='allow',
            capability='mcp.write',
            target='*',
        )
    )
    first = PermissionRequest(
        tool_name='mcp__office__send',
        capability='mcp.write',
        risk='high',
        targets=('chat_id:one',),
        arguments_hash='hash-one',
    )
    second = PermissionRequest(
        tool_name='mcp__office__send',
        capability='mcp.write',
        risk='high',
        targets=('chat_id:one',),
        arguments_hash='hash-two',
    )

    assert asyncio.run(permissions.authorize(first)).action == 'allow'
    assert asyncio.run(permissions.authorize(second)).action == 'allow'
    assert approvals == ['hash-one', 'hash-two']
    assert len(permissions.session_rules) == 1


def test_mcp_write_audit_redacts_the_body_and_keeps_its_hash(
    tmp_path: Path,
) -> None:
    class Journal:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        def append(self, event: str, payload: dict) -> None:
            self.events.append((event, payload))

    journal = Journal()
    permissions = PermissionManager(
        tmp_path,
        mode='auto',
        approval_handler=StaticApprovalHandler('deny'),
        journal=journal,
        user_path=tmp_path / 'missing.json',
    )
    request = PermissionRequest(
        tool_name='mcp__office__send',
        capability='mcp.write',
        risk='high',
        preview='confidential announcement',
        arguments_hash='f' * 64,
    )

    asyncio.run(permissions.authorize(request))

    serialized = repr(journal.events)
    assert 'confidential announcement' not in serialized
    assert 'f' * 64 in serialized
