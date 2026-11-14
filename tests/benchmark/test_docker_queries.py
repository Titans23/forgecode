import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from benchmark.adapters import docker
from harbor.trial.trial import Trial
from harbor.trial.errors import EnvironmentStartTimeoutError


def launch_python(monkeypatch, code):
    original = asyncio.create_subprocess_exec
    children = []

    async def create(*args, **kwargs):
        child = await original(sys.executable, '-u', '-c', code, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(docker.asyncio, 'create_subprocess_exec', create)
    return children


def test_query_drains_fragmented_stdout_and_stderr(monkeypatch):
    launch_python(monkeypatch,
        "import sys,time; sys.stdout.write('{'); sys.stdout.flush(); "
        "sys.stderr.write('diagnostic'*5000); sys.stderr.flush(); time.sleep(.05); "
        "sys.stdout.write('\"ok\":true}')")
    assert json.loads(asyncio.run(docker.docker_query('info'))) == {'ok': True}


def test_query_failure_retains_redacted_docker_reason(monkeypatch):
    launch_python(monkeypatch,
        "import sys; sys.stderr.write('all predefined address pools have been fully subnetted; token=private-value'); sys.exit(1)")
    with pytest.raises(docker.DockerQueryError, match='fully subnetted') as failure:
        asyncio.run(docker.docker_query('network', 'create'))
    assert 'private-value' not in str(failure.value)


@pytest.mark.parametrize('reason', ['timeout', 'cancel', 'output_limit'])
def test_query_failure_reaps_owned_child(monkeypatch, reason):
    children = launch_python(monkeypatch,
        "import sys,time; " + ("sys.stdout.write('x'*1100000); sys.stdout.flush(); "
                               if reason == 'output_limit' else '') + "time.sleep(30)")
    if reason == 'timeout':
        monkeypatch.setattr(docker, 'DOCKER_QUERY_TIMEOUT_SECONDS', .2)

    async def run():
        task = asyncio.create_task(docker.docker_query('network', 'ls'))
        if reason == 'cancel':
            while not children:
                await asyncio.sleep(.01)
            task.cancel()
        expected = asyncio.CancelledError if reason == 'cancel' else docker.DockerQueryError
        with pytest.raises(expected):
            await asyncio.wait_for(task, 5)
        assert children[0].returncode is not None

    asyncio.run(run())


def test_harbor_preserves_query_timeout_and_its_actual_phase_deadline(monkeypatch):
    children = launch_python(monkeypatch, 'import time; time.sleep(30)')
    monkeypatch.setattr(docker, 'DOCKER_QUERY_TIMEOUT_SECONDS', .2)

    async def query(**kwargs):
        await docker.docker_query('network', 'ls')

    trial = SimpleNamespace(agent_environment=SimpleNamespace(start=query),
                            config=SimpleNamespace(environment=SimpleNamespace(force_build=False)),
                            _environment_build_timeout_sec=600)
    async def run():
        with pytest.raises(docker.DockerQueryError, match='0.2 seconds: network ls'):
            await Trial._start_agent_environment(trial)
        trial._environment_build_timeout_sec = .01
        with pytest.raises(EnvironmentStartTimeoutError, match='0.01 seconds'):
            await Trial._start_agent_environment(trial)
        assert all(child.returncode is not None for child in children)
    asyncio.run(run())
