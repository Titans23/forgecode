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


def test_locked_installer_resolves_default_coding_dependencies():
    import shlex
    import subprocess
    from benchmark.harbor.forgecode_agent import _locked_install_command

    # Resolve the installer's actual extras against the lock: a test environment
    # with all extras installed otherwise hides missing default-tool dependencies.
    arguments = shlex.split(_locked_install_command('/fixture'))
    extras = [arguments[index + 1] for index, value in enumerate(arguments) if value == '--extra']
    exported = subprocess.check_output([
        shutil.which('uv'), 'export', '--frozen', '--no-dev', '--no-emit-project', '--no-hashes',
        *[value for extra in extras for value in ('--extra', extra)],
    ], cwd=Path(__file__).resolve().parents[2], text=True, encoding='utf-8')
    assert any(line.lower().startswith('pyyaml==') for line in exported.splitlines())


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
    common = ('-o Acquire::Retries=0 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 '
              '-o Acquire::http::No-Cache=true -o Acquire::https::No-Cache=true -o Acquire::PDiffs=false ')
    assert (log.read_text().splitlines() if log.exists() else []) == ([] if len(present) == 2 else [
        common + '-o APT::Update::Error-Mode=any update',
        common + 'install -y --no-install-recommends ca-certificates curl git'])


@pytest.mark.parametrize('error, succeeds, expected_calls', [
    ('Hash Sum mismatch',True,4), ('503 Service Unavailable',False,3), ('Permission denied',False,1)])
def test_prerequisite_retry_is_bounded_and_only_for_transient_failures(tmp_path, error, succeeds, expected_calls):
    import subprocess
    from benchmark.harbor.forgecode_agent import _prerequisite_install_command
    commands = tmp_path/'bin'
    commands.mkdir()
    apt = commands/'apt-get'
    apt.write_text('#!/bin/sh\nn=0; if [ -f "$CALL_LOG" ]; then read -r n < "$CALL_LOG"; fi\n'
        'n=$((n+1)); printf "%s\\n" "$n" > "$CALL_LOG"\n'
        'if [ "$RECOVER" = yes ] && [ "$n" -ge 3 ]; then exit 0; fi\n'
        'printf "%s\\n" "$FAILURE"; exit 100\n', newline='\n')
    apt.chmod(0o755)
    sleep = commands/'sleep'
    sleep.write_text('#!/bin/sh\nexit 0\n', newline='\n')
    sleep.chmod(0o755)
    log = tmp_path/'calls'
    command = 'cd "$FIXTURE_PATH"; export PATH="$PWD"; ' + _prerequisite_install_command()
    result = subprocess.run([str(_bash()), '--noprofile','--norc','-e','-c',command], capture_output=True,
        env={**os.environ, 'FIXTURE_PATH':commands.as_posix(), 'CALL_LOG':log.as_posix(),
             'FAILURE':error, 'RECOVER':'yes' if succeeds else 'no'})
    assert result.returncode == (0 if succeeds else 100), result.stderr
    assert int(log.read_text()) == expected_calls


def test_bullseye_security_archive_preserves_transport_and_other_sources(tmp_path):
    import subprocess
    from benchmark.harbor.prepare import bullseye_security_archive

    (tmp_path / 'release').write_text('ID=debian\nVERSION_CODENAME=bullseye\n', newline='\n')
    sources = tmp_path / 'debian.list'
    sources.write_text('deb http://deb.debian.org/debian bullseye main\n'
                       'deb https://security.debian.org/debian-security bullseye-security main\n'
                       'deb http://deb.debian.org/debian-security bullseye-security main\n', newline='\n')
    subprocess.run([str(_bash()), '-ec', bullseye_security_archive('./release', '.')], cwd=tmp_path, check=True)
    assert sources.read_text().splitlines() == ['deb http://deb.debian.org/debian bullseye main',
        'deb https://archive.debian.org/debian-security bullseye-security main',
        'deb http://archive.debian.org/debian-security bullseye-security main']


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
    agent.set_phase_timeout(900)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    subprocess.run(['git', 'init', str(workspace)], check=True, capture_output=True)
    async def upload(*args, **kwargs): pass
    async def create_deliverable(*args, **kwargs):
        (workspace / 'new.py').write_text('print("fixture")\n')
        # Model-side commits must not disappear from the final patch.
        subprocess.run(['git', 'add', '.'], cwd=workspace, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'synthetic model change'], cwd=workspace, check=True, capture_output=True)
        nested = workspace / 'cloned'
        subprocess.run(['git', 'init', str(nested)], check=True, capture_output=True)
        subprocess.run(['git', '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.test',
                        'commit', '--allow-empty', '-m', 'fixture'], cwd=nested, check=True, capture_output=True)
        (workspace / '.git/index.lock').write_text('checkpoint interrupted')
    async def exec_fixture(*args, **kwargs):
        import shlex
        command = kwargs['command']
        if 'benchmark.harbor.process_supervisor' in command:
            command = shlex.split(command)[-1]  # Git script runs on Windows too; Linux supervision is covered below.
        result = subprocess.run([str(_bash()), '--noprofile', '--norc', '-c', command],
            cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    async def run_and_capture(instruction, execute):
        environment = SimpleNamespace(upload_file=upload, exec=execute)
        try:
            await agent.run(instruction, environment, AgentContext())
        finally:
            await agent.capture_patch(environment)
    monkeypatch.setattr(agent, 'exec_as_agent', create_deliverable)
    asyncio.run(run_and_capture('synthetic task', exec_fixture))
    frozen = json.loads((tmp_path / 'forgecode-harness.json').read_text())
    scope = frozen['scope']
    assert frozen['harbor_trial_id'] == str(agent.context_id or tmp_path.parent.name)
    journal = SessionStore(tmp_path, data_root=tmp_path / 'state').create(model='fixture')
    recorder = JournalRecorder(journal, scope=Scope(trace_id=scope['trace_id'], span_id=scope['span_id'],
        identities={key: scope[key] for key in ('run_id', 'trial_id')} | {'attempt_id': frozen['attempt_id']}))
    recorder.finish_turn_usage(TurnState())
    assert journal.path.read_text().count('budget.consumed') == 1
    assert 'budget.reserved' not in journal.path.read_text()
    assert '+print("fixture")' in (tmp_path / 'forgecode-pregrade.patch').read_text()
    assert 'warning:' not in (tmp_path / 'forgecode-pregrade.patch').read_text()
    assert (workspace / 'new.py').read_text() == 'print("fixture")\n'
    assert (workspace / '.git/index.lock').read_text() == 'checkpoint interrupted'

    # Large deliverables remain in the verifier workspace even when patch archival is skipped.
    async def create_large_deliverable(*args, **kwargs):
        (workspace / 'weights.bin').write_bytes(bytes(range(256)) * 16)
    monkeypatch.setattr('benchmark.harbor.forgecode_agent._MAX_PATCH_BYTES', 32)
    monkeypatch.setattr(agent, 'exec_as_agent', create_large_deliverable)
    asyncio.run(run_and_capture('large output', exec_fixture))
    capture = json.loads((tmp_path / 'forgecode-patch-capture.json').read_text())
    assert capture['status'] == 'omitted_size_limit' and capture['patch_bytes'] > 32
    assert capture['patch_bytes_exact'] is False
    assert not (tmp_path / 'forgecode-pregrade.patch').exists()
    assert (workspace / 'weights.bin').read_bytes() == bytes(range(256)) * 16

    # Harbor represents successful commands with no output as stdout=None.
    # An unchanged workspace still needs an empty patch and official grading.
    async def empty_diff(*args, **kwargs):
        if 'rev-parse HEAD' in kwargs['command']:
            return await exec_fixture(*args, **kwargs)
        return SimpleNamespace(return_code=0, stdout=None)
    monkeypatch.setattr(agent, 'exec_as_agent', upload)
    asyncio.run(run_and_capture('unchanged task', empty_diff))
    assert (tmp_path / 'forgecode-pregrade.patch').read_bytes() == b''
    async def failed_diff(*args, **kwargs):
        if 'rev-parse HEAD' in kwargs['command']:
            return await exec_fixture(*args, **kwargs)
        return SimpleNamespace(return_code=128, stdout='synthetic missing Git object', stderr='')
    asyncio.run(run_and_capture('failed capture', failed_diff))
    assert json.loads((tmp_path / 'forgecode-patch-capture.json').read_text())['return_code'] == 128
    assert not (tmp_path / 'forgecode-pregrade.patch').exists()
    async def agent_failed(*args, **kwargs):
        raise RuntimeError('Internal model failure returned exit 1')
    monkeypatch.setattr(agent, 'exec_as_agent', agent_failed)
    with pytest.raises(RuntimeError, match='Internal model failure'):
        asyncio.run(run_and_capture('agent failed', empty_diff))
    assert (tmp_path / 'forgecode-pregrade.patch').read_bytes() == b''
    with pytest.raises(RuntimeError, match='Internal model failure') as caught:
        asyncio.run(run_and_capture('two failures', failed_diff))
    assert caught.value.__cause__ is None
    assert 'synthetic missing Git object' in (tmp_path / 'forgecode-patch-capture.json').read_text()


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


def test_patch_limit_stops_producer_without_draining_remaining_output(tmp_path, monkeypatch):
    import subprocess
    from benchmark.harbor.forgecode_agent import _patch_capture_command
    git = tmp_path / 'git'
    git.write_text('#!/bin/sh\ncase "$*" in *" diff "*) exec yes patch-data;; esac\n', newline='\n')
    git.chmod(0o755)
    monkeypatch.setattr('benchmark.harbor.forgecode_agent._MAX_PATCH_BYTES', 32)
    result = subprocess.run([str(_bash()), '-c', 'cd "$FIXTURE_PATH"; export PATH="$PWD:$PATH"; ' + _patch_capture_command('a' * 40)],
        env={**os.environ, 'FIXTURE_PATH': tmp_path.as_posix()}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'FORGECODE_PATCH_BYTES 33'


@pytest.mark.parametrize('distribution,release,archived', [
    ('debian', 'bullseye', True), ('debian', 'bookworm', False), ('ubuntu', 'noble', False)])
def test_preparation_stops_at_failed_authenticated_package_index(tmp_path, distribution, release, archived):
    import subprocess
    from benchmark.harbor.prepare import _prerequisites
    (tmp_path / 'os-release').write_text(f'ID={distribution}\nVERSION_CODENAME={release}\n', newline='\n')
    for name, body in {'find': 'printf "%s\\n" "$*"', 'apt-get': 'printf "%s\\n" "$*"; exit 42'}.items():
        script = tmp_path / name
        script.write_text('#!/bin/sh\n' + body + '\n', newline='\n')
        script.chmod(0o755)
    result = subprocess.run([str(_bash()), '--noprofile', '--norc', '-c',
        'cd "$FIXTURE_PATH"; export PATH="$PWD"; ' + _prerequisites('./os-release')],
        env={**os.environ, 'FIXTURE_PATH': tmp_path.as_posix()}, capture_output=True, text=True)
    assert result.returncode == 42
    assert 'CaInfo=/opt/forgecode-preparation-ca.pem' in result.stdout
    assert 'Error-Mode=any update' in result.stdout and ' install ' not in result.stdout
    assert ('archive.debian.org' in result.stdout) is archived


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


def test_verifier_cache_is_scoped_and_keeps_official_script(tmp_path, monkeypatch):
    from benchmark.harbor.verifier_cache import verifier_specs, INSTALLER_URL
    from benchmark.adapters.docker import EvaluationDockerEnvironment
    task = tmp_path / 'task'
    (task / 'tests').mkdir(parents=True)
    script = task / 'tests/test.sh'
    original = f'curl -LsSf {INSTALLER_URL} | sh\nuvx -p 3.13 -w pytest==8.4.1 pytest /tests/test_outputs.py\n'
    script.write_text(original)
    monkeypatch.setattr('benchmark.adapters.materialize.resolve_task', lambda *args: task)
    assert verifier_specs(tmp_path, {'tasks': ['fixture']}) == [['-p', '3.13', '-w', 'pytest==8.4.1']]
    assert script.read_text() == original
    script.write_text(original.replace('3.13', '${UNKNOWN}'))
    with pytest.raises(ValueError, match='Unresolved'):
        verifier_specs(tmp_path, {'tasks': ['fixture']})
    calls = []
    async def execute(self, **kwargs): calls.append(kwargs)
    monkeypatch.setattr(BoundedDockerEnvironment, 'exec', execute)
    env = object.__new__(EvaluationDockerEnvironment)
    asyncio.run(env.exec(command='official-test', env={'FORGECODE_VERIFIER_CACHE': '1'}))
    asyncio.run(env.exec('agent-command', '/app', {'PATH': '/original'}, 7, 'root'))
    assert calls[0]['env'] == {'UV_DOWNLOAD_URL': 'file:///opt/forgecode-verifier', 'UV_OFFLINE': '1'}
    assert calls[0]['command'].endswith('official-test')
    assert calls[1] == {'command': 'agent-command', 'cwd': '/app', 'env': {'PATH': '/original'},
                        'timeout_sec': 7, 'user': 'root'}


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
