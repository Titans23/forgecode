"""Restore exact preregistered public tasks; never run their code or a model."""
import argparse
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchmark.adapters.protocol import capture_tree, content_hash
from forge.application.models import strict_loads
from scripts.delivery_experiment import check_configuration

async def materialize(configuration,destination):
    value=strict_loads(configuration.read_bytes());check_configuration(value)
    destination=destination.resolve()
    if not destination.is_relative_to(ROOT/'.local') or destination.exists():
        raise ValueError('Choose a new owned .local task directory; existing tasks are never replaced')
    authority=strict_loads((ROOT/value['preexperiment']['taskset_evidence']['path']).read_bytes())
    manifest=deepcopy(authority['taskset']);names=value['preexperiment']['task_ids']
    from harbor.models.task.id import GitTaskId
    from harbor.models.task.task import Task
    from harbor.tasks.client import TaskClient
    sources=[]
    for name in names:
        source=manifest['tasks'][name]['source']
        relative=PurePosixPath(source['path'].replace('\\','/'))
        if source['git_url']!='https://github.com/laude-institute/harbor-datasets.git' or relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Preregistered task source is outside the pinned public authority')
        sources.append(GitTaskId(git_url=source['git_url'],git_commit_id=source['git_commit_id'],path=Path(*relative.parts)))
    class SafeTaskClient(TaskClient):
        def _copy_task_source_to_target(self,source_path,target_path):
            capture_tree(source_path)
            return super()._copy_task_source_to_target(source_path,target_path)
    destination.mkdir(parents=True)
    async with asyncio.timeout(300):
        downloaded=await SafeTaskClient().download_tasks(sources,output_dir=destination,export=True)
    checks=[]
    for name,source,result in zip(names,sources,downloaded.results,strict=True):
        record=manifest['tasks'][name];captured=capture_tree(result.path)
        if result.resolved_git_commit_id!=source.git_commit_id or content_hash(captured)!=record['content_sha256'] or Task(result.path).checksum!=record['harbor_checksum']:
            raise ValueError('Fresh public task differs from its frozen Git/content/grader identity')
        if result.path.resolve()!=destination/record['directory']:
            raise ValueError('Actual task directory differs from its frozen identity')
        checks.append({'id':name,'status':'pass','content_sha256':content_hash(captured),'harbor_checksum':Task(result.path).checksum})
    manifest['restoration_origin']='fresh pinned Git materialization verified against preregistration'
    encoded=json.dumps(manifest,ensure_ascii=False,indent=2)+'\n'
    with (destination/'taskset.json').open('x',encoding='utf-8') as stream:stream.write(encoded)
    return {'status':'pass','scope':'real pinned public task materialization/content/grader checksum; no execution',
        'taskset_sha256':sha256(encoded.encode()).hexdigest(),'public_model_calls':0,'grades':0,'eligible_for_native_pass':False,'checks':checks}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configuration',type=Path,default=ROOT/'experiments/delivery-repair.json')
    parser.add_argument('--output-dir',type=Path,required=True);args=parser.parse_args()
    try:report=asyncio.run(materialize(args.configuration,args.output_dir))
    except (OSError,ValueError,KeyError,TimeoutError) as error:
        report={'status':'fail','reason':str(error),'public_model_calls':0,'grades':0,'checks':[]}
    print(json.dumps(report,ensure_ascii=False))
    return 0 if report['status']=='pass' else 1
if __name__=='__main__':raise SystemExit(main())
