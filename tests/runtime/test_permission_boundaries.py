'''Permission boundaries must survive replacing the runtime recovery loop.'''

import asyncio
from pathlib import Path

import pytest

from forge.permissions.policy import PermissionManager, PermissionRequest, PermissionRule
from forge.permissions.risk import classify_tool_call
from forge.runtime.state import ToolCall

@pytest.mark.parametrize('choice', ['allow_session', 'allow_project'])
def test_explicit_sandbox_approvals_cannot_create_remembered_grants(tmp_path, choice):
    from forge.permissions.policy import ApprovalResponse, PermissionManager, PermissionRequest, PermissionRule
    async def run():
        async def approve(request): return ApprovalResponse(choice)
        manager = PermissionManager(tmp_path, mode='ask', execution_mode='workspace-write',
            approval_handler=approve, load_stored_rules=False)
        manager.session_rules.append(PermissionRule('ask', 'file.write', 'target.txt'))
        result = await manager.authorize(PermissionRequest('write_file', 'file.write', 'low', ('target.txt',)))
        assert result.action == 'deny'
        assert len(manager.session_rules) == 1 and not manager.project_rules
        assert not manager.project_path.exists()
    asyncio.run(run())

def test_allow_rule_cannot_authorize_a_second_uncovered_target(tmp_path: Path) -> None:
    manager = PermissionManager(tmp_path, mode='supervised', user_path=tmp_path / 'user.json')
    manager.session_rules = [PermissionRule('allow', 'file.delete', 'build/cache')]
    request = PermissionRequest('remove_directory', 'file.delete', 'high', ('build/cache', 'src'))
    decision = asyncio.run(manager.authorize(request))
    assert decision.action == 'deny'
    assert decision.source == 'approval_unavailable'
    assert PermissionRule('deny', 'file.delete', 'src').matches(request)


@pytest.mark.parametrize('command', [
    'rm -r /',
    'rm -rf /app/..',
    'echo start;rm -rf /',
    "sh -c 'rm -rf /'",
    "bash -c 'rm -rf $TARGET'",
])
def test_broad_delete_stays_denied_through_shell_wrapping(command: str) -> None:
    request = classify_tool_call(ToolCall(0, 'probe', 'run_command', {'command': command}), 'process')
    assert request.hard_deny


def test_nested_explicit_cleanup_is_approvable() -> None:
    request = classify_tool_call(ToolCall(0, 'probe', 'verify', {
        'command': "sh -c 'rm -rf /app/build-cache && python check.py'",
    }), 'process')
    assert request.capability == 'file.delete'
    assert '/app/build-cache' in request.targets
    assert not request.hard_deny
