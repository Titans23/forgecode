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


@pytest.mark.parametrize('after_state', ['clean', 'recovered', 'residual', 'wrong_engine', 'query_failure'])
def test_cleanup_timeout_rechecks_resources_without_inventing_success(tmp_path, monkeypatch, after_state):
    env = environment(tmp_path)
    empty = dict(daemon_id='engine', project='owned', containers=[], volumes=[], networks=[])
    env._resource_baseline = empty
    env._receipt('start_intent', project='owned')
    env._receipt('baseline', inventory=empty)
    calls = []
    mutations = []
    residual = {**empty, 'containers': ['container'], 'volumes': ['anonymous'], 'networks': ['network']}

    async def inventory(project, known_volumes=()):
        calls.append((project, known_volumes))
        if len(calls) == 1:
            return residual
        assert known_volumes == ['anonymous']
        if after_state == 'query_failure': raise docker.DockerQueryError('daemon unavailable')
        if after_state == 'wrong_engine':
            return {**residual, 'daemon_id': 'other'}
        if after_state == 'residual' or (after_state == 'recovered' and not mutations):
            return residual
        return empty

    async def stop(self, delete): raise TimeoutError('compose timeout')

    async def remove(*args):
        mutations.append(args)
        if after_state == 'residual':
            raise docker.DockerQueryError('fixture still busy')

    monkeypatch.setattr(docker, 'docker_inventory', inventory)
    monkeypatch.setattr(docker, 'docker_query', remove)
    monkeypatch.setattr(BoundedDockerEnvironment, 'stop', stop)
    if after_state in ('clean', 'recovered'): asyncio.run(env.stop(True))
    else:
        with pytest.raises(docker.DockerQueryError if after_state == 'query_failure' else TimeoutError):
            asyncio.run(env.stop(True))
    receipts = [json.loads(l) for l in env._receipt_path.read_text().splitlines()]
    assert next(r for r in receipts if r['phase']=='stop_finished')['error'][0]['type'] == 'TimeoutError'
    assert mutations == ([('rm', '--force', 'container'), ('volume', 'rm', 'anonymous'),
                          ('network', 'rm', 'network')] if after_state in ('recovered', 'residual') else [])
    assert docker.cleanup_state(receipts) == {'clean':'clean','recovered':'clean','residual':'residual',
                                             'wrong_engine':'unknown','query_failure':'unknown'}[after_state]


def test_inventory_does_not_claim_external_named_volume(monkeypatch):
    async def query(*args):
        if args[0] == 'info': return 'engine'
        if args[0] == 'ps': return 'container'
        if args[0] == 'network': return ''
        if args[0] == 'inspect':
            return json.dumps([{'Type': 'volume', 'Name': name}
                               for name in ('external', 'attached-unnamed')])
        if args[-1] == 'label=com.docker.volume.anonymous':
            return 'attached-unnamed\nknown-unnamed\nunrelated-unnamed'
        return 'project-volume'

    monkeypatch.setattr(docker, 'docker_query', query)
    inventory = asyncio.run(docker.docker_inventory('owned', ['known-unnamed', 'external']))
    assert inventory['volumes'] == ['attached-unnamed', 'known-unnamed', 'project-volume']


def test_keep_containers_does_not_reconcile(tmp_path, monkeypatch):
    env = environment(tmp_path)
    env._keep_containers = True
    env._resource_baseline = dict(daemon_id='engine', project='owned', containers=[], volumes=[], networks=[])
    calls = []

    async def inventory(project):
        calls.append(project)
        return {**env._resource_baseline, 'containers': ['retained']}

    async def stop(self, delete):
        assert delete is True

    monkeypatch.setattr(docker, 'docker_inventory', inventory)
    monkeypatch.setattr(BoundedDockerEnvironment, 'stop', stop)
    asyncio.run(env.stop(True))
    assert calls == ['owned']
    assert json.loads(env._receipt_path.read_text())['delete_requested'] is False


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
