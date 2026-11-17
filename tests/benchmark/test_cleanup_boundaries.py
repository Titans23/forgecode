import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from benchmark.adapters import docker
from benchmark.harbor.bounded_docker import BoundedDockerEnvironment
from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.task.config import TaskOS


def environment(tmp_path):
    env = object.__new__(docker.EvaluationDockerEnvironment)
    env._receipt_path = tmp_path / 'resources.jsonl'
    env._receipt_id = 'owned'
    env.logger = logging.getLogger(__name__)
    return env


def test_only_delivery_mounts_receive_recursive_ownership_repair(tmp_path):
    env = environment(tmp_path)
    env.task_env_config = SimpleNamespace(os=TaskOS.LINUX)
    env._mounts = [dict(target=p) for p in ('/logs/agent', '/logs/verifier', '/logs/artifacts',
                                         '/opt/forgecode-cache', '/caller-data')]
    seen = []
    async def chown(path, *, recursive):
        seen.append((path, recursive))
    env._chown_to_host_user = chown
    asyncio.run(env.prepare_logs_for_host())
    assert seen == [(p, True) for p in ('/logs/agent', '/logs/verifier', '/logs/artifacts')]
    record = json.loads(env._receipt_path.read_text())
    assert record['phase'] == 'log_ownership' and record['error'] is None


@pytest.mark.parametrize('after_state', ['clean', 'residual', 'wrong_engine', 'query_failure'])
def test_cleanup_timeout_rechecks_resources_without_inventing_success(tmp_path, monkeypatch, after_state):
    env = environment(tmp_path)
    empty = dict(daemon_id='engine', project='owned', containers=[], volumes=[], networks=[])
    env._resource_baseline = empty
    env._receipt('start_intent', project='owned')
    env._receipt('baseline', inventory=empty)
    calls = []
    async def inventory(project, known_volumes=()):
        calls.append((project, known_volumes))
        if len(calls) == 1:
            return {**empty, 'containers':['container'], 'volumes':['anonymous']}
        assert known_volumes == ['anonymous']
        if after_state == 'query_failure': raise docker.DockerQueryError('daemon unavailable')
        return {**empty, **({'containers':['container']} if after_state == 'residual' else
                           {'daemon_id':'other'} if after_state == 'wrong_engine' else {})}
    async def stop(self, delete): raise TimeoutError('compose timeout')
    monkeypatch.setattr(docker, 'docker_inventory', inventory)
    monkeypatch.setattr(BoundedDockerEnvironment, 'stop', stop)
    if after_state == 'clean': asyncio.run(env.stop(True))
    else:
        with pytest.raises(docker.DockerQueryError if after_state == 'query_failure' else TimeoutError):
            asyncio.run(env.stop(True))
    receipts = [json.loads(l) for l in env._receipt_path.read_text().splitlines()]
    assert next(r for r in receipts if r['phase']=='stop_finished')['error'][0]['type'] == 'TimeoutError'
    assert docker.cleanup_state(receipts) == {'clean':'clean','residual':'residual',
                                             'wrong_engine':'unknown','query_failure':'unknown'}[after_state]


def test_stop_has_one_outer_deadline_and_explicit_process_grace(monkeypatch):
    env = object.__new__(BoundedDockerEnvironment)
    seen = []
    async def compose(self, command, **kwargs): seen.append((command, kwargs['timeout_sec']))
    async def stop(self, delete): await self._run_docker_compose_command(['down', '--volumes'])
    monkeypatch.setattr(DockerEnvironment, '_run_docker_compose_command', compose)
    monkeypatch.setattr(DockerEnvironment, 'stop', stop)
    asyncio.run(env.stop(True))
    assert seen == [(['down','--volumes','--timeout','5'], None)]
    assert not env._stopping and env.cleanup_details['total_seconds'] >= env.cleanup_details['compose_seconds']
