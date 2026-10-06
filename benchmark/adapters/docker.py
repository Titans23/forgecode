"""Host-side receipts for actual official Docker start/stop boundaries."""
import json
import os
from pathlib import Path
from uuid import uuid4

from benchmark.harbor.bounded_docker import BoundedDockerEnvironment


class EvaluationDockerEnvironment(BoundedDockerEnvironment):
    def __init__(self,*args,receipt_path,**kwargs):
        super().__init__(*args,**kwargs)
        self._receipt_path=Path(receipt_path)
        self._receipt_id=uuid4().hex

    def _receipt(self,phase,**facts):
        # This directory is outside task mounts. A successful write cannot be forged by Agent stdout.
        self._receipt_path.parent.mkdir(parents=True,exist_ok=True)
        with self._receipt_path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'resource_id':self._receipt_id,'phase':phase,**facts})+'\n')
            stream.flush()
            os.fsync(stream.fileno())

    async def start(self,*args,**kwargs):
        self._receipt('start_intent')
        await super().start(*args,**kwargs)
        self._receipt('started')

    async def stop(self,delete):
        await super().stop(delete)
        self._receipt('stopped',deleted=delete is True)


def cleanup_state(receipts):
    resources={}
    for row in receipts:
        resources.setdefault(row['resource_id'],[]).append(row)
    if not resources:
        return 'unknown'
    return 'clean' if all(any(row['phase']=='start_intent' for row in rows) and rows[-1]['phase']=='stopped' and rows[-1].get('deleted') is True
        for rows in resources.values()) else 'unknown'
