"""DSH file access and one-call grants through the real helper; no ACL mutation."""
import asyncio
import json
from pathlib import Path
import sys

import pytest

from forge.execution import ExecutionRequest
from forge.permissions.policy import ApprovalResponse, PermissionManager, PermissionRule
from forge.runtime.executor import ToolExecutor
from forge.runtime.state import ToolCall
from forge.sandbox.provider import open_host_execution, open_native_execution, platform_temp_roots
from forge.sessions.checkpoint import CheckpointStore
from forge.tools import create_default_registry
from forge.tools.base import SandboxInput


async def runtime_at(root, data, *, mode='workspace-write', approval='ask', accept=True):
    root.mkdir(exist_ok=True)
    setup = await open_native_execution(ExecutionRequest(root, data, mode, (), None))
    prompts = []
    async def approve(request):
        prompts.append(request)
        return ApprovalResponse('allow_once' if accept else 'deny')
    permissions = PermissionManager(root, mode=approval, execution_mode=mode,
                                    approval_handler=approve, load_stored_rules=False)
    registry = create_default_registry(root, tool_backend=setup.backend)
    checkpoints = CheckpointStore(root, data / 'checkpoints')
    executor = ToolExecutor(registry, permissions, backend=setup.backend, checkpoint_store=checkpoints)
    return executor, prompts


def call(name, **arguments):
    return ToolCall(0, name, name, arguments)


@pytest.mark.parametrize('name', ['.env', 'credentials.json', '.forge/notes.txt', '.git/example.txt'])
def test_file_access_has_no_implicit_sensitive_name_deny(tmp_path, name):
    async def run():
        runtime, prompts = await runtime_at(tmp_path / 'project', tmp_path / 'data')
        try:
            content = '{}' if name.endswith('.json') else 'owned fixture only'
            result = await runtime.execute(call('write_file', path=name, content=content),
                                           checkpoint_id=runtime.checkpoint_store.begin())
            assert result.result.success, result.result
            read = await runtime.execute(call('read_file', path=name))
            assert read.result.success and content in read.result.content
            assert not prompts and runtime.backend._opening is None
        finally:
            assert (await runtime.backend.close())['state'] == 'clean'
    asyncio.run(run())


@pytest.mark.parametrize('mode,target', [('read-only', 'workspace-write'), ('workspace-write', 'danger-full-access')])
def test_file_grant_covers_checkpoint_and_write_but_not_following_call(tmp_path, mode, target):
    async def run():
        root, data = tmp_path / 'project', tmp_path / 'data'
        runtime, prompts = await runtime_at(root, data, mode=mode)
        destination = root / 'item.txt' if mode == 'read-only' else tmp_path / 'outside.txt'
        destination.write_text('before', encoding='utf-8')
        checkpoint = runtime.checkpoint_store.begin()
        try:
            denied = await runtime.execute(call('write_file', path=str(destination), content='denied'))
            assert not denied.result.success and destination.read_text() == 'before'
            assert (await runtime.execute(call('read_file', path=str(destination)))).result.success
            result = await runtime.execute(call('write_file', path=str(destination), content='after',
                sandbox_permissions=target, justification='Update this one owned fixture.'), checkpoint_id=checkpoint)
            assert result.result.success, result.result
            assert destination.read_text() == 'after'
            assert len(prompts) == 1 and prompts[0].sandbox_mode in ('workspace-write', 'full-access')
            assert json.loads(prompts[0].preview)['path'] == str(destination)
            manifest = runtime.checkpoint_store._load_manifest(checkpoint)
            assert len(manifest['files']) == 1
            entry = next(iter(manifest['files'].values()))
            assert entry['before_sha256'] != entry['after_sha256']
            assert runtime.backend.mode == runtime.permission_manager.execution_mode == mode
            again = await runtime.execute(call('write_file', path=str(destination), content='not granted'))
            assert not again.result.success and destination.read_text() == 'after'
            assert runtime.backend._opening is None
        finally:
            assert (await runtime.backend.close())['state'] == 'clean'
    asyncio.run(run())


@pytest.mark.parametrize('approval,accept', [('never', True), ('ask', False)])
def test_file_escalation_without_consent_preserves_file(tmp_path, approval, accept):
    async def run():
        root = tmp_path / 'project'
        runtime, prompts = await runtime_at(root, tmp_path / 'data', mode='read-only', approval=approval, accept=accept)
        try:
            result = await runtime.execute(call('write_file', path='unwritten.txt', content='no',
                sandbox_permissions='workspace-write', justification='Request the exact write.'))
            assert result.result.error.code == 'permission_denied'
            assert not (root / 'unwritten.txt').exists()
            assert len(prompts) == (0 if approval == 'never' else 1)
        finally:
            await runtime.backend.close()
    asyncio.run(run())


def test_explicit_deny_rules_remain_in_force(tmp_path):
    async def run():
        runtime, prompts = await runtime_at(tmp_path / 'project', tmp_path / 'data')
        runtime.permission_manager.session_rules.append(PermissionRule('deny', 'file.write', 'blocked.txt'))
        try:
            result = await runtime.execute(call('write_file', path='blocked.txt', content='no',
                sandbox_permissions='danger-full-access', justification='Still subject to the explicit deny.'))
            assert result.result.error.code == 'permission_denied' and not prompts
        finally:
            await runtime.backend.close()
    asyncio.run(run())


def test_every_file_mutation_advertises_the_shared_grant_schema(tmp_path):
    registry = create_default_registry(tmp_path)
    for name in ('write_file', 'write_file_chunk', 'replace_text', 'apply_patch', 'create_directory', 'remove_directory'):
        assert issubclass(registry.implementation(name).input_model, SandboxInput)


def test_file_policy_includes_existing_platform_temp_roots(tmp_path):
    async def run():
        runtime, _ = await runtime_at(tmp_path / 'project', tmp_path / 'data')
        try:
            writes = set(map(Path, runtime.backend.observer.policy['filesystem']['write_roots']))
            assert set(platform_temp_roots()) <= writes
        finally:
            await runtime.backend.close()
    asyncio.run(run())


def test_host_command_is_not_rejected_by_keyword_or_shell_read_write_heuristics(tmp_path):
    async def run():
        root = tmp_path / 'project'
        root.mkdir()
        setup = await open_host_execution(ExecutionRequest(root, tmp_path / 'data', 'full-access', (), None))
        permissions = PermissionManager(root, mode='ask', execution_mode='full-access', load_stored_rules=False)
        executor = ToolExecutor(create_default_registry(root, tool_backend=setup.backend), permissions, backend=setup.backend)
        try:
            result = await executor.execute(call('run_command', command=f'"{sys.executable}" -',
                stdin="from pathlib import Path\nPath('.env').write_text('fixture')\nprint(Path('.env').read_text())\nprint('git reset --hard; sudo')"))
            assert result.result.success, result.result
            assert 'fixture' in result.result.content and 'git reset --hard; sudo' in result.result.content
        finally:
            assert (await setup.backend.close())['state'] == 'clean'
    asyncio.run(run())
