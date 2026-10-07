"""Offline result validation and recomputation: no provider, subprocess or task loader."""
import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

from benchmark.core.bundle import staged_bundle
from benchmark.core.metrics import compute_metrics
from benchmark.core.spec import INFRASTRUCTURE_ERRORS
from forge.application.models import ContractError, canonical_hash, strict_loads, validate, validate_event
from forge.engine.persistence import encoded

IDS=re.compile(r'^(run|trial|attempt|grade|request|art|annotation)-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
CATEGORIES={'model_reasoning','tool_usage','context','verification','sandbox','environment','provider','grader','unknown'}
RECORDS=('trials','attempts','grades','requests','events','annotations')


def identity(value,prefix):
    if not isinstance(value,str) or not IDS.fullmatch(value) or not value.startswith(prefix+'-'):
        raise ContractError('Invalid result record identity')


def json_lines(root,name):
    path=root/(name+'.jsonl')
    if not path.is_file(): raise ContractError('Structured results missing: '+name)
    rows=[]
    with path.open('rb') as file:
        while line:=file.readline(1048577):
            if len(line)>1048576 or not line.endswith(b'\n'): raise ContractError('Result JSONL line quota or framing invalid')
            row=strict_loads(line)
            if not isinstance(row,dict): raise ContractError('Result JSONL requires objects')
            rows.append(row)
            if len(rows)>100000: raise ContractError('Structured record quota exceeded')
    return rows


def trace_completeness(attempts,events):
    types,pending={},{}
    for event in events:
        if event['origin'] not in ('trusted_engine','trusted_bridge','grader_adapter') or not event['attempt_id']: continue
        target=event['attempt_id'];kind=event['event_type']
        types.setdefault(target,set()).add(kind);boundaries=pending.setdefault(target,set())
        key=event['attributes'].get('model_request_id') if kind.startswith('model.request.') else event.get('execution_id') if kind.startswith('tool.') else event['attributes'].get('evidence_id') if kind.startswith('verification.') else None
        if key and kind.endswith('.started'): boundaries.add(key)
        elif key and kind.endswith(('.finished','.failed')): boundaries.discard(key)
    for attempt in attempts:
        required={'attempt.started','attempt.finished'}
        if attempt['grade_state'] in ('graded','grader_error'): required.add('grade.finished')
        attempt['trace_complete']=required<=types.get(attempt['id'],set()) and not pending.get(attempt['id'])


def unique_rows(rows,prefix):
    by_id={}
    for row in rows:
        identity(row.get('id'),prefix)
        if row['id'] in by_id: raise ContractError('Duplicate structured identity')
        by_id[row['id']]=row
    return by_id


def read_results(root,manifest):
    """Validate relations before any database publish. Source origins remain claims."""
    paths={item['path'] for item in manifest['contents']}
    data={name:json_lines(root,name) for name in RECORDS}
    runs={}
    for path in sorted(paths):
        parts=path.split('/')
        if len(parts)==3 and parts[0]=='runs' and parts[2]=='spec.json':
            identity(parts[1],'run');spec=strict_loads((root/path).read_bytes());validate('run-spec',spec)
            state_path=f'runs/{parts[1]}/state.json'
            if state_path not in paths: raise ContractError('Run state missing')
            state=strict_loads((root/state_path).read_bytes())
            if not isinstance(state,dict) or set(state)!={'state','spec_hash'} or state['spec_hash']!=canonical_hash(spec) or state['state'] not in ('created','queued','running','cancel_requested','completed','failed','cancelled','indeterminate'):
                raise ContractError('Run state or immutable spec hash invalid')
            runs[parts[1]]={'spec':spec,**state}
    if not runs or len(runs)>10000: raise ContractError('Run collection is empty or too large')
    allowed={'evidence.json','annotation_details.jsonl',*(name+'.jsonl' for name in RECORDS)}
    allowed|={f'runs/{run}/{name}.json' for run in runs for name in ('spec','state')}
    for path in paths-allowed:
        parts=path.split('/')
        if len(parts)!=2 or parts[0]!='artifacts': raise ContractError('Unsupported result bundle file')
        identity(parts[1],'art')
    if set(manifest['task_ids'])!={task for run in runs.values() for task in run['spec']['dataset']['task_ids']}:
        raise ContractError('Manifest task collection differs from RunSpec')
    trials=unique_rows(data['trials'],'trial');attempts=unique_rows(data['attempts'],'attempt');grades=unique_rows(data['grades'],'grade')
    for row in trials.values():
        identity(row.get('run_id'),'run')
        if set(row)!={'id','run_id','task_id','task_revision','repeat_index','selected_attempt_id'} or row['run_id'] not in runs:
            raise ContractError('Trial relation invalid')
        spec=runs[row['run_id']]['spec']
        if not isinstance(row['task_id'],str) or not isinstance(row['task_revision'],str): raise ContractError('Trial task identity must be text')
        if row['task_id'] not in spec['dataset']['task_ids'] or row['task_revision']!=spec['dataset']['task_revisions'][row['task_id']] or type(row['repeat_index']) is not int or not 0<=row['repeat_index']<spec['protocol']['repeats']:
            raise ContractError('Trial differs from immutable plan')
    for run_id,run in runs.items():
        if len(run['spec']['dataset']['task_ids'])*run['spec']['protocol']['repeats']>10000: raise ContractError('Planned trial quota exceeded')
        planned={(task,repeat) for task in run['spec']['dataset']['task_ids'] for repeat in range(run['spec']['protocol']['repeats'])}
        actual=[(row['task_id'],row['repeat_index']) for row in trials.values() if row['run_id']==run_id]
        if len(planned)>10000 or len(actual)!=len(planned) or set(actual)!=planned: raise ContractError('Incomplete or duplicate planned denominator')
    allowed_attempt={'id','trial_id','attempt_no','execution_state','error_origin','cleanup_state','agent_outcome','authoritative_grade_id','trace_id','span_id'}
    per_trial={}
    for row in attempts.values():
        identity(row.get('trial_id'),'trial')
        if set(row)!=allowed_attempt or row['trial_id'] not in trials: raise ContractError('Attempt relation invalid')
        spec=runs[trials[row['trial_id']]['run_id']]['spec']
        if row['authoritative_grade_id'] is not None: identity(row['authoritative_grade_id'],'grade')
        for key,length in (('trace_id',32),('span_id',16)):
            if row[key] is not None and (not isinstance(row[key],str) or not re.fullmatch('[0-9a-f]{'+str(length)+'}',row[key])): raise ContractError('Attempt trace identity invalid')
        if row['error_origin'] not in (None,'task_logic',*INFRASTRUCTURE_ERRORS): raise ContractError('Attempt failure category invalid')
        if type(row['attempt_no']) is not int or not 1<=row['attempt_no']<=spec['protocol']['max_infrastructure_attempts'] or row['execution_state'] not in ('planned','queued','running','finished','cancelled','error','blocked') or row['cleanup_state'] not in ('pending','running','clean','residual','unknown') or row['agent_outcome'] not in (None,'completed','failed','cancelled','timed_out','budget_exhausted','blocked','indeterminate'):
            raise ContractError('Attempt status or retry bound invalid')
        group=per_trial.setdefault(row['trial_id'],{})
        if row['attempt_no'] in group: raise ContractError('Duplicate attempt number')
        group[row['attempt_no']]=row['id']
    for trial in trials.values():
        group=per_trial.get(trial['id'],{})
        if group and set(group)!=set(range(1,max(group)+1)): raise ContractError('Attempt sequence has a gap')
        selected=group[max(group)] if group else None
        if trial['selected_attempt_id']!=selected: raise ContractError('Selected attempt is not last authorized attempt')
    for row in grades.values():
        identity(row.get('attempt_id'),'attempt')
        if set(row)!={'id','attempt_id','grader_hash','artifact_hash','state','reward','result'} or row['attempt_id'] not in attempts or row['state'] not in ('unscored','grading','graded','grader_error'):
            raise ContractError('Grade relation invalid')
        if not all(isinstance(row[key],str) and re.fullmatch('[0-9a-f]{64}',row[key]) for key in ('grader_hash','artifact_hash')): raise ContractError('Grade evidence hash invalid')
        if row['state']=='graded':
            try: reward=Decimal(row['reward']) if isinstance(row['reward'],str) and re.fullmatch(r'[01](?:\.[0-9]{1,126})?',row['reward']) else Decimal('NaN')
            except InvalidOperation: reward=Decimal('NaN')
            if not reward.is_finite() or not 0<=reward<=1 or row['result']!=('pass' if reward==1 else 'fail'): raise ContractError('Independent reward/result invalid')
        elif row['result'] is not None or row['reward'] is not None: raise ContractError('Unscored/error grade cannot be a score')
    for row in attempts.values():
        grade=grades.get(row['authoritative_grade_id'])
        if row['authoritative_grade_id'] and (grade is None or grade['attempt_id']!=row['id']): raise ContractError('Authoritative grade belongs to another attempt')
        row['grade_state']=grade['state'] if grade else None;row['grade_result']=grade['result'] if grade else None
        row['grade_reward']=grade['reward'] if grade else None
    request_ids=set()
    for row in data['requests']:
        identity(row.get('id'),'request')
        identity(row.get('run_id'),'run')
        if set(row)!={'id','run_id','cost','quality','currency'} or row['run_id'] not in runs or row['id'] in request_ids:
            raise ContractError('Usage relation or duplicate request invalid')
        request_ids.add(row['id'])
        if row['quality'] not in ('actual','estimated','unknown') or (row['cost'] is not None and not isinstance(row['cost'],str)):
            raise ContractError('Usage quality invalid')
        if row['cost'] is not None and not re.fullmatch(r'(?:0|[1-9][0-9]{0,63})(?:\.[0-9]{1,63})?',row['cost']): raise ContractError('Bounded decimal ledger amount required')
        if row['currency'] is not None and (not isinstance(row['currency'],str) or not re.fullmatch('[A-Z]{3}',row['currency'])): raise ContractError('Usage currency invalid')
    event_ids=set()
    for event in data['events']:
        validate_event(event)
        if event['event_id'] in event_ids or event['run_id'] not in runs or event['trial_id'] and event['trial_id'] not in trials or event['attempt_id'] and event['attempt_id'] not in attempts:
            raise ContractError('Event identity/relation invalid')
        if event['trial_id'] and trials[event['trial_id']]['run_id']!=event['run_id'] or event['attempt_id'] and attempts[event['attempt_id']]['trial_id']!=event['trial_id']:
            raise ContractError('Event cross-run relation invalid')
        event_ids.add(event['event_id'])
    annotations=unique_rows(data['annotations'],'annotation')
    previous_annotations=set()
    for row in data['annotations']:
        identity(row.get('attempt_id'),'attempt')
        if set(row)!={'id','attempt_id','author','category','evidence_refs','supersedes'} or row['attempt_id'] not in attempts or not isinstance(row['category'],str) or row['category'] not in CATEGORIES or not isinstance(row['author'],str) or not 1<=len(row['author'])<=128 or not isinstance(row['evidence_refs'],str):
            raise ContractError('Annotation relation or category invalid')
        if row['supersedes'] is not None:
            identity(row['supersedes'],'annotation')
            prior=annotations.get(row['supersedes'])
            if row['supersedes'] not in previous_annotations or not prior or prior['attempt_id']!=row['attempt_id']:
                raise ContractError('Annotation history contains a cycle, forward or foreign reference')
        previous_annotations.add(row['id'])
        refs=strict_loads(row['evidence_refs'])
        if not isinstance(refs,list) or len(refs)>100: raise ContractError('Annotation evidence bounds invalid')
        for ref in refs: identity(ref,'art')
    details=json_lines(root,'annotation_details') if 'annotation_details.jsonl' in paths else []
    seen_details=set()
    for row in details:
        if set(row)!={'schema_version','annotation_id','created_at','note'} or row['schema_version']!='forge.annotation.details.v1':raise ContractError('Annotation details schema invalid')
        identity(row['annotation_id'],'annotation')
        if row['annotation_id'] not in annotations or row['annotation_id'] in seen_details or not isinstance(row['created_at'],str) or len(row['created_at'])>64 or not isinstance(row['note'],str) or len(row['note'])>4000:
            raise ContractError('Annotation details relation or bounds invalid')
        from datetime import datetime
        try:
            timestamp=datetime.fromisoformat(row['created_at'].replace('Z','+00:00'))
            if timestamp.tzinfo is None:raise ValueError('Timezone required')
        except ValueError as error:raise ContractError('Annotation timestamp invalid') from error
        seen_details.add(row['annotation_id'])
    data['annotation_details']=details
    trace_completeness(attempts.values(),data['events'])
    evidence=strict_loads((root/'evidence.json').read_bytes(),max_bytes=16777216)
    if set(evidence)!={'classification','artifacts','missing'} or evidence['classification'] not in ('metadata_only','redacted_artifacts') or not isinstance(evidence['artifacts'],list) or not isinstance(evidence['missing'],list):
        raise ContractError('Evidence/privacy index invalid')
    artifact_ids=set()
    for item in evidence['artifacts']:
        if not isinstance(item,dict): raise ContractError('Evidence entries require objects')
        identity(item.get('artifact_id'),'art')
        identity(item.get('run_id'),'run')
        if item['artifact_id'] in artifact_ids or item.get('run_id') not in runs or item.get('status') not in ('missing','omitted_private','omitted_metadata_only','redacted_copy'):
            raise ContractError('Evidence relation invalid')
        artifact_ids.add(item['artifact_id'])
        if set(item)!={'artifact_id','run_id','source_sha256','export_sha256','status'} or not isinstance(item['source_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',item['source_sha256']) or item['export_sha256'] is not None and (not isinstance(item['export_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',item['export_sha256'])):
            raise ContractError('Evidence hash identity invalid')
        copy_path='artifacts/'+item['artifact_id']
        if (item['status']=='redacted_copy')!=(copy_path in paths): raise ContractError('Evidence copy availability mismatch')
        if item['status']=='redacted_copy' and sha256_file(root/copy_path)!=item.get('export_sha256'): raise ContractError('Evidence export hash mismatch')
    if {path.split('/')[1] for path in paths if path.startswith('artifacts/')}!= {x['artifact_id'] for x in evidence['artifacts'] if x['status']=='redacted_copy'}:
        raise ContractError('Artifact missing privacy index')
    for item in evidence['missing']: identity(item,'art')
    if len(set(evidence['missing']))!=len(evidence['missing']) or set(evidence['missing'])!={x['artifact_id'] for x in evidence['artifacts'] if x['status']=='missing'}: raise ContractError('Missing evidence index disagrees')
    for row in data['annotations']:
        if not set(strict_loads(row['evidence_refs']))<={x['artifact_id'] for x in evidence['artifacts'] if x['run_id']==trials[attempts[row['attempt_id']]['trial_id']]['run_id']}: raise ContractError('Annotation evidence not indexed in its run')
    reports=[]
    for run_id,run in runs.items():
        local_trials=[row for row in trials.values() if row['run_id']==run_id]
        trial_ids={row['id'] for row in local_trials}
        local_attempts=[row for row in attempts.values() if row['trial_id'] in trial_ids]
        local_requests=[{k:v for k,v in row.items() if k!='run_id'} for row in data['requests'] if row['run_id']==run_id]
        try: metrics=compute_metrics(local_trials,local_attempts,local_requests)
        except (ValueError,InvalidOperation) as error: raise ContractError('Invalid usage ledger fact') from error
        reports.append({'schema_version':'forge.eval.report.v1','run_id':run_id,'state':run['state'],'spec_hash':run['spec_hash'],
            'model_mode':run['spec']['model_mode'],'metrics':metrics,'trials':local_trials,'attempts':local_attempts,
            'missing_evidence':[x['artifact_id'] for x in evidence['artifacts'] if x['run_id']==run_id and x['status']!='redacted_copy'],
            'evidence_changes':[x for x in evidence['artifacts'] if x['run_id']==run_id],
            'annotations':[x for x in data['annotations'] if attempts[x['attempt_id']]['trial_id'] in trial_ids],
            'annotation_details':[x for x in details if attempts[annotations[x['annotation_id']]['attempt_id']]['trial_id'] in trial_ids],
            'origin':'imported_unverified','score_authority':'imported_independent_grader_claim',
            'official_metrics_preserved_separately':True})
    return {'runs':runs,'records':data,'reports':reports,'evidence':evidence}


def sha256_file(path):
    from hashlib import sha256
    digest=sha256()
    with Path(path).open('rb') as file:
        while chunk:=file.read(1048576): digest.update(chunk)
    return digest.hexdigest()


def report_bundle(path):
    with staged_bundle(path) as (root,manifest,digest):
        return {'schema_version':'forge.bundle.report.v1','bundle_id':manifest['bundle_id'],'source_sha256':digest,
            'origin':'imported_unverified','runs':read_results(root,manifest)['reports']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    command=commands.add_parser('report');command.add_argument('--from-bundle',type=Path,required=True)
    args=parser.parse_args()
    try: print(encoded(report_bundle(args.from_bundle)));return 0
    except (ContractError,OSError) as error: print(encoded({'status':'rejected','reason':str(error)}));return 2


if __name__=='__main__': raise SystemExit(main())
