"""Actual public small-set provenance/config probe; no model or Docker environment starts."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from benchmark.adapters.diagnostics import doctor
from benchmark.adapters.harbor import HarborAdapter
from benchmark.adapters.materialize import resolve_task
from benchmark.harbor.snapshot import freeze_source
from forge.engine.persistence import new_id


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--taskset',type=Path,default=ROOT/'.local/f20/aider-smallset')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report={'status':'blocked','scope':'official-Harbor-public-smallset-configuration',
        'execution_label':'official-environment','model_calls':0,'grades':0,'checks':[],
        'started_at_utc':datetime.now(timezone.utc).isoformat(),'readiness':doctor()}
    try:
        capture=ROOT/'.local/f20/probe-source'
        source=freeze_source(ROOT,capture)
        adapter=HarborAdapter('aider-polyglot',taskset_root=args.taskset,source_root=source)
        report['taskset']=adapter.taskset
        # Draft configuration only: model selection and paid authorization are deliberately absent.
        report['configuration_scope']='draft JobConfig, not an accepted run or experimental result'
        for name,record in adapter.taskset['tasks'].items():
            resolve_task(args.taskset,adapter.taskset,name)
            with tempfile.TemporaryDirectory(dir=ROOT/'.local/f20') as temporary:
                directory=Path(temporary)/'prepared'
                spec={'model':{'requested_model':'unconfigured-awaiting-selection','provider':'anthropic'},
                    'budget':{'max_model_requests_per_attempt':10,'max_tool_calls_per_attempt':20}}
                values={'model_parameters':{'temperature':None,'top_p':None,'max_output_tokens':16384,'reasoning_effort':None},
                    'harness':{'max_context_tokens':128000,'compaction_enabled':True,'explore_enabled':True,
                        'trusted_extensions_enabled':False,'max_delivery_repairs':0,
                        'parent_budget':{'max_model_calls':10,'max_tool_calls':20,'wall_seconds':120}},
                    'grader':{'timeout_seconds':1800}}
                work={'task_id':name,'business_id':new_id('attempt'),'run_id':new_id('run'),
                    'trial_id':new_id('trial'),'trace_id':'1'*32,'span_id':'2'*16,'duration_seconds':120}
                prepared=adapter.materialize(spec,values,work,directory,endpoint='https://api.anthropic.com')
                command=[*prepared['argv'],'--print-config']
                result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,
                    encoding='utf-8',errors='replace',timeout=45)
                if result.returncode:
                    raise RuntimeError('Actual Harbor CLI rejected the generated JobConfig')
                resolved=json.loads(result.stdout)
                config=prepared['job_config']
                assert config.n_attempts==1 and config.n_concurrent_trials==1 and config.retry.max_retries==0
                assert len(config.tasks)==1 and config.verifier.disable is False
                assert config.agents[0].kwargs['source_dir']==str(source.resolve())
                report['checks'].append({'name':name,'status':'pass','scope':'real task bytes / pinned Git / official JobConfig / actual --print-config',
                    'content_sha256':record['content_sha256'],'harbor_checksum':record['harbor_checksum'],
                    'source':record['source'],'cli_exit_code':result.returncode,'resolved_task_count':len(resolved['tasks'])})
        report['reason']='Actual public tasks/configuration checked; Docker daemon, explicit model/budget approval and policy/spend reconciliation still required. No model, independent grader or native sandbox was run.'
    except Exception as error:
        report['reason']='Public task/configuration probe unavailable: '+type(error).__name__
        if not isinstance(error,(FileNotFoundError,PermissionError)):
            report['status']='fail'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return 2 if report['status']=='blocked' else 1


if __name__=='__main__':
    raise SystemExit(main())
