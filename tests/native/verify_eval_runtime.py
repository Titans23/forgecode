"""Native evaluator acceptance with scripted models and actual tools/processes."""
import asyncio
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from evals.runner import PreparedFixture, run_case
from forge.permissions.policy import ApprovalResponse
from forge.runtime.state import ToolCall


fixtures = runpy.run_path(str(Path(__file__).parents[1] / 'evals/test_runner.py'))


@pytest.fixture(scope='module', autouse=True)
def private_native_parent(tmp_path_factory):
    if os.name == 'nt':
        from forge.sandbox.windows_worker import grant_controller_access
        base = tmp_path_factory.getbasetemp()
        if not any(base.iterdir()):
            grant_controller_access(base)


@pytest.fixture(autouse=True)
def private_native_fixture(tmp_path):
    if os.name == 'nt':
        from forge.sandbox.windows_worker import grant_controller_access
        grant_controller_access(tmp_path)


def test_native_evaluation_changes_fixture_and_independently_grades(tmp_path):
    case = fixtures['make_case'](fixtures['initialize_project'](tmp_path))
    client = fixtures['successful_client'](case)
    result = asyncio.run(run_case(case, project_root=tmp_path, output_dir=tmp_path / 'out', client=client))
    assert result.passed, result
    assert result.changed_paths == ('value.txt',)
    assert result.public_tests.execution_boundary == 'workspace-write-file-worker'
    assert result.hidden_tests.success and result.cleanup['state'] == 'clean'
    assert client.closed


def test_native_cancel_terminates_actual_command_and_descendant(tmp_path, monkeypatch):
    import evals.runner as runner
    case = fixtures['make_case'](fixtures['initialize_project'](tmp_path))
    observed = []
    class ObservedFixture(PreparedFixture):
        def _prepare(self):
            result = super()._prepare()
            observed.append(self.workspace)
            return result
    monkeypatch.setattr(runner, 'PreparedFixture', ObservedFixture)
    source = ("import os,sys,time,json,subprocess; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "Path('ready.json').write_text(json.dumps([os.getpid(),child.pid])); time.sleep(60)")
    import shlex
    command = (subprocess.list2cmdline if os.name == 'nt' else shlex.join)([sys.executable, '-c', source])
    client = fixtures['FakeModelClient'](fixtures['tool_response'](
        ToolCall(0, 'owned-process', 'run_command', {'command': command, 'timeout_seconds': 90})))
    async def approve(request):
        return ApprovalResponse('allow_once', 'Explicit offline native cancellation fixture')
    async def run():
        task = asyncio.create_task(run_case(case, project_root=tmp_path, output_dir=tmp_path / 'out',
            client=client, approval_handler=approve))
        handles = []
        try:
            async with asyncio.timeout(60):
                while not observed or not (observed[0] / 'ready.json').exists():
                    assert not task.done(), task.result() if task.done() else None
                    await asyncio.sleep(.05)
            pids = json.loads((observed[0] / 'ready.json').read_text())
            if os.name == 'nt':
                import win32api, win32con, win32event
                handles = [win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid) for pid in pids]
                assert all(win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_TIMEOUT for handle in handles)
            else:
                handles = [os.pidfd_open(pid) for pid in pids]
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            if os.name == 'nt':
                assert all(win32event.WaitForSingleObject(handle, 5000) == win32event.WAIT_OBJECT_0 for handle in handles)
            else:
                import select
                assert all(select.select([handle], [], [], 5)[0] for handle in handles)
            assert client.closed
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            for handle in handles:
                handle.Close() if os.name == 'nt' else os.close(handle)
    asyncio.run(run())
    report = json.loads((tmp_path / 'out/demo-001-latest.json').read_text())
    assert report['task_status'] == 'cancelled' and not report['passed']
    assert report['cleanup']['state'] == 'clean'
    assert not observed[0].exists()
