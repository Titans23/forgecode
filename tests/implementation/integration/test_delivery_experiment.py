"""Real SQLite/ZIP pairing; fixture grades explicitly do not establish model quality."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from benchmark.adapters.harbor import export_runspec
from benchmark.core.repair_comparison import compare_repairs, interleaved_schedule, paired_report, report_from_bundle
from benchmark.core.spec import resolve_snapshot
from forge.application.evaluation_client import REFERENCES
from forge.application.models import canonical_hash
from forge.engine.persistence import new_id
from scripts.delivery_experiment import check_configuration, verify
from tests.implementation.integration.test_bundles import mini_run
from tests.implementation.integration.test_evaluations import create, start, claim_next, grade_fixture, finish, make_evaluation


def configurations(methods, a):
    values={name:resolve_snapshot(methods.store,a[group][key],schema)
        for name,(group,key,schema) in REFERENCES.items()}
    b,b_values=deepcopy(a),deepcopy(values)
    b['harness']['max_delivery_repairs']=2
    b_values['harness']['max_delivery_repairs']=2
    with methods.store.transaction():
        b['harness']['configuration']=methods.store._configuration_snapshot(b_values['harness'],canonical_hash(b_values['harness']))
    return values,b,b_values


def test_only_repairs_may_differ_and_frozen_snapshots_are_checked(tmp_path):
    methods,a=make_evaluation(tmp_path)
    try:
        av,b,bv=configurations(methods,a)
        assert compare_repairs(a,b,av,bv)['comparable']
        changed=deepcopy(b);changed['budget']['max_model_requests_per_attempt']+=1
        assert not compare_repairs(a,changed,av,bv)['comparable']
        bad=deepcopy(bv);bad['model_parameters']['max_output_tokens']+=1
        with pytest.raises(ValueError,match='frozen hash'):compare_repairs(a,b,av,bad)
        changed=deepcopy(b);changed['protocol']['feedback']='benchmark_defined'
        assert not compare_repairs(a,changed,av,bv)['comparable']
    finally:methods.store.close()


def test_interleaving_is_reproducible_single_worker_and_balanced():
    schedule=interleaved_schedule(['one','two','three'],2)
    assert schedule==interleaved_schedule(['one','two','three'],2) and len(schedule)==12
    assert {(r['task_id'],r['repeat_index'],r['group']) for r in schedule}=={
        (task,repeat,group) for task in ('one','two','three') for repeat in range(2) for group in ('A','B')}
    for i in range(0,len(schedule),2):
        assert schedule[i]['task_id']==schedule[i+1]['task_id'] and schedule[i]['group']!=schedule[i+1]['group']
    with pytest.raises(ValueError):interleaved_schedule(['same','same'],1)


def test_actual_bundle_recompute_preserves_unknown_cost_missing_grade_and_all_attempts(tmp_path):
    methods,arun=mini_run(tmp_path)
    try:
        a=json.loads(methods.evaluations.run(arun['run_id'])['spec_json'])
        av,b,bv=configurations(methods,a)
        brun,_=create(methods,b);start(methods,brun)
        for result in ('fail','pass','fail','pass'):
            work=claim_next(methods)
            finish(methods,work,agent_outcome='completed',grade_id=grade_fixture(methods,work,result))
        scope={'kind':'all'};path=tmp_path/'pair.zip'
        token=methods.artifacts.grant_destination(path,scope=scope,classification='metadata_only')
        methods.artifacts.export({'client_action_id':new_id('act'),'scope':scope,
            'destination_token':token,'classification':'metadata_only'})
        ap,bp=export_runspec(a,av),export_runspec(b,bv)
        before=methods.store.connection.total_changes
        first=report_from_bundle(path,arun['run_id'],brun['run_id'],ap,bp)
        assert first==report_from_bundle(path,arun['run_id'],brun['run_id'],ap,bp)
        assert methods.store.connection.total_changes==before
        report=first['result']
        assert first['origin']=='imported_unverified' and report['planned_pairs']==4
        assert report['A_metrics']['counts']['attempts']==5 and report['B_metrics']['counts']['attempts']==4
        assert len(report['attempts']['A'])==5 and len(report['attempts']['B'])==4
        assert len(report['requests']['A'])==report['A_metrics']['request_count']
        assert any(request['cost'] is None for request in report['requests']['A'])
        assert report['A_metrics']['cost_per_success']['status']=='unknown' and report['interval'] is None
        changed=deepcopy(bp);changed['spec_hash']='0'*64
        with pytest.raises(ValueError,match='plan hash'):report_from_bundle(path,arun['run_id'],brun['run_id'],ap,changed)
    finally:methods.store.close()


def fixture_report(outcomes, repeats=1):
    trials=[];attempts=[]
    for index,result in enumerate(outcomes):
        task='task-'+str(index//repeats);trial='trial-'+str(index)
        identity='attempt-'+str(index)
        trials.append({'id':trial,'task_id':task,'repeat_index':index%repeats,'selected_attempt_id':identity})
        attempts.append({'id':identity,'trial_id':trial,'attempt_no':1,'grade_state':'graded' if result else 'unscored','grade_result':result})
    return {'trials':trials,'attempts':attempts,'metrics':{},'missing_evidence':[],'score_authority':'synthetic_fixture'}


def test_cluster_interval_is_deterministic_and_missing_grades_do_not_create_interval():
    a,b=fixture_report(['fail','fail','pass','pass'],2),fixture_report(['pass','pass','pass','pass'],2)
    result=paired_report(a,b,{'comparable':True},samples=200)
    assert result==paired_report(a,b,{'comparable':True},samples=200)
    assert result['task_clusters']==2 and result['planned_pairs']==4
    assert result['confirmed_pass_difference']=='0.5'
    assert result['interval']['lower']=='0' and result['interval']['upper']=='1'
    b['attempts'][0]['grade_state']='unscored';b['attempts'][0]['grade_result']=None
    assert paired_report(a,b,{'comparable':True})['interval'] is None


def test_best_of_and_changed_planned_denominator_are_rejected():
    a,b=fixture_report(['pass','fail']),fixture_report(['pass','pass'])
    a['attempts'].append({**a['attempts'][0],'id':'later','attempt_no':2,'grade_result':'fail'})
    with pytest.raises(ValueError,match='last authorized'):paired_report(a,b,{'comparable':True})
    a['trials'][0]['selected_attempt_id']='later'
    assert paired_report(a,b,{'comparable':True})['confirmed_pass_difference']=='1'
    b['trials'].pop()
    with pytest.raises(ValueError,match='denominators'):paired_report(a,b,{'comparable':True})


def test_preregistration_rejects_outer_retry_unbalanced_budget_and_inspected_holdout():
    config=json.loads(Path('experiments/delivery-repair.json').read_text(encoding='utf-8'))
    assert len(check_configuration(config))==6
    for key,value in (('max_infrastructure_attempts',2),('max_model_requests_per_attempt',0),('trial_wall_seconds',1800)):
        bad=deepcopy(config);bad['budget'][key]=value
        with pytest.raises(ValueError):check_configuration(bad)
    bad=deepcopy(config);bad['formal']['selected_shape']=bad['formal']['allowed_shapes'][0]
    bad['formal']['task_ids']=bad['preexperiment']['task_ids']+[str(i) for i in range(9)]
    with pytest.raises(ValueError):check_configuration(bad)
    bad=deepcopy(config);task=bad['preexperiment']['task_ids'][0]
    bad['preexperiment']['tasks'][task]['content_sha256']='0'*64
    with pytest.raises(ValueError,match='Official task reference'):check_configuration(bad)
    bad=deepcopy(config);bad['preexperiment']['taskset_evidence']['canonical_sha256']='0'*64
    with pytest.raises(ValueError,match='Official task reference'):check_configuration(bad)


def test_official_command_preserves_blocked_environment_and_zero_calls(tmp_path):
    output=tmp_path/'actual-readiness.json'
    process=subprocess.run([sys.executable,'scripts/delivery_experiment.py','run','--output',str(output)],
        capture_output=True,text=True,encoding='utf-8',timeout=60)
    result=json.loads(output.read_text(encoding='utf-8'))
    assert process.returncode==2 and result['status']=='blocked'
    assert result['public_model_calls']==0 and result['grades']==0 and len(result['schedule'])==6
    assert 'per-request accounting' in result['reason']
    assert result['authorization']['authorized'] is True


def test_preregistration_flags_cannot_self_authorize_paid_execution(tmp_path):
    value=json.loads(Path('experiments/delivery-repair.json').read_text(encoding='utf-8'))
    value['authorization']={name:True for name in value['authorization']}
    path=tmp_path/'self-asserted-authorization.json';path.write_text(json.dumps(value),encoding='utf-8')
    result=verify(path)
    assert result['status']=='blocked' and result['public_model_calls']==result['grades']==0
    assert 'explicit human authorization' in result['reason'] and 'per-request accounting' in result['reason']

def test_money_policy_must_match_recorded_unbounded_human_choice():
    from scripts.delivery_experiment import human_authorization
    config=json.loads(Path('experiments/delivery-repair.json').read_text(encoding='utf-8'))
    assert human_authorization(config)['authorized']
    changed=deepcopy(config);changed['budget']['total_spend_ceiling']='0'
    assert not human_authorization(changed)['authorized']
    changed=deepcopy(config);changed['authorization']['human_authorization_sha256']='0'*64
    assert not human_authorization(changed)['authorized']
