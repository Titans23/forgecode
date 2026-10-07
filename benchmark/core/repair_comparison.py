"""Strict repair-only comparison and deterministic paired task-cluster reports."""
from copy import deepcopy
from decimal import Decimal,localcontext
import random
from forge.application.models import canonical_hash,validate
from forge.application.evaluation_client import REFERENCES

def differences(a,b,path=''):
    if type(a)!=type(b):return [path]
    if isinstance(a,dict):
        return [item for key in sorted(set(a)|set(b)) for item in
            ([path+'.'+key] if key not in a or key not in b else differences(a[key],b[key],path+'.'+key))]
    if isinstance(a,list):return [] if a==b else [path]
    return [] if a==b else [path]

def compare_repairs(a_spec,b_spec,a_values,b_values):
    normalized=[]
    issues=[]
    for spec,values,cap in ((a_spec,a_values,0),(b_spec,b_values,2)):
        validate('run-spec',spec)
        if set(values)!=set(REFERENCES):raise ValueError('Resolved configuration is incomplete')
        value=deepcopy(spec)
        for name,(group,key,schema) in REFERENCES.items():
            ref=value[group][key]
            if canonical_hash(values[name])!=ref['sha256']:raise ValueError('Resolved snapshot differs from frozen hash: '+name)
            if schema:validate(schema,values[name])
            value[group][key]={'sha256':ref['sha256']}
        if spec['harness']['max_delivery_repairs']!=cap or values['harness']['max_delivery_repairs']!=cap:
            issues.append('harness.max_delivery_repairs')
        config=deepcopy(values['harness']);config.pop('max_delivery_repairs')
        if config['parent_budget']!={'max_model_calls':spec['budget']['max_model_requests_per_attempt'],
                'max_tool_calls':spec['budget']['max_tool_calls_per_attempt'],'wall_seconds':spec['budget']['attempt_wall_seconds']}:
            issues.append('harness.parent_budget')
        value['harness'].pop('max_delivery_repairs');value['harness']['configuration']=config
        value['dataset']['task_ids'].sort()
        normalized.append(value)
    issues+=differences(*normalized)
    return {'comparable':not issues,'differences':sorted(set(issues))[:100],
        'treatment':'internal delivery repairs 0 versus 2, shared total attempt budget'}

def interleaved_schedule(tasks,repeats,seed=20261007):
    if not tasks or len(set(tasks))!=len(tasks) or type(repeats)is not int or not 1<=repeats<=3:raise ValueError('Invalid fixed experiment dimensions')
    order=list(tasks);random.Random(seed).shuffle(order)
    return [{'task_id':task,'repeat_index':repeat,'group':group,'max_delivery_repairs':0 if group=='A' else 2}
        for repeat in range(repeats) for index,task in enumerate(order)
        for group in (('A','B') if (index+repeat)%2==0 else ('B','A'))]

def paired_report(a,b,comparison,*,seed=20261007,samples=2000):
    if not comparison['comparable']:return {'status':'incomparable',**comparison}
    if type(samples)is not int or not 100<=samples<=10000:raise ValueError('Bootstrap count outside reproducible bounded protocol')
    groups=[];all_scored=True;coverage=[]
    def selected(report):
        attempts={x['id']:x for x in report['attempts']}
        if len(attempts)!=len(report['attempts']):raise ValueError('Duplicate attempt identity')
        result={}
        for trial in report['trials']:
            key=(trial['task_id'],trial['repeat_index'])
            if key in result:raise ValueError('Duplicate paired planned task/repeat')
            history=[x for x in attempts.values() if x['trial_id']==trial['id']]
            if history and (len({x['attempt_no'] for x in history})!=len(history) or
                trial['selected_attempt_id']!=max(history,key=lambda x:x['attempt_no'])['id']):raise ValueError('Selection must be the last authorized attempt, never best-of')
            attempt=attempts.get(trial['selected_attempt_id'])
            if trial['selected_attempt_id'] is not None and (attempt is None or attempt['trial_id']!=trial['id']):raise ValueError('Selected attempt belongs to another trial')
            scored=bool(attempt and attempt.get('grade_state')=='graded' and attempt.get('grade_result') in ('pass','fail'))
            result[key]={'pass':int(scored and attempt['grade_result']=='pass'),'scored':scored}
        return result
    left,right=selected(a),selected(b)
    if not left or set(left)!=set(right):raise ValueError('A/B planned denominators differ')
    tasks=sorted({key[0] for key in left})
    if len(tasks)>1000 or len(tasks)*samples>2000000:raise ValueError('Task-cluster bootstrap exceeds the bounded offline budget')
    for task in tasks:
        keys=sorted(k for k in left if k[0]==task)
        delta=sum(right[k]['pass']-left[k]['pass'] for k in keys)
        scored=sum(left[k]['scored'] and right[k]['scored'] for k in keys)
        all_scored&=scored==len(keys)
        groups.append((delta,len(keys)))
        coverage.append({'task_id':task,'planned_pairs':len(keys),'both_scored':scored,'confirmed_pass_delta':delta})
    with localcontext() as context:
        context.prec=40
        point=str(Decimal(sum(x[0] for x in groups))/sum(x[1] for x in groups))
        interval=None
        if all_scored and len(tasks)>=2:
            rng=random.Random(seed);draws=[]
            for _ in range(samples):
                draw=[groups[rng.randrange(len(groups))] for _ in groups]
                draws.append(Decimal(sum(x[0] for x in draw))/sum(x[1] for x in draw))
            draws.sort();interval={'lower':str(draws[(samples*25)//1000]),'upper':str(draws[min(samples-1,(samples*975)//1000)]),
                'confidence':'0.95','method':'paired task-cluster percentile bootstrap','seed':seed,'samples':samples}
    return {'status':'computed','schema_version':'forge.repair-comparison.v1','comparison':comparison,
        'planned_pairs':len(left),'task_clusters':len(tasks),'paired_coverage':coverage,
        'confirmed_pass_difference':point,'interval':interval,
        'uncertainty':'incomplete independent grades; interval unavailable' if not all_scored else
            'fewer than two task clusters; interval unavailable' if len(tasks)<2 else
            'small task count; exploratory interval' if len(tasks)<8 else 'conditional on frozen protocol and observed task set',
        'A_metrics':a['metrics'],'B_metrics':b['metrics'],'source_authority':[a.get('score_authority'),b.get('score_authority')],
        'trials':{'A':a['trials'],'B':b['trials']},'attempts':{'A':a['attempts'],'B':b['attempts']},
        'requests':{'A':a.get('requests',[]),'B':b.get('requests',[])},
        'limitations':['No best-of selection; reports preserve the last authorized attempt and all usage.',
            'Imported bundle integrity does not establish model/grader provenance.','Missing grader evidence remains explicitly visible.'],
        'missing_evidence':{'A':a['missing_evidence'],'B':b['missing_evidence']}}

def report_from_bundle(bundle,a_run,b_run,a_plan,b_plan):
    from benchmark.core.results import report_bundle
    if a_run==b_run:raise ValueError('A and B must be distinct frozen runs')
    for plan in (a_plan,b_plan):
        validate('run-spec',plan['spec'])
        if plan['spec_hash']!=canonical_hash(plan['spec']):raise ValueError('Exported plan hash changed')
    compared=compare_repairs(a_plan['spec'],b_plan['spec'],a_plan['resolved_snapshots'],b_plan['resolved_snapshots'])
    result=report_bundle(bundle);runs={r['run_id']:r for r in result['runs']}
    if any(identity not in runs for identity in (a_run,b_run)):raise ValueError('Selected frozen run is absent from bundle')
    for identity,plan in ((a_run,a_plan),(b_run,b_plan)):
        if runs[identity]['spec_hash']!=plan['spec_hash']:raise ValueError('Result belongs to another frozen configuration')
    return {'bundle_sha256':result['source_sha256'],'origin':result['origin'],
        'result':paired_report(runs[a_run],runs[b_run],compared)}
