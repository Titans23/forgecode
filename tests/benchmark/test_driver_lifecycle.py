import asyncio
import json
import sys
import os
import shutil
from pathlib import Path

import pytest

from benchmark.harbor.bounded_docker import BoundedDockerEnvironment
from benchmark.harbor.summarize import summarize_run
from harbor.environments.docker.docker import DockerEnvironment


def _bash():
    return (Path(shutil.which('git')).resolve().parents[1] / 'bin/bash.exe'
            if sys.platform == 'win32' else shutil.which('bash'))


@pytest.mark.parametrize('present', [(), ('curl',), ('git',), ('curl', 'git')])
def test_minimal_task_tools_are_installed_only_when_missing(tmp_path, present):
    import subprocess
    from benchmark.harbor.forgecode_agent import _prerequisite_install_command
    commands = tmp_path / 'bin'
    commands.mkdir()
    log = tmp_path / 'apt-calls'
    for name in present:
        tool = commands / name
        tool.write_text('#!/bin/sh\nexit 0\n', newline='\n')
        tool.chmod(0o755)
    apt = commands / 'apt-get'
    apt.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n', newline='\n')
    apt.chmod(0o755)
    command = 'cd "$FIXTURE_PATH"; export PATH="$PWD"; ' + _prerequisite_install_command()
    subprocess.run([str(_bash()), '--noprofile', '--norc', '-e', '-c', command],
        env={**os.environ, 'FIXTURE_PATH': commands.as_posix(), 'CALL_LOG': log.as_posix()}, check=True)
    assert (log.read_text().splitlines() if log.exists() else []) == ([] if len(present) == 2 else [
        'update', 'install -y --no-install-recommends ca-certificates curl git'])


def test_frozen_agent_links_harbor_identity_to_valid_journal_scope(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from uuid import uuid4
    from benchmark.harbor.forgecode_agent import ForgeCodeHarborAgent
    from harbor.models.agent.context import AgentContext
    from forge.observability.events import Scope
    from forge.observability.recorder import JournalRecorder
    from forge.runtime.turn_state import TurnState
    from forge.sessions.store import SessionStore
    agent = ForgeCodeHarborAgent(logs_dir=tmp_path,
        frozen_configuration=json.dumps({'run_id': 'run-' + str(uuid4())}))
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    subprocess.run(['git', 'init', str(workspace)], check=True, capture_output=True)
    async def upload(*args, **kwargs): pass
    async def create_deliverable(*args, **kwargs):
        (workspace / 'new.py').write_text('print("fixture")\n')
        # Model-side commits must not disappear from the final patch.
        subprocess.run(['git', 'add', '.'], cwd=workspace, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'synthetic model change'], cwd=workspace, check=True, capture_output=True)
    async def exec_fixture(*args, **kwargs):
        result = subprocess.run([str(_bash()), '--noprofile', '--norc', '-c', kwargs['command']],
            cwd=workspace, capture_output=True, text=True)
        return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    monkeypatch.setattr(agent, 'exec_as_agent', create_deliverable)
    asyncio.run(agent.run('synthetic task', SimpleNamespace(upload_file=upload, exec=exec_fixture), AgentContext()))
    frozen = json.loads((tmp_path / 'forgecode-harness.json').read_text())
    scope = frozen['scope']
    assert frozen['harbor_trial_id'] == str(agent.context_id or tmp_path.parent.name)
    journal = SessionStore(tmp_path, data_root=tmp_path / 'state').create(model='fixture')
    recorder = JournalRecorder(journal, scope=Scope(trace_id=scope['trace_id'], span_id=scope['span_id'],
        identities={key: scope[key] for key in ('run_id', 'trial_id')} | {'attempt_id': frozen['attempt_id']}))
    recorder.reserve_budget(TurnState(max_seconds=30))
    assert journal.path.read_text().count('budget.reserved') == 1
    assert '+print("fixture")' in (tmp_path / 'forgecode-pregrade.patch').read_text()
    assert (workspace / 'new.py').read_text() == 'print("fixture")\n'

    # Harbor represents successful commands with no output as stdout=None.
    # An unchanged workspace still needs an empty patch and official grading.
    async def empty_diff(*args, **kwargs):
        if 'rev-parse HEAD' in kwargs['command']:
            return await exec_fixture(*args, **kwargs)
        return SimpleNamespace(return_code=0, stdout=None)
    monkeypatch.setattr(agent, 'exec_as_agent', upload)
    asyncio.run(agent.run('unchanged task', SimpleNamespace(upload_file=upload, exec=empty_diff), AgentContext()))
    assert (tmp_path / 'forgecode-pregrade.patch').read_bytes() == b''
    async def failed_diff(*args, **kwargs):
        if 'rev-parse HEAD' in kwargs['command']:
            return await exec_fixture(*args, **kwargs)
        return SimpleNamespace(return_code=128, stdout=None, stderr='synthetic missing Git object')
    with pytest.raises(RuntimeError, match='synthetic missing Git object'):
        asyncio.run(agent.run('failed capture', SimpleNamespace(upload_file=upload, exec=failed_diff), AgentContext()))
    assert json.loads((tmp_path / 'forgecode-patch-capture.json').read_text())['return_code'] == 128


def test_prepared_agent_checks_installation_without_download_or_upload(tmp_path, monkeypatch):
    from benchmark.harbor.forgecode_agent import ForgeCodeHarborAgent
    agent = ForgeCodeHarborAgent(logs_dir=tmp_path, frozen_configuration=json.dumps({
        'prepared_images': True, 'source_manifest': {'content_sha256': 'a' * 64}}))
    commands = []
    async def execute(environment, *, command): commands.append(command)
    monkeypatch.setattr(agent, 'exec_as_agent', execute)
    asyncio.run(agent.install(object()))
    assert len(commands) == 1 and 'a' * 64 in commands[0]
    assert 'import forge.launch' in commands[0]
    assert all(word not in commands[0] for word in ('apt-get', 'https://', ' sync ', ' install '))


def test_uv_download_timeout_is_not_a_task_failure(tmp_path):
    trial = tmp_path / 'trial'
    (trial / 'verifier').mkdir(parents=True)
    (trial / 'result.json').write_text(json.dumps({
        'exception_info': None, 'verifier_result': {'rewards': {'reward': 0}},
    }))
    (trial / 'verifier/test-stdout.txt').write_text(
        'Failed to download `example==1.0`\n'
        'Failed to download distribution due to network timeout.\n'
    )
    summary = summarize_run(tmp_path)
    assert summary.verifier_environment_failures == 1
    assert summary.agent_failures == summary.scored_trials == 0
    assert summary.raw_rewards == {'trial': 0.0}


def test_transport_budget_does_not_shorten_agent_execution(monkeypatch):
    observed = []

    async def fake(self, command, **kwargs):
        observed.append(kwargs['timeout_sec'])

    monkeypatch.setattr(DockerEnvironment, '_run_docker_compose_command', fake)
    env = object.__new__(BoundedDockerEnvironment)

    async def run():
        for command, timeout in [('cp', None), ('down', None), ('exec', 1800), ('cp', 2)]:
            await env._run_docker_compose_command([command], timeout_sec=timeout)

    asyncio.run(run())
    assert observed == [120, 60, 1800, 2]


def test_cancelled_transport_reaps_real_process():
    async def run():
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-c', 'import time; time.sleep(30)',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        task = asyncio.create_task(BoundedDockerEnvironment._collect_buffered_output(
            process, timeout_sec=None,
        ))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await asyncio.wait_for(task, 8)
        except asyncio.CancelledError:
            pass
        assert process.returncode is not None

    asyncio.run(run())


@pytest.mark.parametrize('method', ['service_is_dir', 'service_download_file',
                                  'service_download_dir', 'service_download_dir_with_exclusions', 'stop'])
def test_stalled_collection_and_cleanup_are_bounded(monkeypatch, method):
    cancelled = []

    async def stalled(self, *args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(DockerEnvironment, method, stalled)
    env = object.__new__(BoundedDockerEnvironment)
    env.transport_timeout_seconds = env.cleanup_timeout_seconds = 0.02

    async def run():
        with pytest.raises(TimeoutError):
            if method == 'stop':
                await env.stop(False)
            else:
                await getattr(env, method)()

    asyncio.run(run())
    assert cancelled == [True]
