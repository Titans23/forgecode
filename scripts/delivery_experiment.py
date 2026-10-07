"""Preregister repair-only conditions and recompute bundles; official execution and opt-in Windows live regression stay separate."""
import argparse
from hashlib import sha256
import json
from pathlib import Path,PurePosixPath
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from forge.application.models import canonical_hash,strict_loads
from benchmark.core.repair_comparison import interleaved_schedule,report_from_bundle

def check_configuration(value):
    if value.get('schema_version')!='forge.experiment.preregistration.v1':raise ValueError('Unsupported preregistration')
    if value['treatment']!={'A':{'max_delivery_repairs':0},'B':{'max_delivery_repairs':2},'other_configuration_differences_allowed':False}:raise ValueError('The only permitted treatment is repairs 0 versus 2')
    plan=value['preexperiment']
    tasks=plan['task_ids']
    if len(tasks)!=3 or len(set(tasks))!=3 or set(plan['tasks'])!=set(tasks) or plan['repeats']!=1 or plan['planned_group_runs']!=6:raise ValueError('Preexperiment must preserve three exact tasks by two groups')
    evidence=plan['taskset_evidence'];relative=evidence['path']
    if not isinstance(relative,str) or '\\' in relative or ':' in relative or PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts:
        raise ValueError('Task evidence must be an owned repository-relative file')
    path=(ROOT/relative).resolve(strict=True)
    if not path.is_relative_to(ROOT):raise ValueError('Task evidence escapes repository ownership')
    actual=strict_loads(path.read_bytes())
    if canonical_hash(actual)!=evidence['canonical_sha256'] or actual['taskset']['tasks']!=plan['tasks']:
        raise ValueError('Official task reference or its frozen evidence hash changed')
    for task in plan['tasks'].values():
        digest=task['content_sha256']
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('Exact official task hash required')
    if value['protocol']['selection']!='last_authorized_attempt' or value['protocol']['harbor_retries']!=0 or value['protocol']['concurrency']!=1:raise ValueError('Result selection/retry/concurrency differs from preregistration')
    if value['protocol']['feedback']!='none' or value['protocol']['cache_mode']!='cold':raise ValueError('Unimplemented feedback/cache protocol must not be substituted')
    if value['budget']['repairs_share_parent_budget'] is not True:raise ValueError('Repairs must share fixed parent budgets')
    budget=value['budget']
    for name in ('max_model_requests_per_attempt','max_tool_calls_per_attempt','attempt_wall_seconds',
            'trial_wall_seconds','max_context_tokens','max_output_tokens'):
        if type(budget[name]) is not int or budget[name]<=0:raise ValueError('Positive fixed parent budget required: '+name)
    if budget['max_infrastructure_attempts']!=1 or budget['trial_wall_seconds']!=budget['attempt_wall_seconds']:
        raise ValueError('Preexperiment forbids outer retry or another attempt budget')
    if budget['max_output_tokens']>budget['max_context_tokens']:raise ValueError('Output budget exceeds context budget')
    if type(value['protocol']['schedule_seed']) is not int:raise ValueError('Explicit reproducible schedule seed required')
    if [(s['tasks'],s['groups'],s['repeats'],s['planned_trials']) for s in value['formal']['allowed_shapes']]!=[(12,2,2,48),(20,2,3,120)]:raise ValueError('Formal shapes differ from the specification')
    if value['formal']['exclude_preexperiment_tasks'] is not True:raise ValueError('Previously inspected tasks cannot be called independent holdout')
    selected=value['formal']['selected_shape']
    formal_tasks=value['formal']['task_ids']
    if selected is None:
        if formal_tasks:raise ValueError('Formal tasks require a frozen shape')
    elif selected not in value['formal']['allowed_shapes'] or len(formal_tasks)!=selected['tasks'] or len(set(formal_tasks))!=len(formal_tasks) or set(formal_tasks)&set(tasks):
        raise ValueError('Formal holdout does not match its frozen shape or overlaps inspected tasks')
    return interleaved_schedule(tasks,1,value['protocol']['schedule_seed'])

HUMAN_AUTHORIZATION_SHA256='b11b0954d3342159a908a26649590b229a862d917fd64ddc0398822db2a3e2e4'

def human_authorization(value):
    # Preregistration flags alone cannot create human authorization or spend permission.
    auth=value.get('authorization',{})
    relative=auth.get('human_authorization_ref')
    if relative!='docs/implementation/evidence/human-authorization-20261007.json':
        return {'authorized':False,'reason':'Recorded explicit human authorization is absent'}
    try:
        record=strict_loads((ROOT/relative).read_bytes())
        digest=canonical_hash(record)
    except (OSError,ValueError):
        return {'authorized':False,'reason':'Human authorization record is unavailable'}
    authorized=(digest==HUMAN_AUTHORIZATION_SHA256 and auth.get('human_authorization_sha256')==digest and
        record.get('real_model_authorized') is True and record.get('monetary_budget_policy')=='human_unbounded' and
        record.get('monetary_ceiling') is None and value['budget'].get('monetary_budget_policy')=='human_unbounded' and
        value['budget']['total_spend_ceiling'] is None)
    return {'authorized':authorized,'authorization_ref':relative,'authorization_sha256':digest,
        'monetary_budget_policy':'human_unbounded' if authorized else None,
        'reason':None if authorized else 'Preregistration differs from the explicit human authorization'}

def verify(path):
    raw=path.read_bytes()
    value=strict_loads(raw);schedule=check_configuration(value)
    from benchmark.adapters.diagnostics import doctor
    readiness=doctor()
    authorization=human_authorization(value)
    issues=['Official execution policy verification, authorization binding and per-request accounting remain unavailable.',
        'The preregistration has unresolved source/model/environment snapshots and is not an executable RunSpec.']
    if not authorization['authorized']:issues.insert(0,'Recorded explicit human authorization is absent or inconsistent.')
    issues+=readiness['issues']
    return {'status':'blocked','reason':'; '.join(issues),'scope':'real preregistration validation and official runner readiness; zero public-model execution',
        'public_model_calls':0,'grades':0,'eligible_for_native_pass':False,'configuration_sha256':sha256(raw).hexdigest(),
        'schedule':schedule,'readiness':readiness,'authorization':authorization,'pending_formal_choice':value['formal']['selected_shape'],
        'checks':[{'id':'exact-three-task-two-group-plan','status':'pass'},{'id':'actual-official-reference-evidence','status':'pass'},
            {'id':'interleaved-single-worker-schedule','status':'pass'},
            {'id':'shared-budget-repair-only-configuration','status':'pass'},{'id':'explicit-human-authorization','status':'pass' if authorization['authorized'] else 'blocked'},
            {'id':'official-policy-and-request-accounting','status':'blocked'}]}

def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    for name in ('verify','run'):
        command=commands.add_parser(name);command.add_argument('--configuration',type=Path,default=ROOT/'experiments/delivery-repair.json')
        command.add_argument('--output',type=Path,required=True)
    report=commands.add_parser('report');report.add_argument('--from-bundle',type=Path,required=True)
    for name in ('plan-a','plan-b'):report.add_argument('--'+name,type=Path,required=True)
    for name in ('run-a','run-b'):report.add_argument('--'+name,required=True)
    report.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        result=report_from_bundle(args.from_bundle,args.run_a,args.run_b,strict_loads(args.plan_a.read_bytes()),
            strict_loads(args.plan_b.read_bytes())) if args.command=='report' else verify(args.configuration)
    except (OSError,ValueError,KeyError) as error:result={'status':'fail','reason':str(error),'checks':[]}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    return 2 if result.get('status')=='blocked' else 1 if result.get('status')=='fail' or result.get('result',{}).get('status')=='incomparable' else 0
if __name__=='__main__':raise SystemExit(main())
