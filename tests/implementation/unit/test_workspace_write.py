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
    strict=NativeBackendFactory(tmp_path/'data',mode='strict')
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
    async def run():
        with pytest.raises(ContractError,match='missing runtime'):await backend.prepare(policy)
        assert backend._prepared is None and not backend._executions
        assert (await backend.close())['state']=='clean'
        with pytest.raises(ContractError):await backend.prepare(policy)
    asyncio.run(run())


@pytest.mark.parametrize('mode,stderr,execution_error,proof,hint', [
    ('workspace-write', 'ForgeCode workspace sandbox unavailable: SetNamedSecurityInfoW failed '
     '(Win32 5): grantWrite(D:\\project)\n', None, b'', True),
    ('read-only', 'native executable unavailable\x1b\n', None, b'', False),
    ('workspace-write', '', 'TimeoutError', b'', False),
    ('workspace-write', '', None, b'{"outsideDenied": false}', False),
])
def test_probe_failure_exposes_cause_without_admitting_or_falling_back(
        tmp_path, monkeypatch, capsys, mode, stderr, execution_error, proof, hint):
    """Injected failures test diagnostics/lifecycle, not native capability."""
    import forge.sandbox.workspace_backend as module
    import forge.sandbox.windows_worker as worker
    from forge.cli import print_configuration_error
    policy, backend = fixture(tmp_path)
    backend.mode = mode
    if mode == 'read-only':
        policy['filesystem']['write_roots'] = []
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(module, 'verify_runtime', lambda root: SimpleNamespace(
        root=tmp_path / 'installation', node=tmp_path / 'node', entry=tmp_path / 'runner', tools={}))
    monkeypatch.setattr(worker, 'grant_controller_access', lambda directory: None)
    from forge.sandbox.dsh_session import DshSession
    async def start(self): pass
    async def close(self): return {'state': 'clean'}
    monkeypatch.setattr(DshSession, 'start', start)
    monkeypatch.setattr(DshSession, 'close', close)
    async def failed_probe(entry):
        entry.update(exit_code=125, error=execution_error, cleanup=backend._report('clean'))
        entry['captured'] = {'stdout': proof, 'stderr': stderr.encode()}
    monkeypatch.setattr(backend, '_run', failed_probe)
    async def run():
        await backend.prepare(policy)
        assert not backend._executions
        with pytest.raises(ContractError) as caught:
            await backend.probe()
        error = caught.value
        assert error.kind == 'SANDBOX_UNAVAILABLE'
        assert f'Native {mode} probe failed' in str(error)
        assert ('WRITE_OWNER' in str(error)) == hint
        assert str(backend.control_root) in str(error)
        assert '\x1b' not in str(error)
        print_configuration_error(error)
        assert f'SANDBOX_UNAVAILABLE: {error}' in capsys.readouterr().err
        failure = json.loads((backend.control_root / 'boundary-failure.json').read_bytes())
        assert failure['stderr'] == stderr and failure['error'] == execution_error
        if execution_error:
            assert execution_error in str(error)
        assert (await backend.close())['state'] == 'clean'
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


def test_unwired_platform_refuses_before_runtime_or_processes(tmp_path, monkeypatch):
    import forge.sandbox.workspace_backend as module
    policy,backend=fixture(tmp_path)
    monkeypatch.setattr(module,'sys',SimpleNamespace(platform='unsupported'))
    async def run():
        with pytest.raises(ContractError) as caught:
            await backend.prepare(policy)
        assert caught.value.kind=='SANDBOX_UNAVAILABLE'
        assert backend._prepared is None and not backend._executions and not backend.control_root.exists()
        assert (await backend.close())['state']=='clean'
    asyncio.run(run())


def test_failed_dsh_resource_reconciliation_remains_unknown(tmp_path):
    """Fault injection exercises reporting only, never establishes native capability."""
    from datetime import datetime, timedelta, timezone
    _, backend = fixture(tmp_path)
    backend.control_root.mkdir()
    class FailedController:
        async def execute(self, *args): raise OSError('injected launch failure')
        async def cancel(self, *args): return {'state': 'unknown'}
        async def close(self): return {'state': 'unknown'}
    backend._controller = FailedController()
    async def run():
        entry = backend._accept(new_id('exec'), {'deadline_utc':(datetime.now(timezone.utc)+timedelta(seconds=5)).isoformat(),
            'output_limit_bytes':128}, publish=False)
        await entry['task']
        assert entry['cleanup']['state']=='unknown' and entry['cleanup']['completed_at_utc'] is None
        assert backend.control_root.exists()
        assert (await backend.close())['state']=='unknown'
    asyncio.run(run())


def test_partial_dsh_controller_preparation_is_owned_and_cleaned(tmp_path, monkeypatch):
    import forge.sandbox.workspace_backend as module
    import forge.sandbox.windows_worker as worker
    policy, backend = fixture(tmp_path)
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(module, 'verify_runtime', lambda root: SimpleNamespace(
        root=tmp_path / 'installation', node=tmp_path / 'node', entry=tmp_path / 'runner', tools={}))
    monkeypatch.setattr(worker, 'grant_controller_access', lambda directory: None)
    from forge.sandbox.dsh_session import DshSession
    closed = []
    async def partial(self): raise OSError('injected controller failure')
    async def close(self):
        closed.append(self)
        return {'state': 'clean'}
    monkeypatch.setattr(DshSession, 'start', partial)
    monkeypatch.setattr(DshSession, 'close', close)
    async def run():
        with pytest.raises(OSError, match='controller failure'):
            await backend.prepare(policy)
        assert (await backend.close())['state'] == 'clean'
        assert closed == [backend._controller]
        assert backend.control_root.exists()
    asyncio.run(run())


def test_relocated_control_delete_failure_never_publishes_clean(tmp_path, monkeypatch):
    import forge.sandbox.workspace_backend as module
    from forge.sandbox.path_policy import inspect_path
    _, backend = fixture(tmp_path)
    backend.control_root.mkdir()
    backend._control_anchor = inspect_path(backend.control_root, absolute=True)
    backend.diagnostic_root = tmp_path / 'diagnostics'
    backend.diagnostic_root.mkdir()
    backend._diagnostic_anchor = inspect_path(backend.diagnostic_root, absolute=True)
    locator = backend.diagnostic_root / 'control-location.json'
    locator.write_text(json.dumps({'state': 'preparing'}))
    (backend.control_root / 'boundary.json').write_text('{}')
    def fail_delete(path):
        raise OSError('injected owned directory deletion failure')
    monkeypatch.setattr(module.shutil, 'rmtree', fail_delete)
    assert asyncio.run(backend.close())['state'] == 'unknown'
    assert json.loads(locator.read_text())['state'] != 'clean'
    assert backend.control_root.exists()
    assert (backend.diagnostic_root / 'boundary.json').exists()


@pytest.mark.parametrize('consume,command_limit,session_limit', [
    (True, 1048576, 1048576), (False, 1048576, 1048576),
    ('slow', 1048576, 1048576), (True, 16384, 1048576), (True, 1048576, 8192)])
def test_burst_output_serves_active_consumer_without_relaxing_bounded_limits(
        tmp_path, consume, command_limit, session_limit):
    import base64
    _, backend = fixture(tmp_path)
    backend._policy = SimpleNamespace(value={'limits': {'session_artifact_bytes': session_limit}})
    payload = bytes(range(256)) * 1024
    class BufferedReader:
        position = 0
        async def read(self, size):
            chunk = payload[self.position:self.position + size]
            self.position += len(chunk)
            return chunk
    entry = {'execution_id':new_id('exec'), 'owner':backend.owner, 'command':{'output_limit_bytes':command_limit},
             'retained':0, 'discarded_bytes':0, 'stdout_bytes':0, 'publish':True, 'sequence':0}
    async def run():
        received = bytearray()
        async def collect():
            while (frame := await backend.outputs.get()) is not None:
                received.extend(base64.b64decode(frame['raw_base64']))
                if consume == 'slow':
                    await asyncio.sleep(0.002)
        consumer = asyncio.create_task(collect()) if consume else None
        reader = BufferedReader()
        while raw := await reader.read(16384):
            await backend._output(entry, 'stdout', raw)
        assert backend.outputs.qsize() <= backend.outputs.maxsize == 8
        if consumer is not None:
            await backend.outputs.put(None)
            await consumer
            expected = min(len(payload), command_limit, session_limit)
            if consume == 'slow':
                assert 0 < len(received) <= expected
                assert entry['discarded_bytes'] == len(payload) - len(received)
            else:
                assert bytes(received) == payload[:expected]
                assert entry['discarded_bytes'] == len(payload) - expected
        else:
            assert entry['discarded_bytes'] > 0
        assert entry['retained'] <= min(command_limit, session_limit)
        assert backend._retained <= session_limit
    asyncio.run(run())
