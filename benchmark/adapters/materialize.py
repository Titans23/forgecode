"""Resolve exact public registry tasks without running their code or a model."""
import asyncio
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit

from benchmark.catalog import get_benchmark
from benchmark.adapters.protocol import capture_tree, content_hash, file_bytes
from forge.application.models import ContractError, strict_loads


async def materialize_registry(registry_path, *, benchmark, version, task_ids, destination):
    entry = get_benchmark(benchmark)
    if entry.status != 'ready' or not task_ids or len(set(task_ids)) != len(task_ids):
        raise ContractError('Choose exact, unique tasks from an existing runner')
    raw = file_bytes(registry_path,limit=16*1024*1024)
    # Registry is a bounded primary-source dataset, larger than an Engine RPC frame.
    datasets = json.loads(raw)
    selected = [row for row in datasets if row['name']==entry.dataset and row['version']==version]
    if len(selected)!=1:
        raise ContractError('Exact dataset version is absent from the saved registry',kind='NOT_FOUND',code=-32010)
    strict_loads(json.dumps(selected[0]))
    tasks = {row['name']:row for row in selected[0]['tasks']}
    if len(tasks)!=len(selected[0]['tasks']):
        raise ContractError('Registry contains ambiguous duplicate task names')
    if any(name not in tasks for name in task_ids):
        raise ContractError('Task ID absent from the official registry; wildcards are not accepted',kind='NOT_FOUND',code=-32010)
    from harbor.models.task.id import GitTaskId
    from harbor.models.task.task import Task
    from harbor.tasks.client import TaskClient
    source_ids=[]
    for name in task_ids:
        row=tasks[name]
        url=urlsplit(row.get('git_url',''))
        path=PurePosixPath(row['path'])
        if url.scheme!='https' or url.hostname!='github.com' or url.username or url.password or '\\' in row['path'] or path.is_absolute() or '..' in path.parts:
            raise ContractError('Public task source requires a safe, pinned GitHub HTTPS path')
        if not re.fullmatch(r'[0-9a-f]{40,64}',row.get('git_commit_id','')):
            raise ContractError('Moving task revisions are not accepted')
        source_ids.append(GitTaskId(git_url=row['git_url'],git_commit_id=row['git_commit_id'],path=Path(*path.parts)))
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    class SafeTaskClient(TaskClient):
        def _copy_task_source_to_target(self,source_path,target_path):
            capture_tree(source_path)
            return super()._copy_task_source_to_target(source_path,target_path)
    async with asyncio.timeout(300):
        downloaded=await SafeTaskClient().download_tasks(source_ids,output_dir=destination,export=True)
    manifest={'schema_version':'forge.harbor.taskset.v1','benchmark':benchmark,'dataset':entry.dataset,
        'version':version,'registry_sha256':sha256(raw).hexdigest(),'tasks':{}}
    for name,source,result in zip(task_ids,source_ids,downloaded.results,strict=True):
        captured=capture_tree(result.path)
        task=Task(result.path)
        if result.resolved_git_commit_id!=source.git_commit_id:
            raise ContractError('Downloaded source differs from pinned revision',kind='STALE_REVISION',code=-32010)
        manifest['tasks'][name]={'directory':result.path.relative_to(destination).as_posix(),
            'content_sha256':content_hash(captured),'harbor_checksum':task.checksum,
            'source':{**source.model_dump(mode='json'),'path':source.path.as_posix()},'os':task.config.environment.os.value}
    (destination/'taskset.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


def resolve_task(taskset_root, manifest, task_id):
    if task_id not in manifest['tasks']:
        raise ContractError('Task was not materialized',kind='NOT_FOUND',code=-32010)
    record=manifest['tasks'][task_id]
    relative=PurePosixPath(record['directory'])
    if relative.is_absolute() or '..' in relative.parts or '\\' in record['directory']:
        raise ContractError('Unsafe taskset path',kind='POLICY_DENIED',code=-32010)
    root=Path(taskset_root).resolve()
    path=root.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(root) or content_hash(capture_tree(path))!=record['content_sha256']:
        raise ContractError('Task bytes changed since materialization',kind='STALE_REVISION',code=-32010)
    from harbor.models.task.task import Task
    task=Task(path)
    if task.checksum!=record['harbor_checksum']:
        raise ContractError('Official task checksum changed',kind='STALE_REVISION',code=-32010)
    return path
