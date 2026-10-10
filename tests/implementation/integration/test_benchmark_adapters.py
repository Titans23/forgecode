"""Real processes, independent tests, immutable captures and original CLI compatibility."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from benchmark.adapters.harbor import HarborAdapter, HarborExecutor, export_runspec
from benchmark.adapters.materialize import resolve_task
from benchmark.adapters.process import run_process
from benchmark.adapters.protocol import (capture_tree,collect_harbor,content_hash,grader_cache_key,
    normalize,reward_result)
from forge.application.models import ContractError,canonical_hash
from test_evaluations import make_evaluation,create,start,claim_next


@pytest.mark.parametrize('mode,exit_code,grade_state,grade_result,origin',[
    ('pass',0,'graded','pass',None),('fail',0,'graded','fail',None),
    ('nonzero',17,'graded','pass','runner_crash'),('missing',0,'unscored','unknown',None),
    ('grader-error',0,'grader_error','unknown','grader_infrastructure')])
def test_real_process_and_independent_grader_keep_three_axes(tmp_path,mode,exit_code,grade_state,grade_result,origin):
    output=tmp_path/'official-format-synthetic-output'
    process=asyncio.run(run_process([sys.executable,str(Path('tests/implementation/fixtures/runner_protocol.py').resolve()),
        str(output),mode,'a'*64],cwd=Path.cwd(),env=os.environ.copy(),output_dir=tmp_path/'process',timeout_seconds=20))
    assert process['exit_code']==exit_code
    collected=collect_harbor(output,task_name='synthetic-process-fixture',task_checksum='a'*64,runner_exit=exit_code)
    result=normalize(runner_exit=exit_code,result=collected['result'],agent_status=collected['agent_status'],
        cleanup='clean',provenance='explicit synthetic process fixture')
    assert (result['grading_state'],result['grade_result'],result['error_origin'])==(grade_state,grade_result,origin)
    assert result['agent_outcome']=='completed'
    assert result['cleanup_state']=='clean'
    assert collected['artifact_present']
    original=collected['artifact_hash']
    (output/'fixture-trial/artifacts/answer.txt').write_text('changed actual patch')
    second=collect_harbor(output,task_name='synthetic-process-fixture',task_checksum='a'*64,runner_exit=exit_code)
    key=lambda value:grader_cache_key(grader_hash='b'*64,environment_hash='c'*64,task_revision='revision',artifact_hash=value)
    assert key(original)!=key(second['artifact_hash'])
    assert collected['files']['fixture-trial/artifacts/answer.txt'] in (b'41',b'42')


def test_collection_refuses_foreign_identity_ambiguous_results_links_and_bad_rewards(tmp_path):
    root=tmp_path/'data';root.mkdir();(root/'one.txt').write_text('real artifact')
    captured=capture_tree(root)
    assert len(content_hash(captured))==64
    for invalid in [True,float('nan'),float('inf'),'-1','1.01',None,'not-a-reward']:
        assert reward_result(invalid)==(None,'unknown')
    assert reward_result('1.000')==('1','pass')
    assert reward_result('0.000')==('0','fail')
    link=root/'linked.txt';os.link(root/'one.txt',link)
    with pytest.raises(ContractError,match='links'):
        capture_tree(root)
    # Real independently generated output, then a mismatched expected task identity.
    generated=tmp_path/'generated'
    process=asyncio.run(run_process([sys.executable,str(Path('tests/implementation/fixtures/runner_protocol.py').resolve()),
        str(generated),'pass','a'*64],cwd=Path.cwd(),env=os.environ.copy(),output_dir=tmp_path/'process',timeout_seconds=20))
    with pytest.raises(ContractError,match='identity'):
        collect_harbor(generated,task_name='foreign-public-benchmark',task_checksum='a'*64,runner_exit=0)
    (generated/'second').mkdir();(generated/'second/result.json').write_text('{}')
    with pytest.raises(ContractError,match='Multiple'):
        collect_harbor(generated,task_name='synthetic-process-fixture',task_checksum='a'*64,runner_exit=process['exit_code'])


def test_actual_process_cancellation_terminates_owned_child_and_bounds_output(tmp_path):
    async def exercise():
        marker=tmp_path/'pid.txt'
        program='import os,pathlib,sys,time;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));print("x"*5000000,flush=True);time.sleep(30)'
        task=asyncio.create_task(run_process([sys.executable,'-c',program,str(marker)],cwd=tmp_path,
            env=os.environ.copy(),output_dir=tmp_path/'logs',timeout_seconds=20))
        for _ in range(500):
            if marker.exists():break
            await asyncio.sleep(.01)
        assert marker.exists()
        await asyncio.sleep(.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert (tmp_path/'logs/stdout.log').stat().st_size<=4194304
        pid=int(marker.read_text())
        if os.name=='nt':
            # Actual OS handle probe: already killed, no surviving PID with this handle.
            import ctypes
            handle=ctypes.windll.kernel32.OpenProcess(0x1000,False,pid)
            if handle:
                code=ctypes.c_ulong();ctypes.windll.kernel32.GetExitCodeProcess(handle,ctypes.byref(code))
                ctypes.windll.kernel32.CloseHandle(handle)
                assert code.value!=259
        else:
            with pytest.raises(ProcessLookupError):os.kill(pid,0)
    asyncio.run(exercise())


def test_windows_target_feedback_sampling_and_unapproved_live_are_explicitly_blocked(tmp_path):
    from benchmark.core.spec import freeze_spec
    methods,spec=make_evaluation(tmp_path)
    try:
        _,values,_=freeze_spec(methods.store,spec)
        spec=deepcopy(spec);spec['dataset']['name']='aider-polyglot';spec['execution']['target_platform']='windows-native'
        spec['protocol']['feedback']='benchmark_defined';values['model_parameters']['temperature']='1'
        values['environment']['platform']='windows-native'
        spec['execution']['environment_snapshot']['sha256']=canonical_hash(values['environment'])
        spec['model']['parameters']['sha256']=canonical_hash(values['model_parameters'])
        adapter=HarborAdapter('aider-polyglot')
        issues=adapter.validate(spec,values)
        assert any('native Windows' in item['message'] for item in issues)
        assert any('feedback' in item['message'] for item in issues)
        assert any('temperature' in item['message'] for item in issues)
        assert any(item['kind']=='live_authorization_binding_unverified' for item in issues)
        assert adapter.describe()['legacy_feedback_default']
    finally:methods.store.close()


def test_official_executor_records_blocked_without_mock_model_or_grade(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        executor=HarborExecutor(methods.service,adapter=HarborAdapter('aider-polyglot'))
        methods.evaluations.executor=executor
        run,_=create(methods,spec);start(methods,run)
        attempt=methods.store.connection.execute("SELECT business_id FROM work_items WHERE kind='attempt' ORDER BY rowid LIMIT 1").fetchone()[0]
        asyncio.run(methods.evaluations.scheduler.execute(attempt))
        report=methods.evaluations.report_data(run['run_id'])
        assert report['attempts'][0]['execution_state']=='blocked'
        assert report['attempts'][0]['grade_result'] is None
        assert not list((methods.store.data_dir/'evaluation-runners').glob('*'))
        assert methods.store.connection.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0]==0
        assert methods.store.connection.execute('SELECT COUNT(*) FROM grades').fetchone()[0]==0
    finally:methods.store.close()


def test_export_uses_actual_snapshot_hashes_and_cli_does_not_overwrite_or_run(tmp_path):
    from benchmark.core.spec import freeze_spec
    methods,spec=make_evaluation(tmp_path)
    try:
        spec,values,digest=freeze_spec(methods.store,spec)
        exported=export_runspec(spec,values)
        assert exported['spec_hash']==digest and exported['read_only_plan']
        plan=tmp_path/'plan.json';destination=tmp_path/'export.json'
        plan.write_text(json.dumps({'spec':spec,'resolved_snapshots':values}),encoding='utf-8')
        argv=[sys.executable,'-m','benchmark.adapters','export','--plan',str(plan),'--output',str(destination)]
        first=subprocess.run(argv,capture_output=True,text=True,timeout=20)
        assert first.returncode==0,first.stdout+first.stderr
        before=destination.read_bytes()
        assert json.loads(before)==exported
        assert subprocess.run(argv,capture_output=True,timeout=20).returncode!=0
        assert destination.read_bytes()==before
        values['harness']['parent_budget']['max_model_calls']+=1
        with pytest.raises(ContractError,match='snapshot'):
            export_runspec(spec,values)
        assert methods.store.connection.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0]==0
    finally:methods.store.close()


def test_frozen_harbor_harness_runs_real_tools_and_tests_without_extra_repair_budget(tmp_path):
    import shlex
    import shutil
    from benchmark.harbor.run_forge import run_turn
    from forge.application.harness_adapter import LocalTrustedBackend
    from forge.config import ForgeConfig
    from forge.engine.test_profile import scripted_profile
    from forge.permissions.policy import PermissionManager
    from forge.runtime.dependencies import RuntimeBindings
    from forge.engine.persistence import new_id
    methods,spec=make_evaluation(tmp_path)
    try:
        from benchmark.core.spec import freeze_spec
        _,values,_=freeze_spec(methods.store,spec)
        project=tmp_path/'real-project'
        shutil.copytree('tests/implementation/fixtures/fix-python-add/project',project)
        raw=json.loads(Path('tests/implementation/fixtures/fix-python-add/script.json').read_text())
        python=subprocess.list2cmdline([sys.executable]) if os.name=='nt' else shlex.quote(sys.executable)
        for response in raw['responses']:
            for call in response.get('tool_calls',[]):
                if 'command' in call['arguments']:
                    call['arguments']['command']=call['arguments']['command'].replace('${PYTHON}',python)
        profile=scripted_profile(raw)
        clients=[]
        def factory(config,**kwargs):
            client=profile.model_client_factory(config,**kwargs);clients.append(client);return client
        bindings=RuntimeBindings(config=ForgeConfig(api_key='explicit-scripted-only',model_id='synthetic-fixture',
            context_window=128000,max_tokens=1024),model_client_factory=factory,data_root=tmp_path/'harness',
            permission_manager=PermissionManager(project,load_stored_rules=False),backend=LocalTrustedBackend(),trusted_extensions=False)
        config={'parameters':values['model_parameters'],'harness':values['harness'],
            'scope':{'trace_id':'1'*32,'span_id':'2'*16,'run_id':new_id('run'),'trial_id':new_id('trial')},
            'attempt_id':new_id('attempt')}
        config['harness']['max_context_tokens']=128000
        # Observe the real packaged prompt/tool definitions without making a
        # request, then bind the positive scripted run to that exact RunSpec.
        from dataclasses import replace
        from benchmark.harbor.run_forge import BENCHMARK_TASK_POLICY
        from forge.runtime.factory import create_runtime
        from forge.runtime.profile import ExecutionProfile
        probe,probe_journal,_=create_runtime(project,bindings=replace(bindings,data_root=tmp_path/'probe'),
            execution_profile=ExecutionProfile.sandbox(),task_relation='new',
            task_policy=replace(BENCHMARK_TASK_POLICY,max_delivery_repairs=0))
        spec['harness']['prompt_sha256']=sha256(probe.system_prompt.encode('utf-8')).hexdigest()
        spec['harness']['tool_schema_sha256']=canonical_hash(probe._tool_definitions())
        asyncio.run(probe.runtime_close());probe_journal.record_stopped()
        spec['harness']['configuration']['sha256']=canonical_hash(values['harness'])
        spec['model_mode']='live'
        spec['model'].update(provider=bindings.config.provider,requested_model=bindings.config.model_id)
        config['plan']=export_runspec(spec,values)
        result_path=tmp_path/'actual-result.json'
        result=asyncio.run(run_turn(project,'Fix integer addition; run the unchanged standard-library tests.',
            resume=False,max_model_calls=10,max_tool_calls=20,max_turn_seconds=30,
            frozen_configuration=config,runtime_bindings=bindings,result_path=result_path))
        assert 'return left + right' in (project/'calculator.py').read_text()
        assert subprocess.run([sys.executable,'-m','unittest','-v'],cwd=project,capture_output=True).returncode==0
        assert result.model_calls==5 and sum(client.calls for client in clients)==5
        assert json.loads(result_path.read_text())['model_calls']==5
        journals=list((tmp_path/'harness').rglob('*.jsonl'))
        assert journals
        events=[json.loads(row) for row in journals[0].read_text(encoding='utf-8').splitlines()]
        observations=[event['payload']['event'] for event in events if event.get('type')=='observation']
        assert observations and all(event['attempt_id']==config['attempt_id'] for event in observations)
        with pytest.raises(ValueError,match='Unsupported'):
            asyncio.run(run_turn(project,'Must not call model',resume=True,max_model_calls=10,max_tool_calls=20,
                max_turn_seconds=30,frozen_configuration=config,runtime_bindings=bindings))
        assert sum(client.calls for client in clients)==5
    finally:methods.store.close()


def test_cleanup_receipts_do_not_promote_an_unverified_stop_to_clean():
    from benchmark.adapters.docker import cleanup_state
    assert cleanup_state([])=='unknown'
    first=[{'resource_id':'a','phase':'start_intent'},{'resource_id':'a','phase':'started'},
        {'resource_id':'a','phase':'stopped','deleted':True}]
    assert cleanup_state(first)=='unknown'
    assert cleanup_state(first+[{'resource_id':'b','phase':'start_intent'}])=='unknown'
    assert cleanup_state([{'resource_id':'forged','phase':'stopped','deleted':True}])=='unknown'
    assert cleanup_state([*first[:-1],{**first[-1],'deleted':False}])=='unknown'


def test_official_docker_stop_preserves_unknown_when_upstream_swallows_down_failure(tmp_path):
    import logging
    from benchmark.adapters.docker import EvaluationDockerEnvironment, cleanup_state
    environment = object.__new__(EvaluationDockerEnvironment)
    environment._receipt_path = tmp_path / 'cleanup.jsonl'
    environment._receipt_id = 'owned-fixture'
    environment._keep_containers = False
    environment.logger = logging.getLogger(__name__)
    async def prepare():
        pass
    calls = []
    async def fail_down(command, **kwargs):
        calls.append(command)
        raise RuntimeError('synthetic daemon unavailable')
    environment.prepare_logs_for_host = prepare
    environment._run_docker_compose_command = fail_down
    environment._cleanup_mounts_compose_file = lambda: None
    environment._cleanup_resources_compose_file = lambda: None
    environment._cleanup_egress_control_services_compose_file = lambda: None
    environment._receipt('start_intent')
    # Runs pinned Harbor's actual stop implementation. No Docker resource is created.
    asyncio.run(environment.stop(delete=True))
    assert calls == [['down', '--rmi', 'local', '--volumes', '--remove-orphans']]
    receipts = [json.loads(row) for row in environment._receipt_path.read_text().splitlines()]
    assert receipts[-1]['phase'] == 'stop_returned'
    assert receipts[-1]['delete_requested'] is True
    assert 'deleted' not in receipts[-1]
    assert cleanup_state(receipts) == 'unknown'

def test_docker_cleanup_requires_same_daemon_and_empty_resource_queries():
    from benchmark.adapters.docker import cleanup_state
    empty={'daemon_id':'fixture-daemon','project':'owned-fixture','containers':[],'volumes':[],'networks':[]}
    before={**empty,'containers':['container-fixture'],'volumes':['anonymous-fixture']}
    rows=[{'resource_id':'a','phase':'start_intent'}, {'resource_id':'a','phase':'baseline','inventory':empty},
        {'resource_id':'a','phase':'stop_returned','delete_requested':True},
        {'resource_id':'a','phase':'resource_query','before':before,'after':empty}]
    assert cleanup_state(rows)=='clean'
    assert cleanup_state([*rows[:-1],{**rows[-1],'after':{**empty,'volumes':['anonymous-fixture']}}])=='residual'
    assert cleanup_state([*rows[:-1],{**rows[-1],'after':{**empty,'daemon_id':'different'}}])=='unknown'
    assert cleanup_state([*rows,{'resource_id':'a','phase':'query_failed'}])=='unknown'
    assert cleanup_state([*rows,{'resource_id':'b','phase':'start_intent'}])=='unknown'
