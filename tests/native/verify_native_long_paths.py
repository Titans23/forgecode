"""Actual Windows long artifacts with short private execution paths; no model calls."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

from forge.api import HarnessSession, RunProfile, SessionOptions
from forge.config import ForgeConfig
from forge.runtime.state import ToolCall
from forge.sandbox.windows_worker import grant_controller_access


@pytest.mark.parametrize('mode', ['read-only', 'workspace-write'])
def test_native_long_temp_path_keeps_write_boundary_and_cleans_up(tmp_path, mode):
    assert sys.platform == 'win32', 'Requires real Windows native ACL APIs'
    grant_controller_access(tmp_path)
    working = TemporaryDirectory(prefix='forge-long-workspace-', delete=False)
    root = Path(working.name)
    grant_controller_access(root)
    # Keep the original long artifact path; only managed runtime storage moves.
    prefix = tmp_path / 'control'
    padding = max(1, 174 - len(str(prefix)) - 1)
    data = prefix / ('d' * padding)
    data.mkdir(parents=True)
    (root / 'existing.txt').write_text('preserve', encoding='utf-8')
    options = SessionOptions(root, data_root=data, sandbox=mode,
        config=ForgeConfig(api_key='offline-only', model_id='no-model-called'),
        profile=RunProfile(intent_routing=False))

    async def exercise():
        async with await HarnessSession.open(options) as session:
            native = session.backend.observer.native
            boundary = json.loads((native.control_root / 'boundary.json').read_text())
            assert boundary['cleanup']['state'] == 'clean'
            read = await session.backend.execute(ToolCall(0, 'read', 'read_file',
                {'path': 'existing.txt'}), session.conversation.registry)
            assert read.success and 'preserve' in read.content, read
            temp = native.control_root / ('temp-exec-' + str(uuid4()))
            assert len(str(temp)) < 260
            assert len(str(native.diagnostic_root / temp.name)) > 260
            script = ("const fs=require('fs'),p=require('path');"
                      "const t=fs.mkdtempSync(p.toNamespacedPath(p.join(process.env.TEMP,'child-')));"
                      "fs.writeFileSync(p.join(t,'inside.txt'),'scratch');"
                      "let denied=false;try{fs.writeFileSync(process.argv[1],'escape')}catch{denied=true}"
                      "if(!denied)process.exit(3);console.log('scratch-ok-outside-denied');")
            identity = 'exec-' + str(uuid4())
            await native.execute(identity, {'mode': 'argv', 'argv': [str(native.runtime.node), '-e', script,
                str(data / 'outside.txt')], 'cwd': str(root), 'environment': {}, 'output_limit_bytes': 4096,
                'deadline_utc': (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat().replace('+00:00', 'Z')})
            frames = []
            while (await native.status(identity))['state'] == 'running':
                while not native.outputs.empty():
                    frames.append(native.outputs.get_nowait())
                await asyncio.sleep(0.02)
            while not native.outputs.empty():
                frames.append(native.outputs.get_nowait())
            status = await native.status(identity)
            (tmp_path / 'command-output.json').write_text(json.dumps({'status': status, 'frames': frames}, indent=2))
            assert status['exit_code'] == 0, (status, frames)
            cleanup = await native.cancel(identity, 'completed acceptance check', '')
            assert cleanup['confirmed'] and cleanup['cleanup']['state'] == 'clean'
            assert not (data / 'outside.txt').exists()
        assert session.cleanup['state'] == 'clean'
        assert not native.control_root.exists()
        locator = json.loads((native.diagnostic_root / 'control-location.json').read_text())
        assert locator['state'] == 'clean'
        assert locator['control_root'] == str(native.control_root)
        assert (native.diagnostic_root / 'boundary.json').exists()
        assert not list(native.control_root.glob('temp-*'))
        assert (root / 'existing.txt').read_text() == 'preserve'

    asyncio.run(exercise())
    working.cleanup()  # Failed/uncertain native attempts retain their owned workspace.


def test_native_short_runtime_sessions_cancel_independently(tmp_path):
    assert sys.platform == 'win32'
    grant_controller_access(tmp_path)
    working = TemporaryDirectory(prefix='forge-runtime-owners-', delete=False)
    root = Path(working.name)
    grant_controller_access(root)
    options = SessionOptions(root, data_root=tmp_path / ('d' * 90), sandbox='read-only',
        config=ForgeConfig(api_key='offline-only', model_id='no-model-called'),
        profile=RunProfile(intent_routing=False))

    async def exercise():
        async with await HarnessSession.open(options) as first:
            async with await HarnessSession.open(options) as second:
                one, two = first.backend.observer.native, second.backend.observer.native
                assert one.control_root != two.control_root
                identity = 'exec-' + str(uuid4())
                await one.execute(identity, {'mode': 'argv',
                    'argv': [str(one.runtime.node), '-e', 'setInterval(()=>{},1000)'],
                    'cwd': str(root), 'environment': {}, 'output_limit_bytes': 1024,
                    'deadline_utc': (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat().replace('+00:00', 'Z')})
                await asyncio.sleep(0.4)
                cancelled = await one.cancel(identity, 'acceptance', '')
                assert cancelled['confirmed'] and cancelled['cleanup']['state'] == 'clean'
                assert (await first.close())['state'] == 'clean'
                assert not one.control_root.exists()
                assert (two.control_root / 'boundary.json').exists()
            assert second.cleanup['state'] == 'clean'
            assert not two.control_root.exists()

    asyncio.run(exercise())
    working.cleanup()
