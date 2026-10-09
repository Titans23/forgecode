"""Policy/lifecycle failure injection only; real OS proof is workspace_sandbox_probe.py."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from forge.application.models import ContractError
from forge.engine.persistence import new_id
from forge.sandbox.application_backend import NativeBackendFactory
from forge.sandbox.workspace_backend import WorkspaceWriteBackend, require_workspace_policy

ROOT=Path(__file__).resolve().parents[3]


def fixture(tmp_path):
    project=tmp_path/'project';project.mkdir()
    stat=project.stat()
    workspace={'id':new_id('ws'),'canonical_path':str(project),'file_identity':f'{stat.st_dev}:{stat.st_ino}'}
    policy=json.loads((ROOT/'contracts/v1/examples/sandbox-policy.valid.json').read_bytes())
    policy['workspace_id']=workspace['id']
    policy['filesystem'].update(read_mode='host_default',read_roots=[str(project)],write_roots=[str(project)],protected_paths=[])
    policy['network'].update(mode='inherit',allowed_domains=[],dns_isolation_required=False)
    owner={'engine_epoch':new_id('epoch'),'sandbox_session_id':new_id('sandbox'),'execution_id':None}
    return policy,WorkspaceWriteBackend(workspace,owner,tmp_path/'control')


@pytest.mark.parametrize('change',[lambda p:p['filesystem'].update(read_mode='strict_allowlist_required'),
    lambda p:p['network'].update(mode='deny_direct'),lambda p:p['network'].update(allowed_domains=['example.com']),
    lambda p:p['network'].update(dns_isolation_required=True),
    lambda p:p['limits'].update(pids={'value':4,'enforcement':'hard_required'})])
def test_light_mode_refuses_stronger_policy_instead_of_downgrading(tmp_path,change):
    policy,_=fixture(tmp_path);change(policy)
    with pytest.raises(ContractError):require_workspace_policy(policy)


def test_strict_default_and_lightweight_policies_cannot_be_confused(tmp_path):
    policy,_=fixture(tmp_path)
    strict=NativeBackendFactory(tmp_path/'data')
    assert strict.mode=='strict'
    with pytest.raises(ContractError):strict.check_request(policy,required_mode='strict')
    light=NativeBackendFactory(tmp_path/'data',mode='workspace-write')
    light.check_request(policy,required_mode='workspace-write')
    with pytest.raises(ContractError):light.check_request(policy,required_mode='local-trusted')


def test_initialization_failure_cannot_admit_execution_or_fallback(tmp_path,monkeypatch):
    import forge.sandbox.workspace_backend as module
    policy,backend=fixture(tmp_path)
    def unavailable(*args):raise ContractError('owned fixture missing runtime',kind='SANDBOX_UNAVAILABLE')
    monkeypatch.setattr(module,'verify_runtime',unavailable)
    monkeypatch.setattr(module,'windows_supported',lambda: True)  # Test missing runtime separately from OS admission.
    async def run():
        with pytest.raises(ContractError,match='missing runtime'):await backend.prepare(policy)
        assert backend._prepared is None and not backend._executions
        assert (await backend.close())['state']=='clean'
        with pytest.raises(ContractError):await backend.prepare(policy)
    asyncio.run(run())


def test_close_keeps_unknown_cleanup_and_is_idempotent(tmp_path):
    _,backend=fixture(tmp_path)
    async def run():
        task=asyncio.create_task(asyncio.sleep(0));await task
        backend._executions['owned-fixture']={'task':task,'cleanup':{'state':'unknown'}}
        first,second=await asyncio.gather(backend.close(),backend.close())
        assert first==second and first['state']=='unknown'
    asyncio.run(run())


def test_official_executor_refuses_both_non_strict_modes_before_model_access():
    from benchmark.adapters.harbor import HarborExecutor
    for mode in ('workspace-write','local-trusted'):
        executor=object.__new__(HarborExecutor);executor.service=SimpleNamespace(mode=mode)
        issues=executor.validate({}, {})
        assert issues[0]['kind']=='protocol_incompatible'


def test_unsupported_windows_host_refuses_before_runtime_or_processes(tmp_path, monkeypatch):
    import forge.sandbox.workspace_backend as module
    policy,backend=fixture(tmp_path)
    monkeypatch.setattr(module,'sys',SimpleNamespace(platform='win32'))
    monkeypatch.setattr(module,'windows_supported',lambda: False)
    async def run():
        with pytest.raises(ContractError) as caught:
            await backend.prepare(policy)
        assert caught.value.kind=='SANDBOX_UNAVAILABLE'
        assert backend._prepared is None and not backend._executions and not backend.control_root.exists()
        assert (await backend.close())['state']=='clean'
    asyncio.run(run())
