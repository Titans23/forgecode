"""Actual profile storage, immutable drafts, bounded reports and file selections."""
import asyncio
from copy import deepcopy
import inspect
import json
from pathlib import Path
import pytest

from benchmark.adapters.harbor import HarborExecutor, export_runspec
from benchmark.core.spec import freeze_spec
from forge.application.models import ContractError, canonical_hash, validate
from forge.application.services import ApplicationServices
from forge.engine.methods import EngineMethods
from forge.engine.persistence import encoded, new_id
from forge.engine.test_profile import MemoryCredentials
from forge.engine.rpc import RpcServer
from test_evaluations import make_evaluation, create, start, claim_next, finish, grade_fixture


def draft(methods, run, **changes):
    template=methods.evaluation_client.template({'template_id':run['run_id']})
    choices={**template['defaults'],**changes}
    return methods.evaluation_client.draft({'client_action_id':new_id('act'),'template_id':run['run_id'],'choices':choices})


def test_wizard_configurations_freeze_new_runs_without_mutating_original_or_grading(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        original,_=create(methods,spec)
        a=draft(methods,original,max_delivery_repairs=0)
        b=draft(methods,original,max_delivery_repairs=2)
        assert a['spec_hash']!=b['spec_hash']
        ra,_=create(methods,a['spec']);rb,_=create(methods,b['spec'])
        assert len({original['run_id'],ra['run_id'],rb['run_id']})==3
        assert methods.evaluations.run(original['run_id'])['spec_json']==encoded(spec)
        assert a['spec']['budget']==b['spec']['budget']
        snapshot=methods.evaluation_client.snapshot({'run_id':rb['run_id'],'limit':2})
        validate('evaluation.snapshot.result',snapshot)
        assert snapshot['metrics']['counts']['planned']==4 and len(snapshot['trials'])==2
        assert snapshot['metrics']['counts']['unscored']==4 and not snapshot['attempts']
        assert snapshot['metrics']['cost_per_success']['status']=='N/A'
        assert methods.store.connection.execute('SELECT COUNT(*) FROM grades').fetchone()[0]==0
    finally:methods.store.close()


def test_windows_incompatible_plan_keeps_real_export_entry_and_unknown_capability(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        original,_=create(methods,spec)
        value=draft(methods,original,target_platform='windows-native')
        frozen,values,digest=freeze_spec(methods.store,value['spec'])
        assert values['environment']['platform']=='windows-native'
        assert values['environment']['backend_version']=='unavailable'
        assert values['capabilities']['readiness']=='unavailable'
        accepted,_=create(methods,frozen)
        assert not methods.evaluations.validate({'spec':frozen})['compatible']
        exported=methods.evaluation_client.export_plan({'run_id':accepted['run_id']})
        plan=json.loads(methods.store.read_artifact(exported['plan_artifact']['artifact_id']))
        assert plan['spec_hash']==digest and plan['execution_label']=='windows-native'
        assert plan['read_only_plan'] and 'required_environment' in plan
        assert methods.store.connection.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0]==0
    finally:methods.store.close()


def test_imported_configuration_is_profile_bound_and_cannot_authorize_executor(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        _,values,_=freeze_spec(methods.store,spec)
        source=tmp_path/'configuration.json';source.write_text(encoded(export_runspec(spec,values)),encoding='utf-8')
        # A different source hash must not inherit the trusted fixture's identity.
        document=json.loads(source.read_text());document['resolved_snapshots']['source']['origin']='external-claim'
        reference=document['spec']['source']['source_snapshot'];reference['sha256']=canonical_hash(document['resolved_snapshots']['source'])
        document['spec_hash']=canonical_hash(document['spec']);source.write_text(encoded(document),encoding='utf-8')
        imported=methods.evaluation_client.import_plan({'client_action_id':new_id('act'),'path':str(source)})
        template=methods.evaluation_client.template({'template_id':imported['template_id']})
        value=methods.evaluation_client.draft({'client_action_id':new_id('act'),'template_id':imported['template_id'],'choices':template['defaults']})
        assert value['configuration_origin']=='imported_unverified'
        validation=methods.evaluations.validate({'spec':value['spec']})
        assert not validation['compatible'] and any(i['kind']=='unverified_configuration' for i in validation['issues'])
        other=EngineMethods(ApplicationServices(methods.store,profile_id='other',credentials=MemoryCredentials()),profile='test')
        with pytest.raises(ContractError):other.evaluation_client.template({'template_id':imported['template_id']})
        changed=deepcopy(value['spec']);changed['experiment_id']=new_id('experiment')
        assert any(i['kind']=='unverified_configuration' for i in methods.evaluations.validate({'spec':changed})['issues'])
        class NeverCalledExecutor:
            called=False
            def validate(self,spec,values):return []
            async def execute(self,work,scheduler):self.called=True
        executor=NeverCalledExecutor();methods.evaluations.executor=executor
        run,_=create(methods,changed);start(methods,run)
        attempt=methods.store.connection.execute('SELECT selected_attempt_id FROM trials WHERE run_id=?',(run['run_id'],)).fetchone()[0]
        asyncio.run(methods.evaluations.scheduler.execute(attempt))
        actual=next(a for a in methods.evaluation_client.snapshot({'run_id':run['run_id']})['attempts'] if a['id']==attempt)
        assert not executor.called and actual['execution_state']=='blocked'
    finally:methods.store.close()


def test_snapshot_retains_first_pass_and_last_authorized_retry_and_missing_artifact(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        run,_=create(methods,spec);start(methods,run);first=claim_next(methods)
        finish(methods,first,grade_id=grade_fixture(methods,first,'pass'),execution_state='error',error_origin='runner_crash')
        retry=methods.evaluations.retry({'client_action_id':new_id('act'),'trial_id':first['trial_id'],'reason':'declared infrastructure retry'})
        second=methods.evaluations.scheduler.claim(retry['work_item_id'],expected_version=0)
        finish(methods,second,grade_id=grade_fixture(methods,second,'fail'))
        artifact=methods.store.publish_artifact(b'{"real":"grader evidence"}',origin='grader_adapter',classification='metadata',profile_id=methods.service.profile_id)
        with methods.store.transaction():methods.store.connection.execute('INSERT INTO artifact_attempts VALUES(?,?)',(artifact['id'],second['business_id']))
        before=methods.evaluation_client.snapshot({'run_id':run['run_id']})
        assert before['metrics']['first_attempt_success_rate']['value']=='0.25'
        assert before['metrics']['planned_success_rate']['value']=='0'
        assert before['trials'][0]['selected_attempt_id']==second['business_id']
        assert [x['attempt_no'] for x in before['attempts'] if x['trial_id']==first['trial_id']]==[1,2]
        (methods.store.data_dir/artifact['relative_storage_key']).unlink()
        after=methods.evaluation_client.snapshot({'run_id':run['run_id']})
        assert artifact['id'] in after['missing_evidence'] and after['missing_evidence_count']==1
        assert after['metrics']==before['metrics']
    finally:methods.store.close()


def test_signed_paging_and_comparison_never_cross_run_profile_or_task_filter(tmp_path):
    methods,spec=make_evaluation(tmp_path,repeats=3)
    try:
        run,_=create(methods,spec);other,_=create(methods,spec)
        first=methods.evaluation_client.snapshot({'run_id':run['run_id'],'limit':2})
        second=methods.evaluation_client.snapshot({'run_id':run['run_id'],'limit':2,'cursor':first['next_cursor']})
        assert not {t['id'] for t in first['trials']} & {t['id'] for t in second['trials']}
        for changed in ({'run_id':other['run_id']},{'task_id':'case-pass'}):
            with pytest.raises(ContractError):methods.evaluation_client.snapshot({'run_id':run['run_id'],'cursor':first['next_cursor'],**changed})
        changed=draft(methods,run,max_model_requests_per_attempt=9);different,_=create(methods,changed['spec'])
        comparison=methods.evaluation_client.comparison({'run_ids':[run['run_id'],different['run_id']],'protocol':'paired_task_set'})
        assert not comparison['comparable'] and any('budget' in x for x in comparison['differences'])
        other_profile=EngineMethods(ApplicationServices(methods.store,profile_id='other',credentials=MemoryCredentials()),profile='test')
        assert not other_profile.evaluation_client.list_runs({})['items']
        with pytest.raises(ContractError):other_profile.evaluation_client.snapshot({'run_id':run['run_id']})
    finally:methods.store.close()


def test_harbor_executor_implements_actual_scheduler_interface():
    assert list(inspect.signature(HarborExecutor.execute).parameters)==['self','work','scheduler']


def test_old_model_connection_revision_can_be_exported_without_becoming_runnable(tmp_path):
    from forge.config import ForgeConfig
    methods,spec=make_evaluation(tmp_path)
    try:
        connection=methods.service.put_connection(ForgeConfig(api_key='not-a-real-key',provider='openai_responses',model_id='fixture-provider-model'))
        spec['model'].update(connection_id=connection,connection_revision=1,provider='openai_responses',requested_model='fixture-provider-model');spec['model_mode']='live'
        run,_=create(methods,spec)
        methods.service.put_connection(ForgeConfig(api_key='not-a-real-key',provider='openai_responses',model_id='new-fixture-provider-model'),connection_id=connection)
        exported=methods.evaluation_client.export_plan({'run_id':run['run_id']})
        document=json.loads(methods.store.read_artifact(exported['plan_artifact']['artifact_id']))
        assert document['spec']['model']['connection_revision']==1 and document['spec']['model']['requested_model']=='fixture-provider-model'
        snapshot=methods.evaluation_client.snapshot({'run_id':run['run_id']})
        assert not snapshot['compatible'] and snapshot['issues'][0]['kind']=='STALE_REVISION'
        assert not methods.store.connection.execute('SELECT 1 FROM model_requests').fetchone()
    finally:methods.store.close()


def test_source_hash_index_is_metadata_even_when_a_filename_mentions_secrets(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        _,values,_=freeze_spec(methods.store,spec);values['source']['files']={'docs/secret-handling.py':'e'*64}
        spec['source']['source_snapshot']['sha256']=canonical_hash(values['source'])
        source=tmp_path/'hash-index.json';source.write_text(encoded(export_runspec(spec,values)),encoding='utf-8')
        imported=methods.evaluation_client.import_plan({'client_action_id':new_id('act'),'path':str(source)})
        assert imported['origin']=='imported_unverified'
        assert methods.evaluation_client.template({'template_id':imported['template_id']})['configuration_origin']=='imported_unverified'
    finally:methods.store.close()


def test_imported_origin_does_not_short_circuit_remaining_foreign_snapshot_checks(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        _,values,_=freeze_spec(methods.store,spec);values['source']['origin']='external-source'
        spec['source']['source_snapshot']['sha256']=canonical_hash(values['source'])
        source=tmp_path/'external-plan.json';source.write_text(encoded(export_runspec(spec,values)),encoding='utf-8')
        imported=methods.evaluation_client.import_plan({'client_action_id':new_id('act'),'path':str(source)})
        template=methods.evaluation_client.template({'template_id':imported['template_id']})
        value=methods.evaluation_client.draft({'client_action_id':new_id('act'),'template_id':imported['template_id'],'choices':template['defaults']})
        foreign_parameters={**values['model_parameters'],'max_output_tokens':1025}
        with methods.store.transaction():
            foreign=methods.store._configuration_snapshot(foreign_parameters,canonical_hash(foreign_parameters))
            methods.store.connection.execute('INSERT INTO evaluation_snapshot_origins VALUES(?,?,?)',(foreign['snapshot_id'],'other-profile','trusted_configuration'))
        changed=deepcopy(value['spec']);changed['model']['parameters']=foreign
        with pytest.raises(ContractError) as error:methods.evaluations.validate({'spec':changed})
        assert error.value.kind=='NOT_FOUND'
        assert not methods.store.connection.execute('SELECT 1 FROM runs').fetchone()
    finally:methods.store.close()


def test_renderer_cannot_import_configuration_paths_or_export_private_plans(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        run,_=create(methods,spec);methods.initialized=True;server=RpcServer(methods,principal='renderer')
        for method,params in [('evaluation.import_plan',{'client_action_id':new_id('act'),'path':str(tmp_path/'any.json')}),
                ('evaluation.plan_export',{'run_id':run['run_id']})]:
            response=asyncio.run(server.handle_frame(encoded({'jsonrpc':'2.0','id':method,'method':method,'params':params}).encode()))
            assert response['error']['data']['kind']=='UNAUTHORIZED'
        assert not methods.store.connection.execute('SELECT 1 FROM evaluation_templates').fetchone()
    finally:methods.store.close()


def test_actual_harness_scheduler_and_separate_process_grader_flow_through_client(tmp_path):
    from hashlib import sha256
    import subprocess
    import sys
    from benchmark.core.scheduler import emit
    from forge.application.harness_adapter import HarnessAdapter,LocalTrustedBackend
    from forge.config import ForgeConfig
    from forge.engine.journal_projection import JournalProjector
    from forge.observability.events import Scope
    from test_application import ScriptedClient,script
    methods,spec=make_evaluation(tmp_path)
    class ActualFixtureExecutor:
        """Offline model fixture drives real Harness; grading uses an actual separate process."""
        def validate(self,spec,values):
            return [] if spec['model_mode']=='scripted_mock' and spec['dataset']['name']=='eval-mini-bundle' else [{'task_id':None,'kind':'fixture_only','message':'No public model score'}]
        async def execute(self,work,scheduler):
            root=tmp_path/work['business_id'];root.mkdir();(root/'value.txt').write_text('B')
            def factory(config,**kwargs):
                client=ScriptedClient(script());client.provider='scripted_mock';client.model='fixture-model-v1';return client
            adapter=HarnessAdapter(root,config=ForgeConfig(api_key='unused-fixture',model_id='fixture-model-v1'),data_root=methods.store.data_dir/'harness',
                backend=LocalTrustedBackend(),budget={'max_model_calls':10,'max_tool_calls':20,'wall_seconds':30},model_client_factory=factory,task_relation='new')
            adapter.journal.observation_scope=Scope(trace_id=work['trace_id'],parent_span_id=work['span_id'],identities={'run_id':work['run_id'],'trial_id':work['trial_id'],'attempt_id':work['business_id']})
            try:
                async for _ in adapter.stream([{'text':'Read value.txt and report its value.'}]):pass
            finally:await adapter.close()
            JournalProjector(methods.store).project_attempt(adapter.journal.path,work['business_id'])
            # The grader process is outside the Agent project and runs after the artifact is frozen.
            frozen=(root/'value.txt').read_bytes();candidate=tmp_path/(work['business_id']+'.frozen');candidate.write_bytes(frozen)
            graded=subprocess.run([sys.executable,'-c','import pathlib,sys;sys.exit(0 if pathlib.Path(sys.argv[1]).read_bytes()==b"B" else 1)',str(candidate)],cwd=tmp_path,capture_output=True,timeout=10)
            result='pass' if graded.returncode==0 else 'fail';reward='1' if result=='pass' else '0'
            artifact=methods.store.publish_artifact(frozen,origin='grader_adapter',classification='private-runner',profile_id=methods.service.profile_id)
            grade=new_id('grade');grader_hash=sha256(b'offline-independent-byte-assertion-v1').hexdigest()
            with methods.store.transaction():
                methods.store.connection.execute('INSERT INTO artifact_attempts VALUES(?,?)',(artifact['id'],work['business_id']))
                methods.store.connection.execute('INSERT INTO grades VALUES(?,?,?,?,?,?,?)',(grade,work['business_id'],grader_hash,artifact['sha256'],'graded',reward,result))
                emit(methods.store,'grade.finished',{'attempt_id':work['business_id'],'grader_hash':grader_hash,'artifact_hash':artifact['sha256'],
                    'raw_reward_decimal':reward,'grade_state':'graded','grade_result':result},run_id=work['run_id'],trial_id=work['trial_id'],attempt_id=work['business_id'],trace_id=work['trace_id'],span_id=work['span_id'])
            scheduler.finish(work['id'],owner_epoch=methods.store.epoch,expected_version=work['version'],execution_state='finished',cleanup_state='clean',agent_outcome='completed',grade_id=grade)
    try:
        methods.evaluations.executor=ActualFixtureExecutor()
        base,_=create(methods,spec);value=draft(methods,base,task_ids=['case-pass'])
        run,_=create(methods,value['spec']);start(methods,run)
        attempt=methods.store.connection.execute('SELECT selected_attempt_id FROM trials WHERE run_id=?',(run['run_id'],)).fetchone()[0]
        asyncio.run(methods.evaluations.scheduler.execute(attempt))
        snapshot=methods.evaluation_client.snapshot({'run_id':run['run_id']})
        assert snapshot['state']=='completed' and snapshot['metrics']['counts']['passed']==1
        assert snapshot['metrics']['request_count']==2 and snapshot['metrics']['grading_coverage']['value']=='1'
        traces=methods.observations.spans({'scope':{'kind':'run','id':run['run_id']},'attempt_id':attempt})
        actual_trace=methods.store.connection.execute('SELECT trace_id FROM attempt_details WHERE attempt_id=?',(attempt,)).fetchone()[0]
        assert traces['items'] and all(s['trace_id']==actual_trace for s in traces['items'])
        assert methods.store.connection.execute('SELECT COUNT(*) FROM grades').fetchone()[0]==1
    finally:methods.store.close()


@pytest.mark.parametrize('damage',['hash','extra','secret'])
def test_configuration_import_rejects_invalid_claim_without_partial_snapshot_publication(tmp_path,damage):
    methods,spec=make_evaluation(tmp_path)
    try:
        _,values,_=freeze_spec(methods.store,spec);document=export_runspec(spec,values)
        if damage=='hash':document['spec_hash']='0'*64
        elif damage=='extra':document['execute']='arbitrary code'
        else:document['resolved_snapshots']['source']['api_key']='unexpected-secret'
        source=tmp_path/'bad.json';source.write_text(encoded(document),encoding='utf-8')
        count=methods.store.connection.execute('SELECT COUNT(*) FROM configuration_snapshots').fetchone()[0]
        with pytest.raises(ContractError):methods.evaluation_client.import_plan({'client_action_id':new_id('act'),'path':str(source)})
        assert methods.store.connection.execute('SELECT COUNT(*) FROM configuration_snapshots').fetchone()[0]==count
        assert not methods.evaluation_client.templates({})['items']
    finally:methods.store.close()
