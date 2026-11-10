"""Real DSH services on disposable directories; never alter a user's project ACL."""
import asyncio
from pathlib import Path
import sys

import pytest

from forge.api import HarnessSession, SessionOptions, RunProfile
from forge.config import ForgeConfig
from forge.runtime.state import ToolCall


class NoModel:
    model = 'offline'
    provider = 'anthropic'
    async def stream(self, *args, **kwargs):
        raise AssertionError('No model request is authorized by native acceptance')
        yield
    async def aclose(self):
        pass


def settings(tmp_path, mode):
    root = tmp_path / 'project'
    if sys.platform == 'win32':
        from forge.sandbox.windows_worker import grant_controller_access
        # Every directory here was created by this test, not an existing project.
        grant_controller_access(tmp_path)
    root.mkdir()
    if sys.platform == 'win32':
        grant_controller_access(root)
    (root / 'read.txt').write_text('owned fixture', encoding='utf-8')
    return SessionOptions(root, data_root=tmp_path / 'data', sandbox=mode,
        config=ForgeConfig(api_key='offline-only', model_id='offline'), profile=RunProfile(intent_routing=False))


@pytest.mark.parametrize('mode', ['read-only', 'workspace-write'])
def test_dsh_executes_actual_commands_without_a_forge_startup_probe(tmp_path, mode):
    async def run():
        options = settings(tmp_path, mode)
        async with await HarnessSession.open(options, model_client_factory=lambda *a, **k: NoModel()) as session:
            process_backend = await session.backend.prepare_process()
            native = process_backend.observer.native
            assert native._executions == {}
            assert not (native.control_root / 'boundary.json').exists()
            assert not list(options.root.glob('.forge-canary-*'))
            execute = session.conversation.tool_executor.execute
            read = await execute(ToolCall(0, 'read-command', 'run_command', {
                'command': 'python -', 'stdin': "from pathlib import Path\nprint(Path('read.txt').read_text())"}))
            assert read.result.success, read.result
            assert 'owned fixture' in read.result.content
            write = await execute(ToolCall(1, 'write-command', 'run_command', {
                'command': 'python -', 'stdin': "from pathlib import Path\nPath('written.txt').write_text('from DSH')"}))
            assert write.result.success == (mode == 'workspace-write'), write.result
            assert (options.root / 'written.txt').exists() == (mode == 'workspace-write')
            assert len(native._executions) == 2
        assert session.cleanup['state'] == 'clean', session.cleanup
    asyncio.run(run())


@pytest.mark.skipif(sys.platform != 'win32', reason='DSH Windows session temp capability')
def test_dsh_private_temp_is_shared_within_session_and_revoked_on_close(tmp_path):
    async def run():
        options = settings(tmp_path, 'workspace-write')
        paths = []
        async with await HarnessSession.open(options, model_client_factory=lambda *a, **k: NoModel()) as session:
            for index in range(2):
                result = await session.conversation.tool_executor.execute(ToolCall(index, str(index), 'run_command', {
                    'command': 'python -', 'stdin': "import os\nprint(os.environ['TEMP'])"}))
                assert result.result.success, result.result
                paths.append(result.result.content.split('stdout:\n', 1)[1].strip())
            assert paths[0] == paths[1] and Path(paths[0]).is_dir()
            assert not Path(paths[0]).is_relative_to(options.root)
        assert session.cleanup['state'] == 'clean'
        assert not Path(paths[0]).exists()
    asyncio.run(run())


@pytest.mark.parametrize('count', [0, 1, 2])
def test_explicit_native_write_roots_never_grant_sibling(tmp_path, count):
    from dataclasses import replace
    async def run():
        options = settings(tmp_path, 'workspace-write')
        roots = [options.root / name for name in ('one', 'two')]
        for root in roots:
            root.mkdir()
        async with await HarnessSession.open(replace(options, write_roots=tuple(roots[:count])),
                model_client_factory=lambda *a, **k: NoModel()) as session:
            result = await session.conversation.tool_executor.execute(ToolCall(0, 'scoped', 'run_command', {
                'command': 'python -', 'stdin': """from pathlib import Path
for name in ('one/value', 'two/value', 'outside'):
    try:
        Path(name).write_text('written')
    except PermissionError:
        pass
"""}))
            assert result.result.success, result.result
            for index, root in enumerate(roots):
                assert (root / 'value').exists() == (index < count)
            assert not (options.root / 'outside').exists()
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())
