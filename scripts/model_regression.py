"""Opt-in real-model Windows regression; official Harbor acceptance stays separate."""
import argparse
import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys
from time import monotonic
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchmark.adapters.materialize import resolve_task
from benchmark.adapters.protocol import file_bytes, content_hash
from benchmark.core.metrics import compute_metrics
from benchmark.core.repair_comparison import paired_report
from forge.application.harness_adapter import LocalTrustedBackend
from forge.application.models import canonical_hash,strict_loads
from forge.config import ForgeConfig
from forge.engine.persistence import new_id
from forge.permissions.policy import PermissionManager
from forge.runtime.completion import TaskPolicy
from forge.runtime.dependencies import RuntimeBindings
from forge.runtime.factory import create_runtime
from forge.runtime.state import TurnCompleted
from forge.tools.shell import run_process
from scripts.evidence_gate import source_fingerprint
from scripts.delivery_experiment import check_configuration, human_authorization

GRADER_PROGRAM = """import json, os, sys, unittest
sys.path.insert(0, os.getcwd())
suite=unittest.defaultTestLoader.discover('.', pattern='*_test.py')
count=suite.countTestCases()
result=unittest.TextTestRunner(verbosity=2).run(suite)
print(json.dumps({'marker':'forge.independent-unittest.v1','collected':count,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'passed':bool(count and result.wasSuccessful() and not result.skipped)}))
raise SystemExit(0 if count and result.wasSuccessful() and not result.skipped else 1)
"""

def write(path,value,secrets=()):
    text=json.dumps(value,ensure_ascii=False,indent=2,default=str)
    for secret in secrets:
        if secret:text=text.replace(secret,'[REDACTED]')
    with path.open('x',encoding='utf-8') as stream:
        stream.write(text+'\n');stream.flush();os.fsync(stream.fileno())

class Recorder:
    def __init__(self):self.requests=[];self.event_types=[]
    def record(self,event):self.event_types.append(type(event).__name__)
    def record_request(self,kind,attributes):self.requests.append({'kind':kind,'attributes':attributes})

def frozen_task(task):
    workspace=task/'environment/workspace'
    public={p.name:file_bytes(p) for p in sorted(workspace.glob('*.py'))}
    tests={p.name:file_bytes(p) for p in sorted((task/'tests').glob('*_test.py'))}
    if not public or not tests:raise ValueError('Expected public Python stubs and independent unittest files')
    return public,tests,file_bytes(task/'instruction.md').decode('utf-8')

async def grade(directory,public,tests):
    directory.mkdir()
    for name,data in {**public,**tests}.items():(directory/name).write_bytes(data)
    process=await run_process([sys.executable,'-I','-X','utf8','-c',GRADER_PROGRAM],
        cwd=directory,timeout_seconds=30,max_output_bytes=1000000,artifact_root=directory)
    (directory/'stdout.log').write_text(process.stdout,encoding='utf-8')
    (directory/'stderr.log').write_text(process.stderr,encoding='utf-8')
    try:
        observation=strict_loads(process.stdout.strip().splitlines()[-1])
        valid=observation['marker']=='forge.independent-unittest.v1' and type(observation['collected']) is int and observation['collected']>0
    except (ValueError,KeyError,IndexError,TypeError):observation={};valid=False
    state='grader_error' if process.timed_out or not valid else 'graded'
    return {'state':state,'result':('pass' if process.exit_code==0 and observation['passed'] is True else 'fail') if state=='graded' else None,
        'exit_code':process.exit_code,'timed_out':process.timed_out,'observation':observation,
        'test_source_sha256':content_hash(tests),'delivered_source_sha256':content_hash(public),
        'runner_sha256':sha256(GRADER_PROGRAM.encode()).hexdigest(),'output_sha256':sha256((process.stdout+process.stderr).encode()).hexdigest()}

async def attempt(directory,task,schedule,config,budget,trial_id):
    directory.mkdir()
    project=directory/'project';project.mkdir()
    public,tests,instructions=frozen_task(task)
    for name,data in public.items():(project/name).write_bytes(data)
    executable=subprocess.list2cmdline([sys.executable]) if os.name=='nt' else shlex.quote(sys.executable)
    prompt=instructions+'\n\nImplement the supplied Python module. Add public behavioral tests in test_public.py, then run "'+executable+' -m unittest discover -v". Only modify the supplied Python module(s) and test_public.py. Do not install packages or access a network. The independent grader is unavailable to you. Call finish_task with truthful evidence.'
    policy=TaskPolicy(require_changes=True,require_verification=True,require_task_verification=True,
        require_positive_verification=True,max_delivery_repairs=schedule['max_delivery_repairs'],
        allowed_paths=tuple(public)+('test_public.py',),required_paths=tuple(public))
    recorder=Recorder();conversation=None;result=None
    report={'attempt_id':new_id('attempt'),'trial_id':trial_id,'attempt_no':1,**schedule,
        'started_at_utc':datetime.now(timezone.utc).isoformat(),'initial_source_sha256':content_hash(public),
        'policy':asdict(policy),'agent_outcome':'indeterminate','error_type':None}
    start=monotonic()
    baseline=await grade(directory/'baseline-grader',public,tests)
    report['baseline_grade']=baseline
    if baseline['state']!='graded' or baseline['result']!='fail':
        report.update(agent_outcome='blocked',error_type='InitialStubGradeNotFail',requests=[],event_types=[])
        write(directory/'attempt.json',report)
        return report,{'state':'unscored','result':None,'reason':'Initial independent grade did not establish an unsolved task'}
    try:
        bindings=RuntimeBindings(config=config,data_root=directory/'harness',
            permission_manager=PermissionManager(project,mode='auto',load_stored_rules=False),
            backend=LocalTrustedBackend(),recorder=recorder,trusted_extensions=False,task_relation='new',
            max_model_calls=budget['max_model_requests_per_attempt'],max_tool_calls=budget['max_tool_calls_per_attempt'],
            wall_seconds=budget['attempt_wall_seconds'])
        conversation,journal,_=create_runtime(project,bindings=bindings,task_policy=policy)
        async with asyncio.timeout(budget['attempt_wall_seconds']+10):
            async for event in conversation.stream(prompt):
                if isinstance(event,TurnCompleted):result=event.result
        if result is not None:
            report.update(agent_outcome=result.status,result=asdict(result))
    except (Exception,asyncio.CancelledError) as error:
        report.update(error_type=type(error).__name__,agent_outcome='timed_out' if isinstance(error,TimeoutError) else 'failed')
    finally:
        if conversation is not None:
            try:await conversation.runtime_close()
            except Exception as error:report['close_error_type']=type(error).__name__
        report.update(elapsed_seconds=monotonic()-start,requests=recorder.requests,event_types=recorder.event_types,
            ended_at_utc=datetime.now(timezone.utc).isoformat())
        write(directory/'attempt.json',report,(config.api_key,))
    try:
        delivered={name:file_bytes(project/name) for name in public}
        observation=await grade(directory/'independent-grader',delivered,tests)
    except (OSError,ValueError) as error:
        observation={'state':'grader_error','result':None,'error_type':type(error).__name__}
    write(directory/'grade.json',observation)
    return report,observation

async def run(configuration,task_root,output_dir,*,live=False):
    value=strict_loads(configuration.read_bytes());schedule=check_configuration(value)
    authorization=human_authorization(value)
    if not live or not authorization['authorized']:
        return {'status':'blocked','reason':'Explicit live CLI opt-in and recorded human authorization are required',
            'actual_model_calls':0,'grades':0,'checks':[]}
    if sys.platform!='win32':raise ValueError('This entry point records only the current Windows acceptance phase')
    output_dir=output_dir.resolve()
    if not output_dir.is_relative_to((ROOT/'.local').resolve()):raise ValueError('Live evidence must remain in the owned private .local directory')
    if output_dir.exists():raise ValueError('Choose a new evidence directory; an actual attempt is never overwritten or repeated silently')
    manifest=strict_loads((task_root/'taskset.json').read_bytes())
    if manifest['tasks']!=value['preexperiment']['tasks']:raise ValueError('Materialized taskset differs from preregistration')
    tasks={identity:resolve_task(task_root,manifest,identity) for identity in value['preexperiment']['task_ids']}
    for task in tasks.values():frozen_task(task)
    config=ForgeConfig.from_env()
    if config.provider!=value['model']['provider'] or config.model_id!=value['model']['requested_model']:raise ValueError('Selected model differs from preregistration')
    if config.context_window<value['budget']['max_context_tokens'] or config.max_tokens<value['budget']['max_output_tokens']:raise ValueError('Configured context/output budget is below the frozen experiment')
    config=replace(config,max_tokens=value['budget']['max_output_tokens'])
    fingerprint=source_fingerprint(ROOT)
    output_dir.mkdir(parents=True)
    frozen={'configuration_sha256':sha256(configuration.read_bytes()).hexdigest(),'source_inventory_hash':fingerprint,
        'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'model':{'provider':config.provider,'requested_model':config.model_id,'endpoint_host':urlsplit(config.base_url or '').hostname,
            'connection_sha256':canonical_hash({'base_url':config.base_url,'model':config.model_id,'provider':config.provider,
                'context_window':config.context_window,'output_tokens':config.max_tokens,'request_timeout_seconds':config.request_timeout_seconds,
                'reasoning_effort':config.reasoning_effort})},
        'platform':platform.platform(),'schedule':schedule,'authorization':authorization,
        'execution_mode':'local-trusted','official_scores':False,'eligible_for_native_pass':False,
        'grader_authority':'independent official public test bytes under Windows unittest; not Harbor/Linux grader'}
    write(output_dir/'frozen-plan.json',frozen)
    groups={group:{'trials':[],'attempts':[],'requests':[],'missing_evidence':['Formal native sandbox and Harbor trace/grade evidence unavailable'],
        'score_authority':frozen['grader_authority']} for group in ('A','B')}
    for group in groups.values():
        group['trials']=[{'id':new_id('trial'),'task_id':task,'repeat_index':0,'selected_attempt_id':None}
            for task in value['preexperiment']['task_ids']]
    source_changed=False
    for index,item in enumerate(schedule):
        if source_fingerprint(ROOT)!=fingerprint:
            source_changed=True
            break
        group=groups[item['group']]
        trial=next(t for t in group['trials'] if t['task_id']==item['task_id'] and t['repeat_index']==item['repeat_index'])
        tid=trial['id']
        observed,graded=await attempt(output_dir/f'{index:02d}-{item["group"]}-{item["task_id"]}',tasks[item['task_id']],item,config,value['budget'],tid)
        aid=observed['attempt_id'];trial['selected_attempt_id']=aid
        group['attempts'].append({'id':aid,'trial_id':tid,'attempt_no':1,'agent_outcome':observed['agent_outcome'],
            'grade_state':graded['state'],'grade_result':graded['result'],'trace_complete':False,'observation':observed,'grade':graded})
        for event in observed['requests']:
            if event['kind']=='model_request_finished':
                group['requests'].append({'id':event['attributes']['request_id'],'cost':None,'quality':'unknown','currency':None,
                    'attempt_id':aid,'observation':event['attributes']})
        write(output_dir/f'progress-{index:02d}.json',groups,(config.api_key,))
    source_changed=source_changed or source_fingerprint(ROOT)!=fingerprint
    for group in groups.values():group['metrics']=compute_metrics(group['trials'],group['attempts'],group['requests'])
    compared=paired_report(groups['A'],groups['B'],{'comparable':not source_changed,'treatment':'TaskPolicy.max_delivery_repairs 0 versus 2',
        'scope':'Frozen same-source/model/task/budget Windows regression; no official RunSpec execution'})
    report={'status':'pass','schema_version':'forge.windows-real-model-regression.v1','frozen_plan':frozen,
        'actual_model_calls':sum(len(group['requests']) for group in groups.values()),
        'grades':sum(a['grade_state']=='graded' for group in groups.values() for a in group['attempts']),
        'result':compared,'monetary_budget_policy':'human_unbounded','official_harbor_grades':0,
        'limitations':['Public preexperiment tasks were previously inspected; no independent held-out quality estimate.',
            'Actual model names are configured gateway observations; upstream identity/billing independently unverified.',
            'Windows local-trusted process/file execution does not prove SRT isolation or Linux official grader equivalence.',
            'All monetary costs unknown without trusted billing/pricing; no fabricated zero cost or gain claim.'],
        'checks':[{'id':'six-real-harness-attempts','status':'pass' if sum(len(g['attempts']) for g in groups.values())==6 else 'blocked'},
            {'id':'independent-unittest-grades','status':'pass' if sum(a['grade_state']=='graded' for g in groups.values() for a in g['attempts'])==6 else 'blocked'}]}
    if report['grades']!=6 or source_changed:report['status']='blocked'
    if source_changed:report.update(source_changed=True,groups=groups)
    write(output_dir/'report.json',report,(config.api_key,))
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configuration',type=Path,default=ROOT/'experiments/delivery-repair.json')
    parser.add_argument('--task-root',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--authorize-real-model',action='store_true',help='Explicitly opt into the recorded human-authorized real requests')
    args=parser.parse_args();logging.disable(logging.CRITICAL)
    try:result=asyncio.run(run(args.configuration,args.task_root,args.output_dir,live=args.authorize_real_model))
    except (OSError,ValueError,KeyError) as error:result={'status':'fail','error_type':type(error).__name__,'checks':[]}
    print(json.dumps({k:v for k,v in result.items() if k not in ('result','frozen_plan')},ensure_ascii=False))
    return 0 if result['status']=='pass' else 2 if result['status']=='blocked' else 1
if __name__=='__main__':raise SystemExit(main())
