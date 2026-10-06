"""Actual SQLite/CAS/RPC scheduling, with explicitly synthetic grade fixtures."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from benchmark.core.scheduler import emit
from forge.application.models import ContractError, canonical_hash, validate
from forge.application.services import ApplicationServices
from forge.engine.methods import EngineMethods
from forge.engine.persistence import Store, encoded, new_id
from forge.engine.rpc import RpcServer
from forge.engine.test_profile import MemoryCredentials


def make_evaluation(tmp_path, *, repeats=1, categories=None, service=None):
    store = service.store if service else Store(tmp_path / 'data')
    service = service or ApplicationServices(store, profile_id='evaluation-test', credentials=MemoryCredentials())
    methods = EngineMethods(service, profile='test')
    spec = json.loads(Path('contracts/v1/examples/run-spec.valid.json').read_text())
    spec['dataset'] = {'name': 'eval-mini-bundle', 'revision': 'synthetic-fixture-v1',
        'task_ids': ['case-pass', 'case-fail', 'case-unscored', 'case-retry'],
        'task_revisions': {name: 'v1' for name in ('case-pass','case-fail','case-unscored','case-retry')}}
    spec['protocol'].update(repeats=repeats, max_infrastructure_attempts=2,
        infrastructure_retry_categories=['runner_crash','runner_unavailable'] if categories is None else categories)
    spec['execution']['target_platform'] = 'official-environment'
    spec['budget'].update(attempt_wall_seconds=30, trial_wall_seconds=60)
    environment = {'platform':'official-environment','os_build':'synthetic-fixture','architecture':'x64',
        'filesystem':'fixture','toolchains':[],'backend_version':'unavailable'}
    capabilities = {'platform':'unsupported','backend':'unavailable','backend_version':'fixture',
        'read_isolation':'unavailable','write_isolation':False,'direct_network_isolation':False,
        'dns_isolation':False,'socket_isolation':False,'process_cleanup':False,
        'resource_enforcement':{'memory':'unavailable','disk':'unavailable','pids':'unavailable'},
        'readiness':'unavailable','issues':['synthetic metadata; no native capability claimed'],
        'measured_at_utc':'2026-10-06T21:00:00Z'}
    policy = {'schema_version':'forge.sandbox.policy.v1','policy_id':new_id('policy'),'workspace_id':new_id('ws'),
        'filesystem':{'read_mode':'backend_default_with_protected_paths','read_roots':[str(tmp_path)],
            'write_roots':[str(tmp_path)],'protected_paths':[],'deny_overrides_allow':True,'reject_unsafe_links':True},
        'network':{'mode':'deny_direct','allowed_domains':[],'dns_isolation_required':False},
        'limits':{'memory_bytes':None,'disk_bytes':None,'pids':None,'wall_time_seconds':30,
            'command_output_bytes':1048576,'session_artifact_bytes':104857600},
        'environment_keys':[],'fallback':'deny','session_mutation':'replace_session'}
    values = [(spec['source'],'source_snapshot',{'origin':'synthetic-fixture','files':{}}),
        (spec['model'],'parameters',{'temperature':None,'top_p':None,'max_output_tokens':1024,'reasoning_effort':None}),
        (spec['harness'],'configuration',{'max_context_tokens':4096,'compaction_enabled':True,'max_delivery_repairs':0,
            'parent_budget':{'max_model_calls':10,'max_tool_calls':20,'wall_seconds':30},'explore_enabled':True,'trusted_extensions_enabled':False}),
        (spec['execution'],'environment_snapshot',environment),(spec['execution'],'sandbox_capabilities',capabilities),
        (spec['execution'],'sandbox_policy',policy), (spec['execution'],'network_cache_configuration',
            {'network_mode':'deny_direct','allowed_domains':[],'cache_mode':'cold','cache_snapshot':None}),
        (spec['grader'],'configuration',{'adapter_id':'fixture-assertions','revision':'v1','artifact_types':[],
            'timeout_seconds':10,'parameters':{}}), (spec['grader'],'environment',environment),
        (spec['observability'],'pricing_snapshot',{'revision':'fixture-v1','currency':'USD','source':'synthetic-fixture',
            'rates':[{'provider':'scripted_mock','model':'fixture-model-v1',
                'per_million':{'input_tokens':'2','output_tokens':'10','cache_read_tokens':'0.2','cache_write_tokens':'2.5'}}]})]
    with store.transaction():
        for parent, key, value in values:
            parent[key] = store._configuration_snapshot(value, canonical_hash(value))
    return methods, spec


def create(methods, spec):
    validation = methods.evaluations.validate({'spec':spec})
    params = {'client_action_id':new_id('act'),'spec':spec,
        'validation_ticket':validation['validation_ticket'],'spec_hash':validation['spec_hash']}
    return methods.evaluations.create_run(params), params


def start(methods, run):
    return methods.evaluations.start({'client_action_id':new_id('act'),'run_id':run['run_id']})


def claim_next(methods):
    row = methods.store.connection.execute("SELECT id,version FROM work_items WHERE kind='attempt' AND state='queued' ORDER BY rowid LIMIT 1").fetchone()
    return methods.evaluations.scheduler.claim(row['id'],expected_version=row['version'])


def grade_fixture(methods, work, result, state='graded'):
    """Synthetic fixture enters through trusted host storage, never through Agent/RPC text."""
    store = methods.store
    identity = new_id('grade')
    reward = '1' if result=='pass' else '0' if result=='fail' else None
    with store.transaction():
        store.connection.execute('INSERT INTO grades VALUES(?,?,?,?,?,?,?)',
            (identity,work['business_id'],'1'*64,'2'*64,state,reward,result))
        emit(store,'grade.finished',{'attempt_id':work['business_id'],'grader_hash':'1'*64,'artifact_hash':'2'*64,
            'raw_reward_decimal':reward,'grade_state':state,'grade_result':result},run_id=work['run_id'],
            trial_id=work['trial_id'],attempt_id=work['business_id'],trace_id=work['trace_id'],span_id=work['span_id'])
    return identity


def finish(methods, work, **kwargs):
    methods.evaluations.scheduler.finish(work['id'],owner_epoch=methods.store.epoch,expected_version=work['version'],
        execution_state=kwargs.pop('execution_state','finished'),cleanup_state=kwargs.pop('cleanup_state','clean'),**kwargs)


def test_acceptance_is_atomic_idempotent_and_unclaimed_trials_remain_in_denominator(tmp_path):
    methods,spec = make_evaluation(tmp_path,repeats=2)
    try:
        run,params = create(methods,spec)
        assert run['state']=='created' and len(run['trial_ids'])==8
        assert methods.evaluations.create_run(params)['reused_existing_action']
        with pytest.raises(ContractError) as error:
            methods.evaluations.create_run({**params,'spec_hash':'0'*64})
        assert error.value.kind=='IDEMPOTENCY_CONFLICT'
        assert methods.store.connection.execute('SELECT COUNT(*) FROM runs').fetchone()[0]==1
        report = methods.evaluations.report_data(run['run_id'])
        assert report['metrics']['counts']['planned']==8
        assert report['metrics']['grading_coverage']['value']=='0'
        assert report['metrics']['cost_per_success']['status']=='N/A'
        assert methods.store.connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==0
        with pytest.raises(sqlite3.IntegrityError):
            with methods.store.transaction():
                methods.store.connection.execute("UPDATE trials SET task_revision='changed'")
        saved=methods.evaluations.report({'run_id':run['run_id']})
        validate('evaluation.report.result',saved)
        assert json.loads(methods.store.read_artifact(saved['report_artifact']['artifact_id']))['metrics']==report['metrics']
    finally: methods.store.close()


def test_stale_ticket_snapshot_or_mutated_harness_budget_cannot_accept_partial_plan(tmp_path):
    methods,spec = make_evaluation(tmp_path)
    try:
        validation=methods.evaluations.validate({'spec':spec})
        spec['dataset']['task_ids'].pop()
        spec['dataset']['task_revisions'].pop('case-retry')
        with pytest.raises(ContractError):
            methods.evaluations.create_run({'client_action_id':new_id('act'),'spec':spec,
                'validation_ticket':validation['validation_ticket'],'spec_hash':canonical_hash(spec)})
        assert methods.store.connection.execute('SELECT COUNT(*) FROM trials').fetchone()[0]==0
        changed=deepcopy(spec)
        changed['harness']['max_delivery_repairs']=2
        with pytest.raises(ContractError): methods.evaluations.validate({'spec':changed})
        changed=deepcopy(spec)
        changed['execution']['environment_snapshot']['sha256']='0'*64
        with pytest.raises(ContractError) as error: methods.evaluations.validate({'spec':changed})
        assert error.value.kind=='STALE_REVISION'
    finally: methods.store.close()


def test_single_worker_old_epoch_and_terminal_grade_cleanup_are_separate(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        run,_=create(methods,spec);start(methods,run)
        work=claim_next(methods)
        methods.evaluations.scheduler.heartbeat(work['id'],owner_epoch=methods.store.epoch,expected_version=work['version'])
        with pytest.raises(ContractError): claim_next(methods)
        with pytest.raises(ContractError) as error:
            methods.evaluations.scheduler.finish(work['id'],owner_epoch=new_id('epoch'),expected_version=work['version'],execution_state='finished',cleanup_state='clean')
        assert error.value.kind=='INDETERMINATE'
        grade=grade_fixture(methods,work,'fail')
        finish(methods,work,agent_outcome='completed',grade_id=grade)
        data=methods.evaluations.report_data(run['run_id'])
        assert data['metrics']['completion_false_positive']['value']=='1'
        assert data['metrics']['grading_coverage']['value']=='0.25'
        assert data['attempts'][0]['cleanup_state']=='clean'
        assert data['attempts'][0]['execution_state']=='finished'
        assert data['attempts'][0]['grade_result']=='fail'
        with pytest.raises(ContractError): finish(methods,work,grade_id=grade)
        work2=claim_next(methods)
        finish(methods,work2,execution_state='error',cleanup_state='unknown',error_origin='runner_crash')
        assert methods.evaluations.run(run['run_id'])['state']=='indeterminate'
        with pytest.raises(ContractError): claim_next(methods)
    finally: methods.store.close()


def test_infrastructure_retry_is_explicit_bounded_and_last_authorized_not_best(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        run,_=create(methods,spec);start(methods,run)
        first=claim_next(methods)
        grade=grade_fixture(methods,first,'pass')
        finish(methods,first,grade_id=grade,execution_state='error',error_origin='runner_crash')
        params={'client_action_id':new_id('act'),'trial_id':first['trial_id'],'reason':'Retry declared runner infrastructure error'}
        retry=methods.evaluations.retry(params)
        assert methods.evaluations.retry(params)=={**retry,'reused_existing_action':True}
        assert methods.store.connection.execute('SELECT COUNT(*) FROM trials').fetchone()[0]==4
        data=methods.evaluations.report_data(run['run_id'])
        assert data['metrics']['counts']['passed']==0
        assert data['metrics']['first_attempt_success_rate']['value']=='0.25'
        second=methods.evaluations.scheduler.claim(retry['work_item_id'],expected_version=0)
        finish(methods,second,execution_state='error',error_origin='runner_crash')
        with pytest.raises(ContractError): methods.evaluations.retry({**params,'client_action_id':new_id('act')})
        third=claim_next(methods)
        finish(methods,third,error_origin='task_logic',grade_id=grade_fixture(methods,third,'fail'))
        with pytest.raises(ContractError) as error:
            methods.evaluations.retry({'client_action_id':new_id('act'),'trial_id':third['trial_id'],'reason':'Try task again'})
        assert error.value.kind=='UNAUTHORIZED'
    finally: methods.store.close()


def test_restart_retains_old_owner_and_unknown_cleanup_without_automatic_reexecution(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    run,_=create(methods,spec);start(methods,run);work=claim_next(methods)
    old_epoch=methods.store.epoch
    methods.store.close()
    reopened=Store(tmp_path/'data')
    service=ApplicationServices(reopened,profile_id='evaluation-test',credentials=MemoryCredentials())
    current=EngineMethods(service,profile='test')
    try:
        assert reopened.epoch!=old_epoch
        row=reopened.connection.execute('SELECT * FROM attempt_details WHERE attempt_id=?',(work['business_id'],)).fetchone()
        assert row['owner_epoch']==old_epoch
        assert reopened.connection.execute('SELECT state FROM work_items WHERE id=?',(work['id'],)).fetchone()[0]=='reconciling'
        assert current.evaluations.run(run['run_id'])['state']=='indeterminate'
        with pytest.raises(ContractError):
            current.evaluations.scheduler.finish(work['id'],owner_epoch=old_epoch,expected_version=work['version'],execution_state='finished',cleanup_state='clean')
        assert reopened.connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==4
        with pytest.raises(ContractError): claim_next(current)
        original=reopened.connection.execute('SELECT trace_id FROM attempt_details WHERE attempt_id=?',(work['business_id'],)).fetchone()[0]
        recovery=json.loads(reopened.connection.execute("SELECT body_json FROM events WHERE json_extract(body_json,'$.event_type')='recovery.started'").fetchone()[0])
        assert recovery['trace_id']!=original and recovery['attributes']['trace_links'][0]['trace_id']==original
        version=reopened.connection.execute('SELECT version FROM work_items WHERE id=?',(work['id'],)).fetchone()[0]
        with pytest.raises(ContractError): current.evaluations.scheduler.reconcile(work['id'],expected_version=version,cleanup_observation={})
    finally: reopened.close()


def test_evaluation_and_real_accepted_interactive_turn_share_the_execution_mutex(tmp_path):
    from test_application import setup
    service,store,_,clients,_,params=setup(tmp_path)
    methods,spec=make_evaluation(tmp_path,service=service)
    try:
        accepted=service.start_turn(params)
        interactive=store.connection.execute('SELECT * FROM work_items WHERE business_id=?',(accepted['turn_id'],)).fetchone()
        owner=store.claim_work_item(interactive['id'],expected_version=interactive['version'])
        run,_=create(methods,spec);start(methods,run)
        with pytest.raises(ContractError): claim_next(methods)
        assert not clients
        store.finish_work_item(owner['id'],owner_epoch=store.epoch,expected_version=owner['version'],outcome='cancelled')
        attempt=claim_next(methods)
        accepted2=service.start_turn({**params,'client_action_id':new_id('act')})
        with pytest.raises(ContractError): asyncio.run(service.execute_turn(accepted2['turn_id']))
        assert not clients
        finish(methods,attempt,agent_outcome='cancelled',execution_state='cancelled')
        asyncio.run(service.execute_turn(accepted2['turn_id']))
        assert clients and store.connection.execute('SELECT outcome FROM turns WHERE id=?',(accepted2['turn_id'],)).fetchone()[0]=='completed'
    finally: store.close()


def test_real_rpc_unavailable_runner_blocks_without_fake_grade_and_shutdown_cancels_queue(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    server=RpcServer(methods)
    async def exercise():
        methods.initialized=True
        run,_=create(methods,spec)
        request={'jsonrpc':'2.0','id':'start','method':'evaluation.start','params':{'client_action_id':new_id('act'),'run_id':run['run_id']}}
        response=await server.handle_frame(encoded(request).encode())
        assert response['result']['state']=='queued'
        work=methods.store.connection.execute("SELECT business_id FROM work_items WHERE kind='attempt' ORDER BY rowid LIMIT 1").fetchone()[0]
        await methods.evaluations.scheduler.execute(work)
        assert methods.evaluations.report_data(run['run_id'])['attempts'][0]['execution_state']=='blocked'
        assert methods.store.connection.execute('SELECT COUNT(*) FROM grades').fetchone()[0]==0
        methods.begin_shutdown('cancel','test EOF')
        assert not methods.store.connection.execute("SELECT 1 FROM work_items WHERE state='queued'").fetchone()
        denied=await server.handle_frame(encoded({**request,'params':{**request['params'],'client_action_id':new_id('act')}}).encode())
        assert denied['error']['data']['kind']=='INDETERMINATE'
        replay=await server.handle_frame(encoded(request).encode())
        assert replay['result']['reused_existing_action']
    try: asyncio.run(exercise())
    finally: methods.store.close()


def test_real_harness_attempt_journal_costs_and_replay_use_one_actual_request_ledger(tmp_path):
    from forge.application.harness_adapter import HarnessAdapter,LocalTrustedBackend
    from forge.config import ForgeConfig
    from forge.engine.journal_projection import JournalProjector
    from forge.observability.events import Scope
    from test_application import ScriptedClient,script
    methods,spec=make_evaluation(tmp_path)
    project=tmp_path/'actual-project'
    project.mkdir();(project/'value.txt').write_text('B')
    run,_=create(methods,spec);start(methods,run);work=claim_next(methods)
    def factory(config,**kwargs):
        client=ScriptedClient(script())
        client.provider='scripted_mock';client.model='fixture-model-v1'
        return client
    async def exercise():
        adapter=HarnessAdapter(project,config=ForgeConfig(api_key='unused-fixture',model_id='fixture-model-v1'),
            data_root=methods.store.data_dir/'harness',backend=LocalTrustedBackend(),
            budget={'max_model_calls':10,'max_tool_calls':20,'wall_seconds':30},model_client_factory=factory,task_relation='new')
        adapter.journal.observation_scope=Scope(trace_id=work['trace_id'],parent_span_id=work['span_id'],
            identities={'run_id':run['run_id'],'trial_id':work['trial_id'],'attempt_id':work['business_id']})
        try:
            async for _ in adapter.stream([{'text':'Read value.txt and report its value.'}]): pass
            projector=JournalProjector(methods.store)
            assert projector.project_attempt(adapter.journal.path,work['business_id'])>0
            assert projector.project_attempt(adapter.journal.path,work['business_id'])==0
            data=methods.evaluations.report_data(run['run_id'])
            assert data['metrics']['request_count']==2
            assert data['metrics']['total_known_cost']=='0.000116'
            assert data['metrics']['unknown_cost_requests']==0
            grade=grade_fixture(methods,work,'pass')
            finish(methods,work,grade_id=grade,agent_outcome='completed')
            final=methods.evaluations.report_data(run['run_id'])
            assert final['metrics']['cost_per_success']['value']=='0.000116'
            assert final['metrics']['trace_completeness']['numerator']==1
            assert projector.project_attempt(adapter.journal.path,work['business_id'])==0
        finally: await adapter.close()
    try: asyncio.run(exercise())
    finally: methods.store.close()


def test_trial_creation_rolls_back_every_plan_and_event_on_actual_sql_failure(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        validation=methods.evaluations.validate({'spec':spec})
        params={'client_action_id':new_id('act'),'spec':spec,'spec_hash':validation['spec_hash'],
            'validation_ticket':validation['validation_ticket']}
        methods.store.connection.execute("CREATE TRIGGER fail_second_trial BEFORE INSERT ON trials WHEN NEW.task_id='case-fail' BEGIN SELECT RAISE(ABORT,'controlled disk transaction fixture'); END")
        with pytest.raises(sqlite3.IntegrityError): methods.evaluations.create_run(params)
        for table in ('runs','trials','actions','events'):
            assert methods.store.connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0
        assert methods.store.connection.execute('SELECT consumed_run_id FROM validation_tickets').fetchone()[0] is None
        methods.store.connection.execute('DROP TRIGGER fail_second_trial')
        assert len(methods.evaluations.create_run(params)['trial_ids'])==4
    finally: methods.store.close()


def test_default_protocol_does_not_allow_infrastructure_retry(tmp_path):
    methods,spec=make_evaluation(tmp_path,categories=[])
    try:
        spec['protocol'].pop('infrastructure_retry_categories')
        run,_=create(methods,spec);start(methods,run);work=claim_next(methods)
        finish(methods,work,execution_state='blocked',error_origin='runner_unavailable')
        with pytest.raises(ContractError) as error:
            methods.evaluations.retry({'client_action_id':new_id('act'),'trial_id':work['trial_id'],'reason':'not preauthorized'})
        assert error.value.kind=='UNAUTHORIZED'
        assert methods.store.connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==4
    finally: methods.store.close()


def test_grader_error_and_trial_wall_budget_do_not_become_task_failure_or_free_retry(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        run,_=create(methods,spec);start(methods,run);work=claim_next(methods)
        grade=grade_fixture(methods,work,None,state='grader_error')
        finish(methods,work,grade_id=grade,error_origin='grader_infrastructure')
        data=methods.evaluations.report_data(run['run_id'])
        assert data['attempts'][0]['execution_state']=='finished'
        assert data['attempts'][0]['grade_state']=='grader_error'
        assert data['metrics']['counts']['failed']==0 and data['metrics']['counts']['unscored']==4
        second=claim_next(methods)
        finish(methods,second,execution_state='error',error_origin='runner_crash')
        # Controlled trusted elapsed fact exercises the trial cap without waiting 60 seconds.
        methods.store.connection.execute('UPDATE attempt_details SET elapsed_ns=? WHERE attempt_id=?',('60000000000',second['business_id']))
        retry=methods.evaluations.retry({'client_action_id':new_id('act'),'trial_id':second['trial_id'],'reason':'authorized infrastructure retry'})
        assert methods.evaluations.scheduler.claim(retry['work_item_id'],expected_version=0) is None
        terminal=methods.store.connection.execute('SELECT agent_outcome FROM attempt_details WHERE attempt_id=?',(retry['attempt_id'],)).fetchone()[0]
        assert terminal=='budget_exhausted'
        assert methods.store.connection.execute('SELECT COUNT(*) FROM trials').fetchone()[0]==4
    finally: methods.store.close()


@pytest.mark.parametrize('cancel',[False,True])
def test_actual_owned_process_worker_heartbeat_and_cancellation_settle_only_after_exit(tmp_path,cancel):
    methods,spec=make_evaluation(tmp_path)
    class SyntheticProcessExecutor:
        """Controlled process fixture, not an official benchmark adapter or OS sandbox."""
        def __init__(self):
            self.started=asyncio.Event();self.process=None
        def validate(self,spec,values):
            assert spec['dataset']['name']=='eval-mini-bundle' and spec['model_mode']=='scripted_mock'
            return []
        async def execute(self,work,scheduler):
            self.process=await asyncio.create_subprocess_exec(sys.executable,'-c',
                'import time;time.sleep(60)' if cancel else 'import time;time.sleep(5.2);print(42)',stdout=asyncio.subprocess.PIPE)
            self.started.set()
            try:
                output,_=await self.process.communicate()
                assert output.strip()==b'42' and self.process.returncode==0
                scheduler.finish(work['id'],owner_epoch=methods.store.epoch,expected_version=work['version'],
                    execution_state='finished',cleanup_state='clean')
            except asyncio.CancelledError:
                self.process.kill();await self.process.wait()
                scheduler.finish(work['id'],owner_epoch=methods.store.epoch,expected_version=work['version'],
                    execution_state='cancelled',cleanup_state='clean',agent_outcome='cancelled')
                raise
    async def exercise():
        executor=SyntheticProcessExecutor();methods.evaluations.executor=executor
        run,_=create(methods,spec);start(methods,run)
        identity=methods.store.connection.execute("SELECT business_id FROM work_items WHERE kind='attempt' ORDER BY rowid LIMIT 1").fetchone()[0]
        task=asyncio.create_task(methods.evaluations.scheduler.execute(identity))
        await executor.started.wait()
        if cancel:
            methods.evaluations.cancel({'client_action_id':new_id('act'),'run_id':run['run_id'],'reason':'controlled process cancel'})
        await task
        assert executor.process.returncode is not None
        result=methods.evaluations.report_data(run['run_id'])['attempts'][0]
        assert result['cleanup_state']=='clean'
        assert result['execution_state']==('cancelled' if cancel else 'finished')
        if not cancel:
            assert methods.store.connection.execute("SELECT 1 FROM events WHERE json_extract(body_json,'$.event_type')='attempt.heartbeat'").fetchone()
        assert methods.store.connection.execute('SELECT COUNT(*) FROM grades').fetchone()[0]==0
    try: asyncio.run(exercise())
    finally: methods.store.close()


def test_committed_mini_fixture_report_matches_planned_protocol_and_final_grades_are_immutable(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        run,_=create(methods,spec);start(methods,run)
        def claim_case(case):
            row=methods.store.connection.execute('SELECT w.id,w.version FROM work_items w JOIN attempts a ON a.id=w.business_id '
                'JOIN trials t ON t.id=a.trial_id WHERE t.run_id=? AND t.task_id=? ORDER BY a.attempt_no DESC LIMIT 1',(run['run_id'],case)).fetchone()
            return methods.evaluations.scheduler.claim(row['id'],expected_version=row['version'])
        for case,result in [('case-pass','pass'),('case-fail','fail')]:
            work=claim_case(case)
            grade=grade_fixture(methods,work,result)
            finish(methods,work,grade_id=grade,agent_outcome='completed')
            with pytest.raises(sqlite3.IntegrityError):
                methods.store.connection.execute("UPDATE grades SET result='pass' WHERE id=?",(grade,))
        first=claim_case('case-retry')
        finish(methods,first,execution_state='error',error_origin='runner_crash')
        methods.evaluations.retry({'client_action_id':new_id('act'),'trial_id':first['trial_id'],'reason':'explicit fixture infrastructure retry'})
        second=claim_case('case-retry')
        finish(methods,second,grade_id=grade_fixture(methods,second,'pass'),agent_outcome='completed')
        expected=json.loads(Path('tests/implementation/fixtures/eval-mini-bundle/input.json').read_text())['expected']
        data=methods.evaluations.report_data(run['run_id'])
        assert data['metrics']['counts']['planned']==expected['denominator']
        assert data['metrics']['counts']['passed']==expected['passed_cases']
        assert data['metrics']['counts']['failed']==expected['failed_cases']
        assert data['metrics']['counts']['unscored']==expected['unscored_cases']
        assert data['metrics']['planned_success_rate']['value']==expected['pass_rate']
        assert data['metrics']['grading_coverage']['value']=='0.75'
        assert data['metrics']['first_attempt_success_rate']['value']=='0.25'
        assert data['metrics']['trace_completeness']['value']=='0.8'
        first_report=methods.evaluations.report({'run_id':run['run_id']})
        second_report=methods.evaluations.report({'run_id':run['run_id']})
        assert first_report['report_artifact']['sha256']==second_report['report_artifact']['sha256']
    finally: methods.store.close()
