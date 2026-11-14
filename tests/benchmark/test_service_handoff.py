'''Offline Linux process ownership and service handoff; no task containers or models.'''
import asyncio
import json
import os
from pathlib import Path
import socket
import sys

import pytest

from forge.sandbox.process_owner import LocalProcessOwner


def test_patch_timeout_reaps_git_children_and_retains_separate_diagnostic(tmp_path, monkeypatch):
    import shlex
    from types import SimpleNamespace
    from benchmark.harbor import process_supervisor
    from benchmark.harbor.forgecode_agent import ForgeCodeHarborAgent
    git = tmp_path / 'git'
    child_pid = tmp_path / 'git-child.pid'
    git.write_text('#!/bin/sh\nsleep 60 &\necho $! > "$CHILD_PID"\nwait\n', newline='\n')
    git.chmod(0o755)
    monkeypatch.setattr('benchmark.harbor.forgecode_agent._venv_python', lambda: sys.executable)
    monkeypatch.setattr('benchmark.harbor.forgecode_agent._PATCH_CAPTURE_SECONDS', 1)
    agent = ForgeCodeHarborAgent(logs_dir=tmp_path)
    agent._patch_baseline = 'a' * 40
    async def execute(**kwargs):
        argv = shlex.split(kwargs['command'])
        assert argv[2:4] == ['-m', 'benchmark.harbor.process_supervisor']
        # Run the same supervisor file without requiring an installed editable
        # package in the host's isolated interpreter (task containers install it).
        argv[2:4] = [str(Path(process_supervisor.__file__).resolve())]
        process = await asyncio.create_subprocess_exec(*argv,
            env={**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'], 'CHILD_PID': str(child_pid)},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        return SimpleNamespace(return_code=process.returncode, stdout=stdout.decode(), stderr=stderr.decode())
    asyncio.run(agent.capture_patch(SimpleNamespace(exec=execute)))
    diagnostic = json.loads((tmp_path / 'forgecode-patch-capture.json').read_text())
    assert diagnostic['status'] == 'timed_out' and diagnostic['return_code'] == 124
    assert diagnostic['phase'] == 'patch_capture'
    assert not (tmp_path / 'forgecode-pregrade.patch').exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(child_pid.read_text()), 0)


AGENT = '''
import asyncio, json, shlex, sys
from pathlib import Path
from forge.api import HarnessSession, RunProfile, SessionOptions
from forge.config import ForgeConfig
from forge.runtime.state import ToolCall
root, mode = Path(sys.argv[1]), sys.argv[2]
class NoModel:
    async def aclose(self): pass
async def main():
    session = await HarnessSession.open(SessionOptions(root=root/'project', data_root=root/'private',
        profile=RunProfile.evaluation(), service_handoff_dir=root/'receipts',
        config=ForgeConfig(api_key='offline', model_id='unused')),
        model_client_factory=lambda *a, **kw: NoModel())
    code = "import socket,time; from pathlib import Path; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); Path('port').write_text(str(s.getsockname()[1])); print('ready',flush=True); time.sleep(60)"
    outcome = await session.conversation.tool_executor.execute(ToolCall(0,'service','run_command', {
        'command': shlex.join([sys.executable,'-u','-c',code]), 'run_in_background':True,
        'lifetime':'session', 'timeout_seconds':None}))
    assert outcome.result.success
    while not (root/'project/port').exists(): await asyncio.sleep(.02)
    (root/'ready').touch()
    if mode == 'success':
        session.conversation.tool_executor.backend.observer.transfer_services()
        await session.close()
        (root/'cleanup.json').write_text(json.dumps(session.cleanup))
    else: await asyncio.Event().wait()
asyncio.run(main())
'''

PARENT = '''
import json, signal, subprocess, sys, time
from pathlib import Path
root, mode = Path(sys.argv[1]), sys.argv[2]
child = subprocess.Popen([sys.executable,'-I','-m','benchmark.harbor.process_supervisor',
    '--timeout-seconds','10' if mode == 'deadline' else '20',
    '--service-receipts',str(root/'receipts'),'--',sys.executable,str(root/'agent.py'),str(root),mode],
    stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
if mode == 'cancel':
    while not (root/'ready').exists() and child.poll() is None: time.sleep(.02)
    child.send_signal(signal.SIGTERM)
_, error = child.communicate(timeout=25)
print(json.dumps({'exit_code':child.returncode,'error':error.decode()}),flush=True)
time.sleep(60)
'''


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux evaluation handoff')
@pytest.mark.parametrize('mode', ['success', 'deadline', 'cancel'])
def test_live_service_handoff_and_user_cancellation(tmp_path, mode):
    (tmp_path/'project').mkdir()
    (tmp_path/'agent.py').write_text(AGENT)
    (tmp_path/'parent.py').write_text(PARENT)
    async def run():
        owner = await LocalProcessOwner.start([sys.executable, str(tmp_path/'parent.py'), str(tmp_path), mode],
            cwd=tmp_path, environment=dict(os.environ), output=True, capture_stderr=True)
        try:
            line = await asyncio.wait_for(owner.process.stdout.readline(), 30)
            assert line, await owner.process.stderr.read()
            outcome = json.loads(line)
            assert outcome['exit_code'] == {'success':0, 'deadline':124, 'cancel':143}[mode], outcome
            result = json.loads((tmp_path/'receipts/supervisor-result.json').read_text())
            port = int((tmp_path/'project/port').read_text())
            if mode == 'cancel':
                assert not result['handed_off'] and result['cancelled']
                with pytest.raises(OSError): socket.create_connection(('127.0.0.1',port),timeout=.2)
            else:
                assert len(result['handed_off']) == 1 and result['timed_out'] == (mode == 'deadline')
                with socket.create_connection(('127.0.0.1',port),timeout=1): pass
                if mode == 'success':
                    cleanup = json.loads((tmp_path/'cleanup.json').read_text())
                    assert len(cleanup['transferred_services']) == 1
        finally:
            cleanup = await owner.close()
        assert cleanup['state'] == 'clean'
        if mode != 'cancel':
            with pytest.raises(OSError): socket.create_connection(('127.0.0.1',port),timeout=.2)
    asyncio.run(run())
