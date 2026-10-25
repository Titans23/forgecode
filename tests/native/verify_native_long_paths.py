"""Actual Windows long-control-path ACL admission and cleanup; no model calls."""

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
from forge.sandbox.windows_worker import grant_controller_access


@pytest.mark.parametrize('mode', ['read-only', 'workspace-write'])
def test_native_long_temp_path_keeps_write_boundary_and_cleans_up(tmp_path, mode):
    assert sys.platform == 'win32', 'Requires real Windows native ACL APIs'
    grant_controller_access(tmp_path)
    working = TemporaryDirectory(prefix='forge-long-workspace-', delete=False)
    root = Path(working.name)
    grant_controller_access(root)
    # Native session IDs add about 48 chars and each execution another 47.
    # Force the Win32 ACL target above MAX_PATH without changing system policy.
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
            temp = native.control_root / ('temp-exec-' + str(uuid4()))
            assert len(str(temp)) > 260
            # Node's mkdtemp itself also requires an extended Windows path;
            # the sandbox preserves canonical TEMP rather than rewriting all env.
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
        assert not list(native.control_root.glob('temp-*'))
        assert (root / 'existing.txt').read_text() == 'preserve'

    asyncio.run(exercise())
    working.cleanup()  # Failed/uncertain native attempts retain their owned workspace.
