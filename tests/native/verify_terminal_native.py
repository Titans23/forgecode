"""Real native boundary acceptance. No mocked backend or model service."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from forge.api import HarnessSession, SessionOptions
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
            native = session.backend.observer.native
            proof = json.loads((native.control_root / 'boundary.json').read_text())
            assert proof['mode'] == mode
            assert proof['cleanup']['state'] == 'clean'
        assert session.cleanup['state'] == 'clean'
        assert not list(native.control_root.glob('temp-*'))
    asyncio.run(run())


def test_native_scoped_write_does_not_write_sibling(tmp_path):
    async def run():
        options = settings(tmp_path, 'workspace-write')
        allowed = options.root / 'src'
        allowed.mkdir()
        from dataclasses import replace
        async with await HarnessSession.open(options) as broad:
            assert broad.backend.observer.native.policy_hash
        assert broad.cleanup['state'] == 'clean'
        async with await HarnessSession.open(replace(options, write_roots=(allowed,))) as session:
            registry = session.conversation.registry
            for path, permitted in [('src/new.txt', True), ('sibling.txt', False)]:
                result = await session.backend.execute(ToolCall(0, 'fixture', 'write_file',
                    {'path': path, 'content': 'scoped'}), registry)
                assert result.success == permitted, result
            native = session.backend.observer.native
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
            native = session.backend.observer.native
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
            assert all(entry['discarded_bytes'] == 0 for entry in session.backend.observer.native._executions.values())
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
            native = session.backend.observer.native
        assert session.cleanup['state'] == 'clean'
        assert not list(native.control_root.glob('temp-*'))
        assert not (native.control_root / 'python-runtime').exists()
        assert not (options.root.parent / 'escape.txt').exists()
    asyncio.run(run())
