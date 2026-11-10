"""Real native boundary acceptance. No mocked backend or model service."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from forge.api import HarnessSession, SessionOptions, RunProfile
from forge.config import ForgeConfig
from forge.runtime.state import ToolCall
from forge.sandbox.launcher import verify_runtime


@pytest.fixture(scope='module', autouse=True)
def native_fixture_acl(tmp_path_factory):
    import sys
    import os
    if os.environ.get('FORGE_EXPECT_WHEEL') == '1':
        import forge
        assert 'site-packages' in Path(forge.__file__).parts, forge.__file__
    if sys.platform == 'win32':
        from forge.sandbox.windows_worker import grant_controller_access
        grant_controller_access(tmp_path_factory.getbasetemp())


def settings(tmp_path, mode, **kwargs):
    import sys
    if sys.platform == 'win32':
        from forge.sandbox.windows_worker import grant_controller_access
        grant_controller_access(tmp_path)
    root = tmp_path / 'project'
    root.mkdir(exist_ok=True)
    if sys.platform == 'win32':
        from forge.sandbox.windows_worker import grant_controller_access
        grant_controller_access(root)
    (root / 'existing.txt').write_text('preserve', encoding='utf-8')
    return SessionOptions(root, data_root=tmp_path / 'data', sandbox=mode,
        config=ForgeConfig(api_key='offline-only', model_id='no-model-called'), **kwargs)


async def prepared_native(session):
    return (await session.backend.prepare_process()).observer.native


@pytest.mark.parametrize('mode', ['read-only', 'workspace-write'])
def test_native_file_write_scope_read_and_cleanup(tmp_path, mode):
    async def run():
        options = settings(tmp_path, mode)
        async with await HarnessSession.open(options) as session:
            registry = session.conversation.registry
            async def tool(name, args):
                return await session.backend.execute(ToolCall(0, 'fixture', name, args), registry)
            read = await tool('read_file', {'path': 'existing.txt'})
            assert read.success and 'preserve' in read.content
            write = await tool('write_file', {'path': 'new.txt', 'content': 'created'})
            assert write.success == (mode == 'workspace-write'), write
            outside = await tool('write_file', {'path': '../escape.txt', 'content': 'escape'})
            assert not outside.success
            assert not (options.root.parent / 'escape.txt').exists()
            native = await prepared_native(session)
            proof = json.loads((native.control_root / 'boundary.json').read_text())
            assert proof['mode'] == mode
            assert proof['cleanup']['state'] == 'clean'
        assert session.cleanup['state'] == 'clean'
        assert not list(native.control_root.glob('temp-*'))
    asyncio.run(run())


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows ACL prerequisite')
def test_cli_and_file_policy_work_without_write_owner_but_native_command_fails(tmp_path):
    """An owned, disposable directory reproduces the real grantWrite denial."""
    import os
    import subprocess
    import win32api
    import win32con
    import win32security
    from ntsecuritycon import FILE_ALL_ACCESS, WRITE_OWNER
    options = settings(tmp_path, 'workspace-write')
    root = str(options.root)
    original = win32security.GetNamedSecurityInfo(root, win32security.SE_FILE_OBJECT,
                                                win32security.DACL_SECURITY_INFORMATION)
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION,
        win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE, FILE_ALL_ACCESS & ~WRITE_OWNER, sid)
    try:
        win32security.SetNamedSecurityInfo(root, win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, acl, None)
        environment = {**os.environ, 'FORGE_PROVIDER': 'anthropic', 'FORGE_API_KEY': 'offline-only',
            'FORGE_MODEL': 'offline-only', 'FORGE_BASE_URL': 'http://127.0.0.1:9',
            'FORGE_DATA_DIR': str(options.data_root), 'FEISHU_ENABLED': '0'}
        result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'forge'], cwd=root,
            env=environment, input='', capture_output=True, encoding='utf-8', timeout=90)
        assert result.returncode == 0 and 'Session ended.' in result.stdout, result.stdout + result.stderr
        assert not list(options.data_root.glob('native/*/boundary-failure.json'))
        async def file_and_process():
            async with await HarnessSession.open(options) as session:
                registry = session.conversation.registry
                read = await session.backend.execute(ToolCall(0, 'read', 'read_file', {'path': 'existing.txt'}), registry)
                assert read.success and 'preserve' in read.content
                written = await session.backend.execute(ToolCall(0, 'write', 'write_file',
                    {'path': 'policy.txt', 'content': 'allowed by workspace policy'}), registry)
                assert written.success and written.metadata['execution_boundary'] == 'workspace-write-file-policy'
                denied = await session.backend.execute(ToolCall(0, 'outside', 'write_file',
                    {'path': '../outside.txt', 'content': 'denied'}), registry)
                assert not denied.success and not (tmp_path / 'outside.txt').exists()
                command = await session.backend.execute(ToolCall(0, 'run', 'run_command',
                    {'command': 'python -', 'stdin': "from pathlib import Path\nPath('must-not-run').touch()"}), registry)
                assert not command.success and command.error.code == 'SANDBOX_UNAVAILABLE'
                assert 'SetNamedSecurityInfoW failed (Win32 5)' in command.error.message
                assert not (options.root / 'must-not-run').exists()
                from forge.permissions.policy import ApprovalResponse, PermissionManager
                from forge.runtime.executor import ToolExecutor
                flags = (win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION
                         | win32security.LABEL_SECURITY_INFORMATION)
                def current_acl():
                    descriptor = win32security.GetNamedSecurityInfo(root, win32security.SE_FILE_OBJECT, flags)
                    return win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
                        descriptor, win32security.SDDL_REVISION_1, flags)
                before = current_acl()
                approvals = []
                async def approve(request):
                    approvals.append(request)
                    return ApprovalResponse('allow_once')
                manager = PermissionManager(options.root, mode='supervised', execution_mode='workspace-write',
                    approval_handler=approve, load_stored_rules=False)
                executor = ToolExecutor(registry, manager, backend=session.backend)
                for name in ('run_command', 'verify'):
                    approved = await executor.execute(ToolCall(0, name, name, {
                        'command': f'"{sys.executable}" -',
                        'stdin': "from pathlib import Path\nPath('approved.txt').write_text('once')\nprint('executed')",
                        'sandbox_permissions': 'danger-full-access', 'justification': 'Native ACL setup failed.'}))
                    assert approved.result.success, approved.result
                    assert approved.result.metadata['execution_mode'] == 'full-access'
                    assert approved.result.metadata['sandbox_covered'] is False
                assert len(approvals) == 2 and current_acl() == before
                assert (options.root / 'approved.txt').read_text() == 'once'
                assert manager.execution_mode == session.backend.mode == 'workspace-write'
                assert not (await session.backend.execute(ToolCall(0, 'still-native', 'run_command',
                    {'command': 'echo should-not-run'}), registry)).success
                still_readable = await session.backend.execute(ToolCall(0, 'read-after', 'read_file',
                    {'path': 'policy.txt'}), registry)
                assert still_readable.success
            assert session.cleanup['state'] == 'clean'
        asyncio.run(file_and_process())
        receipts = list(options.data_root.glob('native/*/boundary-failure.json'))
        assert len(receipts) == 1
        failure = json.loads(receipts[0].read_bytes())
        assert failure['exit_code'] == 125 and failure['cleanup']['state'] == 'clean'
        locator = json.loads(receipts[0].with_name('control-location.json').read_bytes())
        assert locator['state'] == 'clean' and not Path(locator['control_root']).exists()
        assert (options.root / 'existing.txt').read_text() == 'preserve'
    finally:
        inheritance = (win32security.PROTECTED_DACL_SECURITY_INFORMATION
            if original.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
            else win32security.UNPROTECTED_DACL_SECURITY_INFORMATION)
        win32security.SetNamedSecurityInfo(root, win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION | inheritance,
            None, None, original.GetSecurityDescriptorDacl(), None)


def test_read_only_command_can_receive_one_native_workspace_write_approval(tmp_path):
    from forge.permissions.policy import ApprovalResponse, PermissionManager
    from forge.runtime.executor import ToolExecutor
    async def run():
        options = settings(tmp_path, 'read-only')
        async def approve(request):
            assert request.sandbox_mode == 'workspace-write'
            return ApprovalResponse('allow_once')
        async with await HarnessSession.open(options) as session:
            manager = PermissionManager(options.root, execution_mode='read-only',
                approval_handler=approve, load_stored_rules=False)
            executor = ToolExecutor(session.conversation.registry, manager, backend=session.backend)
            result = await executor.execute(ToolCall(0, 'write-once', 'run_command', {
                'command': 'python -', 'stdin': "from pathlib import Path\nPath('one.txt').write_text('native')",
                'sandbox_permissions': 'workspace-write', 'justification': 'Generate one task file.'}))
            assert result.result.success, result.result
            assert result.result.metadata['execution_mode'] == 'workspace-write'
            assert result.result.metadata['execution_boundary'] == 'workspace-write-file-worker'
            assert (options.root / 'one.txt').read_text() == 'native'
            assert manager.execution_mode == session.backend.mode == 'read-only'
            assert not session.backend._temporary and session.backend._opening is None
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_plugin_selected_native_provider_owns_backend_until_cleanup(tmp_path):
    from dataclasses import replace
    from forge.agents import AgentDefinition, PluginSpec
    options = settings(tmp_path, 'workspace-write')
    plugin = tmp_path / 'execution-plugin'
    plugin.mkdir()
    (plugin / 'forge-plugin.json').write_text(json.dumps({
        'id': 'execution', 'api_version': 1, 'entry_point': 'plugin.py:activate'}))
    (plugin / 'plugin.py').write_text('''
from forge.execution import ExecutionSetup
from forge.sandbox.provider import open_native_execution
def activate(ctx, config):
    log = ctx.data_dir / "close-order"
    def dispose():
        assert log.read_text() == "backend"
        log.write_text("backend,plugin")
    ctx.effect(dispose)
    async def open_execution(request):
        setup = await open_native_execution(request)
        class ObservedBackend:
            mode = setup.backend.mode
            observer = setup.backend.observer
            check_ready = setup.backend.check_ready
            execute = setup.backend.execute
            snapshot = setup.backend.snapshot
            async def close(self):
                report = await setup.backend.close()
                assert report["state"] == "clean"
                log.write_text("backend")
                return report
        return ExecutionSetup(ObservedBackend(), setup.policy)
    ctx.register_sandbox_provider("custom-native", open_execution)
''', encoding='utf-8')
    async def run():
        definition = AgentDefinition(sandbox_provider='custom-native',
            plugins=(PluginSpec(str(plugin), trusted=True),))
        async with await HarnessSession.open(replace(options, agent=definition)) as session:
            result = await session.backend.execute(ToolCall(0, 'write', 'write_file',
                {'path': 'new.txt', 'content': 'native plugin write'}), session.conversation.registry)
            assert result.success, result
            denied = await session.backend.execute(ToolCall(0, 'outside', 'write_file',
                {'path': '../outside.txt', 'content': 'denied'}), session.conversation.registry)
            assert not denied.success and not (tmp_path / 'outside.txt').exists()
            command = await session.backend.execute(ToolCall(0, 'process', 'run_command',
                {'command': 'python -', 'stdin': "from pathlib import Path\nassert Path('new.txt').read_text() == 'native plugin write'"}),
                session.conversation.registry)
            assert command.success, command
        assert session.cleanup['state'] == 'clean'
        assert (options.data_root / 'plugins/execution/close-order').read_text() == 'backend,plugin'
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['read-only', 'workspace-write'])
def test_native_linked_workspace_checks_actual_targets_without_tree_rejection(tmp_path, mode):
    import subprocess
    from forge.runtime.state import ModelToolCallCompleted, ModelResponseCompleted
    options = settings(tmp_path, mode, profile=RunProfile(intent_routing=False))
    inside, outside = options.root / 'inside', tmp_path / 'outside'
    inside.mkdir()
    outside.mkdir()
    (outside / 'reference.txt').write_text('outside read', encoding='utf-8')
    links = [(options.root / 'internal-link', inside), (options.root / 'external-link', outside)]
    for link, target in links:
        if sys.platform == 'win32':
            subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                           check=True, capture_output=True)
        else:
            link.symlink_to(target, target_is_directory=True)
    class Scripted:
        model = 'offline-fixture'
        count = 0
        async def stream(self, **kwargs):
            calls = [
                ('write_file', {'path': 'internal-link/from-agent.txt', 'content': 'agent write'}),
                ('verify', {'command': 'python -', 'stdin': "from pathlib import Path\nassert Path('internal-link/from-agent.txt').read_text() == 'agent write'\n"}),
                ('finish_task', {'task_kind': 'change', 'status': 'completed', 'summary': 'Created and verified the file.'}),
            ]
            name, arguments = calls[self.count]
            self.count += 1
            yield ModelToolCallCompleted(ToolCall(0, str(self.count), name, arguments))
            yield ModelResponseCompleted('tool_use')
    async def run():
        from forge.permissions.policy import ApprovalResponse
        async def approve(request):
            return ApprovalResponse('allow_once')
        model = Scripted()
        async with await HarnessSession.open(options, model_client_factory=lambda *a, **k: model,
                approval_handler=approve) as session:
            async def tool(name, arguments):
                return await session.backend.execute(ToolCall(0, name, name, arguments), session.conversation.registry)
            read = await tool('read_file', {'path': 'external-link/reference.txt'})
            assert read.success and 'outside read' in read.content, read
            write = await tool('write_file', {'path': 'internal-link/new.txt', 'content': 'inside'})
            assert write.success == (mode == 'workspace-write'), write
            denied = await tool('write_file', {'path': 'external-link/new.txt', 'content': 'escape'})
            assert not denied.success and not (outside / 'new.txt').exists(), denied
            scan = await session.backend.observer.scan()
            assert scan['complete'], scan
            assert scan['files']['external-link']['is_link']
            assert not any(path.startswith('external-link/') for path in scan['files'])
            if mode == 'workspace-write':
                result = await session.run('Create internal-link/from-agent.txt containing agent write and verify it.')
                assert result.status == 'completed', result
                assert model.count == 3
                assert (inside / 'from-agent.txt').read_text() == 'agent write'
            native = await prepared_native(session)
            # Exercise the actual OS boundary without the file-tool guard.
            script = "const fs=require('fs');for(const p of process.argv.slice(1)){try{fs.writeFileSync(p,'native');console.log('write')}catch{console.log('denied')}}"
            command = {'mode': 'argv', 'argv': [str(verify_runtime().node), '-e', script,
                str(options.root / 'internal-link/os.txt'), str(options.root / 'external-link/os.txt')],
                'cwd': str(options.root), 'environment': {}, 'output_limit_bytes': 1024,
                'deadline_utc': (datetime.now(timezone.utc) + timedelta(seconds=15)).isoformat().replace('+00:00', 'Z')}
            execution_id = 'exec-22222222-2222-4222-8222-222222222222'
            await native.execute(execution_id, command)
            while (await native.status(execution_id))['state'] == 'running':
                await asyncio.sleep(.02)
            assert (await native.status(execution_id))['exit_code'] == 0
            assert (inside / 'os.txt').exists() == (mode == 'workspace-write')
            assert not (outside / 'os.txt').exists()
        assert session.cleanup['state'] == 'clean'
    try:
        asyncio.run(run())
    finally:
        for link, _ in links:
            link.rmdir() if sys.platform == 'win32' else link.unlink()


def test_native_cli_default_starts_in_workspace_with_junction(tmp_path):
    import os
    import subprocess
    options = settings(tmp_path, 'workspace-write')
    outside = tmp_path / 'reference'
    outside.mkdir()
    link = options.root / 'node_modules'
    if sys.platform == 'win32':
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(outside)], check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    environment = {key: value for key, value in os.environ.items() if key.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'HOME', 'USERPROFILE',
        'APPDATA', 'LOCALAPPDATA', 'PATHEXT'}}
    environment.update(FORGE_DATA_DIR=str(options.data_root), FORGE_PROVIDER='anthropic',
        FORGE_API_KEY='test-only', FORGE_MODEL='offline-fixture', FORGE_BASE_URL='http://127.0.0.1:1')
    try:
        result = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-m', 'forge'], cwd=options.root,
            env=environment, input='', capture_output=True, text=True, encoding='utf-8', timeout=45)
        assert result.returncode == 0 and 'Session ended.' in result.stdout, result.stdout + result.stderr
    finally:
        link.rmdir() if sys.platform == 'win32' else link.unlink()


def test_native_scoped_write_does_not_write_sibling(tmp_path):
    async def run():
        options = settings(tmp_path, 'workspace-write')
        allowed = options.root / 'src'
        allowed.mkdir()
        from dataclasses import replace
        async with await HarnessSession.open(options) as broad:
            assert (await prepared_native(broad)).policy_hash
        assert broad.cleanup['state'] == 'clean'
        async with await HarnessSession.open(replace(options, write_roots=(allowed,))) as session:
            registry = session.conversation.registry
            for path, permitted in [('src/new.txt', True), ('sibling.txt', False)]:
                result = await session.backend.execute(ToolCall(0, 'fixture', 'write_file',
                    {'path': path, 'content': 'scoped'}), registry)
                assert result.success == permitted, result
            native = await prepared_native(session)
            runtime = verify_runtime()
            # Bypass Python's file guard: OS boundary must also refuse the sibling.
            script = "const fs=require('fs');try{fs.writeFileSync(process.argv[1],'escape');process.exit(3)}catch{console.log('denied')}"
            command = {'mode': 'argv', 'argv': [str(runtime.node), '-e', script, str(options.root / 'os-escape.txt')],
                'cwd': str(options.root), 'environment': {}, 'output_limit_bytes': 1024,
                'deadline_utc': (datetime.now(timezone.utc) + timedelta(seconds=15)).isoformat().replace('+00:00', 'Z')}
            await native.execute('exec-11111111-1111-4111-8111-111111111111', command)
            while (await native.status('exec-11111111-1111-4111-8111-111111111111'))['state'] == 'running':
                await asyncio.sleep(0.02)
            status = await native.status('exec-11111111-1111-4111-8111-111111111111')
            assert status['exit_code'] == 0, status
            assert not (options.root / 'os-escape.txt').exists()
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_native_helper_command_git_and_additional_read_scope(tmp_path):
    async def run():
        options = settings(tmp_path, 'workspace-write')
        outside = tmp_path / 'reference'
        outside.mkdir()
        (outside / 'reference.txt').write_text('reference data', encoding='utf-8')
        from dataclasses import replace
        async with await HarnessSession.open(replace(options, read_roots=(outside,))) as session:
            registry = session.conversation.registry
            result = await session.backend.execute(ToolCall(0, 'read', 'read_file',
                {'path': str(outside / 'reference.txt')}), registry)
            assert result.success and 'reference data' in result.content, result
            command = await session.backend.execute(ToolCall(0, 'init', 'run_command',
                {'command': 'git init --quiet'}), registry)
            assert command.success, command
            script = "from pathlib import Path;Path('build').mkdir();Path('build/result.txt').write_text('real build output')"
            build = await session.backend.execute(ToolCall(0, 'build', 'run_command',
                {'command': 'python -', 'stdin': script}), registry)
            assert build.success and (options.root / 'build/result.txt').read_text() == 'real build output', build
            status = await session.backend.execute(ToolCall(0, 'status', 'git_status', {}), registry)
            assert status.success, status
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_native_cancellation_reconciles_process_tree(tmp_path):
    async def run():
        async with await HarnessSession.open(settings(tmp_path, 'workspace-write')) as session:
            native = await prepared_native(session)
            command = {'mode': 'argv', 'argv': [str(verify_runtime().node), '-e', 'setInterval(()=>{},1000)'],
                'cwd': native.workspace['canonical_path'], 'environment': {}, 'output_limit_bytes': 1024,
                'deadline_utc': (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat().replace('+00:00', 'Z')}
            identity = 'exec-11111111-1111-4111-8111-111111111112'
            await native.execute(identity, command)
            await asyncio.sleep(0.4)
            result = await native.cancel(identity, 'acceptance', command['deadline_utc'])
            assert result['confirmed'] and result['cleanup']['state'] == 'clean', result
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_native_large_checkpoint_preserves_edit_and_existing_user_changes(tmp_path):
    """Actual helper protocol, not a model/capability benchmark."""
    from dataclasses import replace
    from hashlib import sha256
    async def run():
        options = settings(tmp_path, 'workspace-write')
        source = options.root / 'src'
        source.mkdir()
        large = source / 'large.py'
        large.write_bytes(b'# source data\n' * 24000 + b'# unique marker\n')
        prior = source / 'user.py'
        prior.write_bytes(b'# preexisting user edit\n')
        async with await HarnessSession.open(replace(options, write_roots=(source,))) as session:
            tracker = session.conversation.workspace_tracker
            await tracker.begin_turn()
            assert tracker.available
            await tracker.watch_paths_async(('src/large.py',))
            changed = await session.backend.execute(ToolCall(0, 'edit', 'replace_text',
                {'path': 'src/large.py', 'old_text': '# unique marker', 'new_text': '# changed marker'}),
                session.conversation.registry)
            assert changed.success, changed
            await tracker.refresh_paths(('src/large.py',))
            assert tracker.available and tracker.changed_paths == ('src/large.py',)
            snapshot = await session.backend.observer.snapshot(('src/large.py',))
            value = snapshot['files']['src/large.py']
            assert snapshot['complete']
            assert base64.b64decode(value['content_base64']) == large.read_bytes()
            assert value['sha256'] == sha256(large.read_bytes()).hexdigest()
            assert prior.read_bytes() == b'# preexisting user edit\n'
            denied = await session.backend.execute(ToolCall(0, 'deny', 'write_file',
                {'path': 'outside.txt', 'content': 'escape'}), session.conversation.registry)
            assert not denied.success and not (options.root / 'outside.txt').exists()
            assert session.backend.observer.native is None  # No native process is needed for file policy.
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_native_git_observation_tracks_command_edits_to_ignored_files(tmp_path):
    import subprocess
    import shutil
    async def run():
        options = settings(tmp_path, 'workspace-write')
        git = shutil.which('git')
        assert git is not None
        subprocess.run([git, 'init', '--quiet', str(options.root)], check=True)
        (options.root / '.gitignore').write_text('ignored.txt\n')
        (options.root / 'ignored.txt').write_text('prior user content\n')
        async with await HarnessSession.open(options) as session:
            tracker = session.conversation.workspace_tracker
            await tracker.begin_turn()
            assert tracker.available and not tracker.git_available
            # No watch_paths: an arbitrary command does not declare edit targets.
            changed = await session.backend.execute(ToolCall(0, 'command', 'run_command',
                {'command': 'python -', 'stdin': "from pathlib import Path;Path('ignored.txt').write_text('changed by command\\n')"}),
                session.conversation.registry)
            assert changed.success, changed
            await tracker.refresh()
            assert tracker.available and tracker.changed_paths == ('ignored.txt',)
            assert (options.root / 'existing.txt').read_text() == 'preserve'
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_native_public_session_continues_with_structured_events(tmp_path):
    from forge.api import event_dict
    from forge.runtime.state import ModelTextDelta, ModelResponseCompleted
    class OfflineClient:
        model = 'offline-native'
        async def stream(self, *args, **kwargs):
            yield ModelTextDelta('offline native response')
            yield ModelResponseCompleted('end_turn')
        async def aclose(self):
            pass
    async def run():
        async with await HarnessSession.open(settings(tmp_path, 'workspace-write'),
                model_client_factory=lambda *args, **kwargs: OfflineClient()) as session:
            assert (await session.run('hello')).text == 'offline native response'
            events = [event_dict(event) async for event in session.stream('continue')]
            assert events[-1]['type'] == 'TurnCompleted'
            assert events[-1]['payload']['result']['text'] == 'offline native response'
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows Python cache/ACL compatibility')
@pytest.mark.parametrize('mode', ['read-only', 'workspace-write'])
def test_native_pytest_default_private_cache_preserves_real_failures(tmp_path, mode):
    async def run():
        options = settings(tmp_path, mode)
        (options.root/'test_cache.py').write_text(
            'def test_pass():\n    assert True\ndef test_fail():\n    assert False, "visible real failure"\n')
        script = '''import pytest, json, os, sys
from pathlib import Path
class Audit:
    @pytest.hookimpl(trylast=True)
    def pytest_configure(self, config):
        scope = Path(json.loads(os.environ['FORGE_NATIVE_SCOPE'])['temp']).resolve()
        assert config.pluginmanager.hasplugin('cacheprovider')
        assert config.cache._cachedir.resolve().is_relative_to(scope)
        print('owned pytest cache enabled')
sys.exit(pytest.main(['-q', 'test_cache.py'], plugins=[Audit()]))
'''
        async with await HarnessSession.open(options) as session:
            result = await session.backend.execute(ToolCall(0,'cache','run_command',
                {'command':'python -','stdin':script,'timeout_seconds':30}),session.conversation.registry)
            assert not result.success and result.metadata['exit_code']==1, result
            assert not result.metadata['timed_out']
            assert 'owned pytest cache enabled' in result.content and 'visible real failure' in result.content
            assert '1 failed, 1 passed' in result.content
            assert not (options.root/'.pytest_cache').exists()
        assert session.cleanup['state']=='clean'
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['read-only', 'workspace-write'])
def test_native_python_private_temporary_directory_and_cleanup(tmp_path, mode):
    async def run():
        options = settings(tmp_path, mode)
        script = """
from pathlib import Path
import tempfile, os, subprocess, sys, json
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory)
    (path / 'nested').mkdir()
    (path / 'nested/result').write_text('temporary data')
    assert (path / 'nested/result').read_text() == 'temporary data'
    if os.name == 'nt':
        import win32security
        scope = json.loads(os.environ['FORGE_NATIVE_SCOPE'])
        acl = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
        allowed = {win32security.ConvertSidToStringSid(acl.GetAce(i)[2]) for i in range(acl.GetAceCount()) if acl.GetAce(i)[0][0] == 0}
        assert scope['tempSid'] in allowed
        assert not allowed.intersection({'S-1-1-0','S-1-5-11','S-1-5-32-545','S-1-5-4'})
assert not path.exists()
subprocess.run([sys.executable, '-I', '-c', 'import tempfile; t=tempfile.TemporaryDirectory(); t.cleanup()'], check=True)
try:
    Path('../escape.txt').write_text('must be denied')
except OSError:
    pass
else:
    raise AssertionError('OS write scope was widened')
print('private temporary directory passed')
"""
        (options.root / 'temp_check.py').write_text(script, encoding='utf-8')
        async with await HarnessSession.open(options) as session:
            for command in ('python temp_check.py', 'python -I temp_check.py'):
                result = await session.backend.execute(ToolCall(0, 'temp', 'run_command',
                    {'command': command, 'timeout_seconds': 15}), session.conversation.registry)
                assert result.success and 'private temporary directory passed' in result.content, result
            native = await prepared_native(session)
        assert session.cleanup['state'] == 'clean'
        assert not list(native.control_root.glob('temp-*'))
        assert not (native.control_root / 'python-runtime').exists()
        assert not (options.root.parent / 'escape.txt').exists()
    asyncio.run(run())
