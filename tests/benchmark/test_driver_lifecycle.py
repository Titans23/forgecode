import asyncio
import json
import sys

import pytest

from benchmark.harbor.bounded_docker import BoundedDockerEnvironment
from benchmark.harbor.summarize import summarize_run
from harbor.environments.docker.docker import DockerEnvironment


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
