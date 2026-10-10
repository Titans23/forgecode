"""Real storage/RPC/bundles; offline Harness fixture and an independent grader process."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest
from benchmark.core.scheduler import emit
from forge.application.models import ContractError, validate
from forge.application.services import ApplicationServices
from forge.engine.methods import EngineMethods
from forge.engine.persistence import encoded,new_id
from forge.engine.test_profile import MemoryCredentials
from test_evaluations import make_evaluation,create,start,claim_next,finish,grade_fixture


def target(run,work):return {'run_id':run['run_id'],'attempt_id':work['business_id']}


def failed_case(methods,spec):
    run,_=create(methods,spec);start(methods,run);work=claim_next(methods)
    finish(methods,work,grade_id=grade_fixture(methods,work,'fail'))
    return run,work


def label(methods,value,**changes):
    current=methods.annotations.get(value)
    params={'client_action_id':new_id('act'),**value,'expected_head':current['annotation_head'],
        'author':'researcher','category':'unknown','note':'Insufficient evidence for a cause','evidence_refs':[],**changes}
    return methods.annotations.annotate(params),params


def test_manual_correction_keeps_author_time_versions_and_idempotency(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        run,work=failed_case(methods,spec);value=target(run,work)
        first,params=label(methods,value,category='tool_usage')
        assert methods.annotations.annotate(params)['reused_existing_action']
        second,_=label(methods,value,category='unknown',note='Earlier classification corrected')
        detail=methods.annotations.get(value);validate('failure.get.result',detail)
        assert [a['id'] for a in detail['annotations']]==[first['annotation_id'],second['annotation_id']]
        assert detail['annotations'][1]['supersedes']==first['annotation_id']
        assert all(a['author']=='researcher' and a['created_at'] for a in detail['annotations'])
        assert detail['annotations'][0]['category']=='tool_usage'
        assert detail['annotations'][1]['category']=='unknown'
        with pytest.raises(ContractError) as error:
            methods.annotations.annotate({**params,'client_action_id':new_id('act')})
        assert error.value.kind=='STALE_REVISION'
        assert not methods.annotations.get(value)['suggestions']
        assert len(methods.annotations.get(value)['annotations'])==2
    finally:methods.store.close()


def test_missing_evidence_saved_candidate_never_becomes_reproduced_or_writes_grade(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        run,work=failed_case(methods,spec);value=target(run,work)
        artifact=methods.store.publish_artifact(b'{"fixture":"evidence"}',origin='grader_adapter',classification='metadata',profile_id=methods.service.profile_id)
        with methods.store.transaction():methods.store.connection.execute('INSERT INTO artifact_attempts VALUES(?,?)',(artifact['id'],work['business_id']))
        label(methods,value,evidence_refs=[artifact['id']])
        (methods.store.data_dir/artifact['relative_storage_key']).unlink()
        params={'client_action_id':new_id('act'),**value,'expected_head':methods.annotations.get(value)['annotation_head']}
        candidate=methods.annotations.save_candidate(params)
        assert methods.annotations.save_candidate(params)['reused_existing_action']
        current=methods.annotations.get(value)['candidates'][0]
        assert current['status']=='blocked' and 'original_evidence_missing' in current['blockers']
        assert current['fixture']['available']
        record=json.loads(methods.store.read_artifact(current['fixture']['artifact_id']))
        assert record['status']=='saved_pending_reproduction' and record['evidence'][0]['available'] is False
        assert record['root_cause_authority']=='human_label_only'
        assert methods.store.connection.execute('SELECT COUNT(*) FROM grades').fetchone()[0]==1
        assert not methods.store.connection.execute('SELECT 1 FROM reproduction_checks').fetchone()
    finally:methods.store.close()


def test_failure_filters_cursor_profile_scope_and_foreign_evidence_are_enforced(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        run,first=failed_case(methods,spec);other=claim_next(methods);finish(methods,other,execution_state='blocked',error_origin='runner_unavailable')
        page=methods.annotations.list({'run_id':run['run_id'],'limit':1});validate('failure.list.result',page)
        assert page['next_cursor']
        second=methods.annotations.list({'run_id':run['run_id'],'limit':1,'cursor':page['next_cursor']})
        assert page['items'][0]['attempt_id']!=second['items'][0]['attempt_id']
        with pytest.raises(ContractError):methods.annotations.list({'run_id':run['run_id'],'category':'provider','cursor':page['next_cursor']})
        artifact=methods.store.publish_artifact(b'{}',origin='grader_adapter',classification='metadata',profile_id=methods.service.profile_id)
        with methods.store.transaction():methods.store.connection.execute('INSERT INTO artifact_attempts VALUES(?,?)',(artifact['id'],other['business_id']))
        with pytest.raises(ContractError):label(methods,target(run,first),evidence_refs=[artifact['id']])
        assert not methods.annotations.get(target(run,first))['annotations']
        warnings=methods.annotations.get(target(run,other))['suggestions']
        assert warnings[0]['authority']=='suggestion_only'
        other_profile=EngineMethods(ApplicationServices(methods.store,profile_id='other',credentials=MemoryCredentials()),profile='test')
        with pytest.raises(ContractError):other_profile.annotations.get(target(run,first))
    finally:methods.store.close()


def test_bundle_preserves_history_details_and_imported_human_overlay_cannot_gain_authority(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        run,work=failed_case(methods,spec);value=target(run,work)
        label(methods,value,category='verification');label(methods,value,note='Corrected human label')
        destination=tmp_path/'result.zip';scope={'kind':'run','id':run['run_id']}
        token=methods.artifacts.grant_destination(destination,scope=scope,classification='metadata_only')
        methods.artifacts.export({'client_action_id':new_id('act'),'scope':scope,'classification':'metadata_only','destination_token':token})
        token=methods.artifacts.grant_source(destination)
        imported=methods.artifacts.import_bundle({'client_action_id':new_id('act'),'source_token':token})
        external={'run_id':imported['run_ids'][0],'attempt_id':work['business_id']}
        before=methods.annotations.get(external)
        assert before['origin']=='imported_unverified' and len(before['annotations'])==2
        assert all(a['created_at'] and a['origin']=='imported_unverified' for a in before['annotations'])
        assert before['annotations'][-1]['note']=='Corrected human label'
        original=methods.evaluations.report_data(external['run_id'])
        label(methods,external,category='environment',note='Local human overlay')
        after=methods.annotations.get(external)
        assert after['annotations'][-1]['origin']=='local_human_overlay'
        assert methods.evaluations.report_data(external['run_id'])==original
        methods.annotations.save_candidate({'client_action_id':new_id('act'),**external,'expected_head':after['annotation_head']})
        assert 'unverified_origin' in methods.annotations.get(external)['candidates'][0]['blockers']
    finally:methods.store.close()


class ActualFailureExecutor:
    """Scripted offline model fixture runs actual Harness; grader reads frozen bytes in another process."""
    def __init__(self,methods,root):self.methods,self.root=methods,root
    def validate(self,spec,values):return []
    async def execute(self,work,scheduler):
        from forge.application.harness_adapter import HarnessAdapter,LocalTrustedBackend
        from forge.config import ForgeConfig
        from forge.engine.journal_projection import JournalProjector
        from forge.observability.events import Scope
        from test_application import ScriptedClient,script
        methods=self.methods;root=self.root/work['business_id'];root.mkdir();(root/'value.txt').write_text('B')
        def factory(config,**kwargs):
            client=ScriptedClient(script());client.provider='scripted_mock';client.model='fixture-model-v1';return client
        adapter=HarnessAdapter(root,config=ForgeConfig(api_key='offline-unused',model_id='fixture-model-v1'),data_root=methods.store.data_dir/'harness',
            backend=LocalTrustedBackend(),budget={'max_model_calls':10,'max_tool_calls':20,'wall_seconds':30},model_client_factory=factory,task_relation='new')
        adapter.journal.observation_scope=Scope(trace_id=work['trace_id'],parent_span_id=work['span_id'],identities={'run_id':work['run_id'],'trial_id':work['trial_id'],'attempt_id':work['business_id']})
        try:
            async for _ in adapter.stream([{'text':'Read value.txt and report its value.'}]):pass
        finally:await adapter.close()
        JournalProjector(methods.store).project_attempt(adapter.journal.path,work['business_id'])
        frozen=(root/'value.txt').read_bytes();path=self.root/(work['business_id']+'.frozen');path.write_bytes(frozen)
        # Real intentionally failing assertion: source fixture B does not equal expected A.
        outcome=subprocess.run([sys.executable,'-c','import pathlib,sys;sys.exit(0 if pathlib.Path(sys.argv[1]).read_bytes()==b"A" else 1)',str(path)],cwd=self.root,capture_output=True,timeout=10)
        assert outcome.returncode==1
        artifact=methods.store.publish_artifact(frozen,origin='grader_adapter',classification='private-runner',profile_id=methods.service.profile_id)
        grade=new_id('grade');grader_hash=sha256(b'independent-byte-assertion-A-v1').hexdigest()
        with methods.store.transaction():
            methods.store.connection.execute('INSERT INTO artifact_attempts VALUES(?,?)',(artifact['id'],work['business_id']))
            methods.store.connection.execute('INSERT INTO grades VALUES(?,?,?,?,?,?,?)',(grade,work['business_id'],grader_hash,artifact['sha256'],'graded','0','fail'))
            emit(methods.store,'grade.finished',{'attempt_id':work['business_id'],'grader_hash':grader_hash,'artifact_hash':artifact['sha256'],
                'raw_reward_decimal':'0','grade_state':'graded','grade_result':'fail'},run_id=work['run_id'],trial_id=work['trial_id'],attempt_id=work['business_id'],trace_id=work['trace_id'],span_id=work['span_id'])
        scheduler.finish(work['id'],owner_epoch=methods.store.epoch,expected_version=work['version'],execution_state='finished',cleanup_state='clean',agent_outcome='completed',grade_id=grade)


def execute_one(methods,spec):
    spec=deepcopy(spec);spec['dataset']['task_ids']=['case-fail'];spec['dataset']['task_revisions']={'case-fail':'v1'}
    run,_=create(methods,spec);start(methods,run)
    attempt=methods.store.connection.execute('SELECT selected_attempt_id FROM trials WHERE run_id=?',(run['run_id'],)).fetchone()[0]
    asyncio.run(methods.evaluations.scheduler.execute(attempt))
    return {'run_id':run['run_id'],'attempt_id':attempt}


def test_only_actual_new_harness_and_independent_grader_failure_can_reproduce_candidate(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        methods.evaluations.executor=ActualFailureExecutor(methods,tmp_path)
        original=execute_one(methods,spec)
        candidate=methods.annotations.save_candidate({'client_action_id':new_id('act'),**original,'expected_head':None})
        saved=methods.annotations.get(original)['candidates'][0]
        assert saved['status']=='saved_pending_reproduction' and not saved['blockers']
        rejected=methods.annotations.check_reproduction({'client_action_id':new_id('act'),'candidate_id':candidate['candidate_id'],**original})
        assert rejected['observed_status']=='blocked' and 'same_execution' in rejected['blockers']
        reproduction=execute_one(methods,spec)
        params={'client_action_id':new_id('act'),'candidate_id':candidate['candidate_id'],**reproduction}
        verified=methods.annotations.check_reproduction(params)
        assert verified['observed_status']=='reproduced' and not verified['blockers']
        assert methods.annotations.check_reproduction(params)['reused_existing_action']
        actual=methods.annotations.get(original)['candidates'][0]
        assert actual['status']=='reproduced' and actual['reproduction_attempt_id']==reproduction['attempt_id']
        assert 'root cause is not proven' in actual['authority']
        changed=deepcopy(spec);changed['protocol']['max_infrastructure_attempts']=1
        different=execute_one(methods,changed)
        mismatch=methods.annotations.check_reproduction({'client_action_id':new_id('act'),'candidate_id':candidate['candidate_id'],**different})
        assert 'different_frozen_conditions' in mismatch['blockers']
        # Re-select the genuine matching execution; a failed comparison never changes grades.
        methods.annotations.check_reproduction({**params,'client_action_id':new_id('act')})
        detail=methods.annotations.get(reproduction)
        path=methods.store.connection.execute('SELECT relative_storage_key FROM artifacts WHERE id=?',(detail['evidence'][0]['artifact_id'],)).fetchone()[0]
        (methods.store.data_dir/path).unlink()
        assert methods.annotations.get(original)['candidates'][0]['status']=='blocked'
        assert methods.store.connection.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0]==6
    finally:methods.store.close()

def test_notes_and_candidates_are_redacted_before_persistence(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    try:
        run,work=failed_case(methods,spec);value=target(run,work)
        methods.store.observation_secrets=('private-fixture-key',)
        label(methods,value,note='api_key=private-fixture-key; Bearer also-private')
        detail=methods.annotations.get(value)
        assert 'private-fixture-key' not in encoded(detail)
        assert 'also-private' not in encoded(detail)
        methods.annotations.save_candidate({'client_action_id':new_id('act'),**value,'expected_head':detail['annotation_head']})
        content=methods.store.read_artifact(methods.annotations.get(value)['candidates'][0]['fixture']['artifact_id'])
        assert b'private-fixture-key' not in content and b'also-private' not in content
        assert b'[REDACTED]' in content
    finally:methods.store.close()


def test_renderer_cannot_declare_reproducible_or_supply_raw_fixture_paths(tmp_path):
    from forge.engine.rpc import RpcServer
    methods,spec=make_evaluation(tmp_path)
    try:
        run,work=failed_case(methods,spec);value=target(run,work)
        methods.initialized=True;server=RpcServer(methods,principal='renderer')
        valid={'client_action_id':new_id('act'),**value,'expected_head':None}
        for extra in ({'reproducible':True},{'fixture_path':str(tmp_path/'untrusted.py')}):
            response=asyncio.run(server.handle_frame(encoded({'jsonrpc':'2.0','id':'forged','method':'failure.save_candidate','params':{**valid,**extra}}).encode()))
            assert response['error']['code']==-32602
        assert not methods.store.connection.execute('SELECT 1 FROM regression_candidates').fetchone()
        response=asyncio.run(server.handle_frame(encoded({'jsonrpc':'2.0','id':'read','method':'failure.get','params':value}).encode()))
        assert response['result']['attempt']['grade_result']=='fail'
        assert not response['result']['candidates']
    finally:methods.store.close()

@pytest.mark.parametrize('damage',['cycle','unknown_field','unknown_annotation','invalid_time'])
def test_untrusted_annotation_history_extension_is_validated_before_import(tmp_path,damage):
    from benchmark.core.bundle import write_bundle
    methods,spec=make_evaluation(tmp_path)
    try:
        run,work=failed_case(methods,spec);value=target(run,work)
        label(methods,value);label(methods,value,note='revision two')
        files,tasks=methods.artifacts._freeze({'kind':'run','id':run['run_id']},'metadata_only')
        if damage=='cycle':
            rows=[json.loads(line) for line in files['annotations.jsonl'].splitlines()]
            rows[-1]['supersedes']=rows[-1]['id']
            files['annotations.jsonl']=b''.join(encoded(row).encode()+b'\n' for row in rows)
        else:
            rows=[json.loads(line) for line in files['annotation_details.jsonl'].splitlines()]
            if damage=='unknown_field':rows[0]['execute']='untrusted script'
            elif damage=='unknown_annotation':rows[0]['annotation_id']=new_id('annotation')
            else:rows[0]['created_at']='not a timestamp'
            files['annotation_details.jsonl']=b''.join(encoded(row).encode()+b'\n' for row in rows)
        path=tmp_path/'malformed.zip';write_bundle(path,files,task_ids=tasks)
        grant=methods.artifacts.grant_source(path)
        with pytest.raises(ContractError):methods.artifacts.import_bundle({'client_action_id':new_id('act'),'source_token':grant})
        assert not methods.store.connection.execute('SELECT 1 FROM imported_runs').fetchone()
        assert len(methods.annotations.get(value)['annotations'])==2
    finally:methods.store.close()


def test_legacy_bundle_without_annotation_details_retains_unknown_historical_time(tmp_path):
    from benchmark.core.bundle import write_bundle
    methods,spec=make_evaluation(tmp_path)
    try:
        run,work=failed_case(methods,spec);label(methods,target(run,work))
        files,tasks=methods.artifacts._freeze({'kind':'run','id':run['run_id']},'metadata_only')
        del files['annotation_details.jsonl']
        path=tmp_path/'legacy.zip';write_bundle(path,files,task_ids=tasks)
        grant=methods.artifacts.grant_source(path)
        imported=methods.artifacts.import_bundle({'client_action_id':new_id('act'),'source_token':grant})
        external={'run_id':imported['run_ids'][0],'attempt_id':work['business_id']}
        assert methods.annotations.get(external)['annotations'][0]['created_at'] is None
    finally:methods.store.close()
