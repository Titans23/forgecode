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
        # Pinned Harbor logs and suppresses compose stop/down failures.
        self._receipt('stop_returned',delete_requested=delete is True)


def cleanup_state(receipts):
    # Neither legacy `stopped/deleted` nor `stop_returned` receipts contain an
    # independently observed inventory of remaining owned Docker resources.
    # Keep them unknown until actual owner reconciliation is implemented.
    return 'unknown'
