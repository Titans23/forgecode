"""One-call approval and real host execution; injected native failures are not OS evidence."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryDirectory, gettempdir

import pytest
from pydantic import ValidationError

from forge.execution import ExecutionRequest
from forge.permissions.policy import ApprovalResponse, PermissionManager, PermissionRule
from forge.protocol import ContractError
from forge.runtime.executor import ToolExecutor
from forge.runtime.state import ToolCall
from forge.runtime.workspace import WorkspaceTracker
from forge.sandbox.provider import open_native_execution
from forge.tools.base import ToolRegistry, ToolResult
from forge.tools.shell import RunCommandInput, RunCommandTool
from forge.tools.verify import VerifyInput, VerifyTool
from forge.tools.git import GitStatusInput, GitStatusTool, GitDiffInput, GitDiffTool, GitLogInput, GitLogTool


OVERRIDE = {'sandbox_permissions': 'danger-full-access', 'justification': 'Native setup denied WRITE_OWNER.'}


@pytest.mark.parametrize('model', [RunCommandInput, VerifyInput, GitStatusInput, GitDiffInput, GitLogInput])
@pytest.mark.parametrize('extra', [
    {'sandbox_permissions': 'danger-full-access'}, {'justification': 'reason'},
    {**OVERRIDE, 'justification': '  '}, {**OVERRIDE, 'sandbox_permissions': 'anything'},
])
def test_widening_requires_paired_valid_fields(model, extra):
    with pytest.raises(ValidationError):
        model.model_validate({**({'command': 'echo ok'} if model in (RunCommandInput, VerifyInput) else {}), **extra})


class RecordingBackend:
    def __init__(self, calls=None, mode='default'):
        self.calls = [] if calls is None else calls
        self.mode = mode

    async def execute(self, call, registry):
        self.calls.append((self.mode, call))
        return ToolResult.ok(self.mode)

    def open_approved(self, mode):
        return RecordingBackend(self.calls, mode)

    async def close_approved(self, backend):
        return {'state': 'clean'}


def executor(root, *, choice='allow_once', mode='supervised', sandbox='workspace-write', handler=True):
    prompts = []
    async def approve(request):
        prompts.append(request)
        return ApprovalResponse(choice)
    manager = PermissionManager(root, mode=mode, execution_mode=sandbox,
        approval_handler=approve if handler else None, load_stored_rules=False)
    manager.session_rules.append(PermissionRule('allow', '*', '*'))
    registry = ToolRegistry([RunCommandTool(root), VerifyTool(root, WorkspaceTracker(root)),
                            GitStatusTool(root), GitDiffTool(root), GitLogTool(root)])
    backend = RecordingBackend()
    return ToolExecutor(registry, manager, backend=backend), prompts


@pytest.mark.parametrize('tool', ['run_command', 'verify'])
def test_exact_one_call_ignores_stored_allow_and_preserves_default(tmp_path, tool):
    async def run():
        runtime, prompts = executor(tmp_path, mode='auto')
        arguments = {'command': 'python -', 'cwd': '.', 'stdin': 'print("exact")', 'timeout_seconds': 4}
        approved = await runtime.execute(ToolCall(0, 'one', tool, {**arguments, **OVERRIDE}))
        following = await runtime.execute(ToolCall(1, 'two', tool, arguments))
        assert approved.result.success and following.result.success
        assert len(prompts) == 1 and prompts[0].sandbox_mode == 'full-access'
        assert json.loads(prompts[0].preview) == {**arguments, **OVERRIDE}
        assert len(prompts[0].arguments_hash) == 64
        assert '宿主权限' in prompts[0].reason
        assert [(mode, call.arguments) for mode, call in runtime.backend.calls] == [
            ('full-access', arguments), ('default', arguments)]
        assert runtime.permission_manager.execution_mode == 'workspace-write'
        assert len(runtime.permission_manager.session_rules) == 1
    asyncio.run(run())


@pytest.mark.parametrize('options', [
    {'choice': 'deny'}, {'choice': 'allow_session'}, {'choice': 'allow_project'},
    {'handler': False}, {'mode': 'plan'},
])
def test_missing_one_shot_consent_never_executes(tmp_path, options):
    async def run():
        runtime, _ = executor(tmp_path, **options)
        result = await runtime.execute(ToolCall(0, 'one', 'run_command', {'command': 'echo ok', **OVERRIDE}))
        assert result.result.error.code == 'permission_denied'
        assert result.record.status == 'rejected'
        assert runtime.backend.calls == []
        assert len(runtime.permission_manager.session_rules) == 1
        assert not (tmp_path / '.forge' / 'permissions.json').exists()
    asyncio.run(run())


@pytest.mark.parametrize('case', ['deny_rule', 'scope', 'unsupported', 'narrower'])
def test_other_boundaries_still_precede_widening(tmp_path, case):
    async def run():
        runtime, prompts = executor(tmp_path, sandbox='full-access' if case == 'narrower' else 'workspace-write')
        arguments = {'command': 'sudo echo nope' if case == 'hard_deny' else 'echo ok', **OVERRIDE}
        if case == 'deny_rule':
            runtime.permission_manager.session_rules.append(PermissionRule('deny', '*', '*'))
        if case == 'unsupported':
            runtime.backend = None
        if case == 'narrower':
            arguments['sandbox_permissions'] = 'workspace-write'
        result = await runtime.execute(ToolCall(0, 'one', 'run_command', arguments),
            scope_checker=(lambda call: ToolResult.fail('scope_denied', 'out of scope')) if case == 'scope' else None)
        assert not result.result.success and result.record.status == 'rejected'
        assert not prompts
        assert runtime.backend is None or not runtime.backend.calls
    asyncio.run(run())


def test_read_only_can_widen_once_but_plan_cannot(tmp_path):
    async def run():
        runtime, prompts = executor(tmp_path, sandbox='read-only')
        arguments = {'command': 'echo ok', 'sandbox_permissions': 'workspace-write', 'justification': 'Write generated files.'}
        assert (await runtime.execute(ToolCall(0, 'one', 'run_command', arguments))).result.success
        assert runtime.backend.calls[0][0] == 'workspace-write'
        assert prompts[0].sandbox_mode == 'workspace-write'
        assert runtime.permission_manager.execution_mode == 'read-only'
        assert (await runtime.execute(ToolCall(1, 'two', 'run_command', {'command': 'echo ok'}))).result.success
        assert runtime.backend.calls[-1][0] == 'default'
    asyncio.run(run())


def test_repeating_current_mode_uses_ordinary_command_permissions(tmp_path):
    async def run():
        runtime, prompts = executor(tmp_path, sandbox='full-access')
        result = await runtime.execute(ToolCall(0, 'same', 'run_command', {'command': 'echo ok', **OVERRIDE}))
        assert result.result.success and not prompts
        assert runtime.backend.calls[0][0] == 'default'
    asyncio.run(run())


def test_cancelling_pending_approval_never_starts_command(tmp_path):
    async def run():
        runtime, _ = executor(tmp_path)
        asked = asyncio.Event()
        async def pending(request):
            asked.set()
            await asyncio.Event().wait()
        runtime.permission_manager.approval_handler = pending
        task = asyncio.create_task(runtime.execute(ToolCall(0, 'waiting', 'run_command', {'command': 'echo ok', **OVERRIDE})))
        await asyncio.wait_for(asked.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not runtime.backend.calls
    asyncio.run(run())


@pytest.mark.parametrize('tool', ['git_status', 'git_diff', 'git_log'])
def test_git_host_retry_denial_never_executes(tmp_path, tool):
    async def run():
        runtime, prompts = executor(tmp_path, choice='deny')
        result = await runtime.execute(ToolCall(0, 'git-denied', tool, OVERRIDE))
        assert result.result.error.code == 'permission_denied' and result.record.status == 'rejected'
        assert len(prompts) == 1 and runtime.backend.calls == []
        assert runtime.permission_manager.execution_mode == 'workspace-write'
    asyncio.run(run())


@pytest.mark.parametrize('tool', ['run_command', 'verify', 'git_status', 'git_diff', 'git_log'])
def test_real_host_command_after_native_failure_without_changing_session(tool):
    async def run(tmp_path):
        root, data = tmp_path / 'project', tmp_path / 'data'
        root.mkdir()
        if tool.startswith('git_'):
            subprocess.run(['git', 'init', '--quiet', str(root)], check=True)
            subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                            'commit', '--allow-empty', '-qm', 'fixture'], cwd=root, check=True)
            (root / 'note.txt').write_text('fixture')
        setup = await open_native_execution(ExecutionRequest(root, data, 'workspace-write', (), None, 30))
        frozen = deepcopy(setup.policy)
        native_attempts = []
        async def unavailable():
            native_attempts.append(True)
            error = ContractError('injected missing WRITE_OWNER', kind='SANDBOX_UNAVAILABLE')
            error.cleanup_report = {'state': 'clean'}
            raise error
        setup.backend._process_factory = unavailable
        runtime, prompts = executor(root)
        runtime.backend = setup.backend
        arguments = {'command': f'"{sys.executable}" -', 'stdin': 'import os\nprint("executed", os.getcwd())',
                     'cwd': '.', 'timeout_seconds': 10}
        if tool.startswith('git_'):
            arguments = {}
        try:
            first = await runtime.execute(ToolCall(0, 'native', tool, arguments))
            assert first.result.error.code == 'SANDBOX_UNAVAILABLE'
            assert first.result.error.details['command_started'] is False
            assert first.record.status == 'rejected'
            assert 'sandbox_permissions="danger-full-access"' in first.result.content
            second = await runtime.execute(ToolCall(1, 'approved', tool, {**arguments, **OVERRIDE}))
            assert second.result.success, second.result
            if not tool.startswith('git_'):
                assert 'executed' in second.result.content and str(root) in second.result.content
            assert second.result.metadata['sandbox_covered'] is False
            assert second.result.metadata['execution_mode'] == 'full-access'
            assert second.result.metadata['cleanup']['state'] == 'clean'
            assert second.result.metadata['sandbox_override'] == 'allow_once'
            if tool == 'verify':
                assert 'sandbox_permissions' not in second.result.metadata['check_spec']
            third = await runtime.execute(ToolCall(2, 'native-again', tool, arguments))
            assert third.result.error.code == 'SANDBOX_UNAVAILABLE'
            assert native_attempts == [True] and len(prompts) == 1
            assert setup.policy == frozen and setup.backend.mode == 'workspace-write'
        finally:
            assert (await setup.backend.close())['state'] == 'clean'
    # CI puts pytest's basetemp under the source installation, which the native
    # provider deliberately protects. Real command fixtures must live outside it.
    with TemporaryDirectory(prefix='forge-host-command-') as directory:
        root = Path(directory).resolve()
        assert root.parent == Path(gettempdir()).resolve()
        asyncio.run(run(root))


def test_actual_runner_failure_supplies_recovery_without_invalidating_environment(tmp_path):
    from types import SimpleNamespace
    from forge.sandbox.tool_backend import FileToolBackend
    class Client:
        native = SimpleNamespace(mode='workspace-write')
        workspace = {'canonical_path': str(tmp_path)}
        policy = {'execution_mode': 'workspace-write'}
        async def request(self, *args, **kwargs):
            raise ContractError('DSH sandbox unavailable; command not started. grantWrite: WRITE_OWNER',
                                kind='SANDBOX_UNAVAILABLE')
    async def run():
        runtime, prompts = executor(tmp_path)
        tracker = WorkspaceTracker(tmp_path)
        async def unexpected_refresh(*args):
            pytest.fail('A command that never started must not trigger a workspace refresh')
        tracker.refresh = unexpected_refresh
        runtime.workspace_tracker = tracker
        runtime.backend = FileToolBackend(Client())
        result = await runtime.execute(ToolCall(0, 'blocked', 'run_command', {'command': 'node --version'}))
        assert result.record.status == 'rejected' and tracker.environment_epoch == 0
        assert not prompts
        assert result.result.error.details['retry_with_unchanged_permissions'] is False
        assert 'not a command syntax failure' in result.result.content
        assert 'justification' in result.result.content and 'WRITE_OWNER' in result.result.error.message
    asyncio.run(run())


def test_cancellation_closes_the_temporary_command_backend(tmp_path):
    async def run():
        root = tmp_path / 'project'
        root.mkdir()
        setup = await open_native_execution(ExecutionRequest(root, tmp_path / 'data', 'workspace-write', (), None, 30))
        started, closed = asyncio.Event(), []
        class Running:
            async def execute(self, call, registry):
                started.set()
                await asyncio.Event().wait()
            async def close(self):
                closed.append(True)
                return {'state': 'clean'}
        setup.backend._mode_factory = lambda mode: Running()
        runtime, _ = executor(root)
        runtime.backend = setup.backend
        task = asyncio.create_task(runtime.execute(ToolCall(0, 'cancel', 'run_command', {'command': 'echo ok', **OVERRIDE})))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        outcome = await task
        assert outcome.result.error.code == 'execution_cancelled'
        assert closed == [True] and not setup.backend._temporary
        assert setup.backend._opening is None
        assert (await setup.backend.close())['state'] == 'clean'
    asyncio.run(run())


def test_unknown_temporary_cleanup_is_indeterminate_and_retained_on_close(tmp_path):
    async def run():
        root = tmp_path / 'project'
        root.mkdir()
        setup = await open_native_execution(ExecutionRequest(root, tmp_path / 'data', 'workspace-write', (), None, 30))
        class Unclean:
            async def execute(self, call, registry):
                return ToolResult.ok('ran', content='observed output')
            async def close(self):
                return {'state': 'unknown'}
        setup.backend._mode_factory = lambda mode: Unclean()
        runtime, _ = executor(root)
        runtime.backend = setup.backend
        result = await runtime.execute(ToolCall(0, 'unknown', 'run_command', {'command': 'echo ok', **OVERRIDE}))
        assert result.result.error.code == 'INDETERMINATE'
        assert result.record.status == 'indeterminate'
        assert result.result.content == 'observed output'
        assert (await setup.backend.close())['state'] == 'unknown'
    asyncio.run(run())
