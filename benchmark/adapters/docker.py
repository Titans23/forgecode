"""Host receipts backed by actual Docker project/container/volume inventories."""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from harbor.environments.docker.docker import _sanitize_docker_compose_project_name
from benchmark.harbor.bounded_docker import BoundedDockerEnvironment


async def docker_query(*arguments):
    child=await asyncio.create_subprocess_exec('docker',*arguments,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        async with asyncio.timeout(10):
            output=await child.stdout.read(1048577)
            if len(output)>1048576: raise ValueError('Docker inventory exceeds its bounded report')
            await child.wait()
            if child.returncode: raise RuntimeError('Docker inventory query failed')
            return output.decode('utf-8').strip()
    finally:
        if child.returncode is None: child.kill();await child.wait()


async def docker_inventory(project, known_volumes=()):
    label='label=com.docker.compose.project='+project
    daemon=await docker_query('info','--format','{{.ID}}')
    if not daemon: raise ValueError('Docker daemon identity unavailable')
    containers=(await docker_query('ps','--all','--quiet','--no-trunc','--filter',label)).splitlines()
    volumes=set((await docker_query('volume','ls','--quiet','--filter',label)).splitlines())
    networks=(await docker_query('network','ls','--quiet','--no-trunc','--filter',label)).splitlines()
    # Anonymous volumes may lack the Compose project label. Capture their exact
    # names from actual container mounts before stop/down can remove the parent.
    for container in containers:
        mounts=json.loads(await docker_query('inspect','--format','{{json .Mounts}}',container))
        volumes.update(mount['Name'] for mount in mounts if mount.get('Type')=='volume')
    if known_volumes:
        current=set((await docker_query('volume','ls','--quiet')).splitlines())
        volumes.update(current.intersection(known_volumes))
    return {'daemon_id':daemon,'project':project,'containers':sorted(containers),'volumes':sorted(volumes),'networks':sorted(networks)}


class EvaluationDockerEnvironment(BoundedDockerEnvironment):
    def __init__(self,*args,receipt_path,**kwargs):
        super().__init__(*args,**kwargs)
        self._receipt_path=Path(receipt_path)
        self._receipt_id=uuid4().hex
        self._resource_baseline=None

    def _receipt(self,phase,**facts):
        # Host-owned directory is outside all task mounts; Agent stdout is not evidence.
        self._receipt_path.parent.mkdir(parents=True,exist_ok=True)
        with self._receipt_path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'resource_id':self._receipt_id,'phase':phase,**facts})+'\n')
            stream.flush();os.fsync(stream.fileno())

    async def start(self,*args,**kwargs):
        project=_sanitize_docker_compose_project_name(self.session_id)
        self._receipt('start_intent',project=project)
        baseline=await docker_inventory(project)
        if any(baseline[key] for key in ('containers','volumes','networks')):
            raise RuntimeError('Docker project already owns resources; reconciliation required')
        self._resource_baseline=baseline
        self._receipt('baseline',inventory=baseline)
        await super().start(*args,**kwargs)
        self._receipt('started')

    async def stop(self,delete):
        baseline=getattr(self,'_resource_baseline',None)
        before=None
        if baseline:
            try: before=await docker_inventory(baseline['project'])
            except Exception: pass  # No inventory means unknown, even if down succeeds.
        await super().stop(delete)
        # Harbor suppresses down failures. Its return alone never establishes absence.
        self._receipt('stop_returned',delete_requested=delete is True)
        if before and delete is True:
            try:
                after=await docker_inventory(baseline['project'],before['volumes'])
                self._receipt('resource_query',before=before,after=after)
            except Exception as error:
                self._receipt('query_failed',reason=type(error).__name__)


def cleanup_state(receipts):
    starts={r['resource_id'] for r in receipts if r.get('phase')=='start_intent'}
    if not starts:return 'unknown'
    states=[]
    for identity in starts:
        rows=[r for r in receipts if r.get('resource_id')==identity]
        baseline=next((r.get('inventory') for r in rows if r.get('phase')=='baseline'),None)
        query=next((r for r in reversed(rows) if r.get('phase')=='resource_query'),None)
        if not baseline or not query or rows[-1] is not query or not any(r.get('phase')=='stop_returned' and r.get('delete_requested') is True for r in rows):
            states.append('unknown');continue
        inventories=[baseline,query.get('before',{}),query.get('after',{})]
        if any(not value.get('daemon_id') or value.get('daemon_id')!=baseline['daemon_id'] or value.get('project')!=baseline['project'] or
               any(not isinstance(value.get(key),list) for key in ('containers','volumes','networks')) for value in inventories):
            states.append('unknown');continue
        if any(baseline[key] for key in ('containers','volumes','networks')):states.append('unknown');continue
        states.append('residual' if any(query['after'][key] for key in ('containers','volumes','networks')) else 'clean')
    return 'unknown' if 'unknown' in states else 'residual' if 'residual' in states else 'clean'
