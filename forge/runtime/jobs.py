'''Session-owned command jobs; no daemon, restart, or automatic replay.'''
from __future__ import annotations

import asyncio
import codecs
from collections import deque
from dataclasses import asdict, dataclass, field
from time import monotonic
from uuid import uuid4

from forge.tool_types import ToolResult
from forge.process_output import process_output


@dataclass
class CommandJob:
    id: str
    call_id: str
    owner: object
    command: str
    origin_turn_id: str | None = None
    state: str = 'queued'
    task: asyncio.Task | None = None
    result: ToolResult | None = None
    started_at: float = field(default_factory=monotonic)
    finished_at: float | None = None
    chunks: deque = field(default_factory=deque)
    cursor: int = 0
    characters: int = 0
    discarded_characters: int = 0
    record_error: str | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    decoders: dict = field(default_factory=lambda: {name: codecs.getincrementaldecoder('utf-8')('replace')
                                                   for name in ('stdout', 'stderr')})

    def feed(self, stream, data, *, final=False):
        text = self.decoders[stream].decode(data, final=final)
        if not text:
            return
        self.cursor += 1
        self.chunks.append({'cursor': self.cursor, 'stream': stream, 'text': text})
        self.characters += len(text)
        while self.characters > 65536 and len(self.chunks) > 1:
            removed = len(self.chunks.popleft()['text'])
            self.characters -= removed
            self.discarded_characters += removed
        if self.characters > 65536:
            self.discarded_characters += self.characters - 65536
            self.chunks[0]['text'] = self.chunks[0]['text'][-65536:]
            self.characters = 65536
        self.changed.set()


class CommandJobs:
    def __init__(self, *, limit=10, journal=None):
        self.jobs = {}
        self.journal = journal
        self._slots = asyncio.Semaphore(limit)
        self._closing = None

    @property
    def running(self):
        return any(job.state in {'queued', 'running'} for job in self.jobs.values())

    def _record(self, kind, payload):
        if self.journal is not None:
            self.journal.append(kind, payload)

    def require_idle(self):
        if self.running:
            raise RuntimeError('Stop running command jobs before changing session history or permissions.')

    def bind(self, journal):
        if journal is not self.journal:
            self.require_idle()
            self.jobs.clear()
            self.journal = journal

    def start(self, call, operation, *, owner):
        if self._closing is not None:
            raise RuntimeError('Command jobs are closing')
        job = CommandJob('job-' + uuid4().hex, call.id, owner, call.arguments['command'],
                         origin_turn_id=getattr(self.journal, 'turn_id', None))
        self._record('job_started', {'job_id': job.id, 'tool_call_id': call.id,
                                    'command': job.command, 'state': 'queued', 'origin_turn_id': job.origin_turn_id})
        self.jobs[job.id] = job
        job.task = asyncio.create_task(self._run(job, operation))
        return job.id

    def _finished(self, job):
        try:
            self._record('job_finished', {'job_id': job.id, 'tool_call_id': job.call_id,
                'origin_turn_id': job.origin_turn_id,
                'state': job.state, 'elapsed_seconds': job.finished_at - job.started_at,
                'result': asdict(job.result), 'output': list(job.chunks), 'next_cursor': job.cursor,
                'output_truncated': job.discarded_characters > 0})
        except Exception as error:
            job.record_error = type(error).__name__
            job.state = 'indeterminate'
            job.result = ToolResult.fail('INDETERMINATE', 'Job completed without a durable terminal record; inspect before retrying.')

    async def _run(self, job, operation):
        token = process_output.set(job.feed)
        try:
            async with self._slots:
                job.state = 'running'
                job.changed.set()
                outcome = await operation()
                job.result = outcome.result
                job.state = ('indeterminate' if outcome.record.status == 'indeterminate' else
                             'cancelled' if outcome.record.status == 'cancelled' else
                             'completed' if job.result.success else 'failed')
        except asyncio.CancelledError:
            job.state = 'cancelled'
            job.result = ToolResult.fail('execution_cancelled', 'Background command cancelled.')
        except Exception as error:
            job.state = 'indeterminate'
            job.result = ToolResult.fail('INDETERMINATE', f'Background command failed: {type(error).__name__}')
        finally:
            process_output.reset(token)
            for stream in job.decoders:
                job.feed(stream, b'', final=True)
            job.finished_at = monotonic()
            job.changed.set()
            self._finished(job)

    async def output(self, identity, *, after=0, wait_seconds=0):
        job = self.jobs.get(identity)
        if job is None:
            return ToolResult.fail('unknown_job', 'This job is not owned by the live session. Jobs are not replayed on resume.')
        if after > job.cursor:
            return ToolResult.fail('invalid_cursor', 'Output cursor is beyond the recorded job output.')
        if job.state in {'queued', 'running'} and after == job.cursor and wait_seconds:
            job.changed.clear()
            try:
                await asyncio.wait_for(job.changed.wait(), wait_seconds)
            except TimeoutError:
                pass
        chunks = [chunk for chunk in job.chunks if chunk['cursor'] > after]
        text = ''.join(f"[{chunk['stream']}] {chunk['text']}" for chunk in chunks)
        truncated = job.discarded_characters > 0
        metadata = {'job_id': job.id, 'state': job.state, 'next_cursor': job.cursor,
                    'output_truncated': truncated, 'elapsed_seconds': (job.finished_at or monotonic()) - job.started_at,
                    'exit_code': job.result.metadata.get('exit_code') if job.result else None}
        if job.result is not None:
            metadata['execution_result'] = {'success': job.result.success, 'summary': job.result.summary,
                'error_code': job.result.error.code if job.result.error else None}
            metadata['output_truncated'] |= bool(job.result.metadata.get('stdout_truncated') or job.result.metadata.get('stderr_truncated'))
            if not text and after == 0:
                text = job.result.content
        if job.state in {'failed', 'cancelled', 'indeterminate'}:
            return ToolResult.fail('INDETERMINATE' if job.state == 'indeterminate' else 'background_command_' + job.state,
                                   f'Background command {job.state}.',
                                   content=text, metadata=metadata)
        return ToolResult.ok(f'Background command {job.state}.', content=text, metadata=metadata)

    async def cancel(self, identity):
        job = self.jobs.get(identity)
        if job is None:
            return ToolResult.fail('unknown_job', 'This job is not owned by the live session.')
        if not job.task.done():
            job.task.cancel()
        await asyncio.gather(job.task, return_exceptions=True)
        if job.result is None and job.task.cancelled():
            job.state = 'cancelled'
            job.finished_at = monotonic()
            job.result = ToolResult.fail('execution_cancelled', 'Job cancelled before dispatch.')
            job.changed.set()
            self._finished(job)
        result = await self.output(identity)
        if job.state == 'cancelled':
            return ToolResult.ok('Background command cancelled; partial effects may remain.',
                                 content=result.content, metadata=result.metadata)
        return result

    async def cancel_owner(self, owner):
        await asyncio.gather(*(self.cancel(job.id) for job in self.jobs.values()
                               if job.owner is owner and not job.task.done()))

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        return await asyncio.shield(self._closing)

    async def _close(self):
        await asyncio.gather(*(self.cancel(job.id) for job in self.jobs.values()))
        if any(job.record_error for job in self.jobs.values()):
            raise RuntimeError('Background job journal settlement failed')
