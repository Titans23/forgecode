"""Monotonic and UTC deadlines plus ownership-scoped cleanup; no replay/recovery kills."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from time import monotonic


def now_utc():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Deadline:
    seconds: float
    started_monotonic: float
    expires_utc: datetime

    @classmethod
    def start(cls, seconds, *, utc=None, clock=None):
        if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('Deadline duration must be finite and positive')
        return cls(seconds, monotonic() if clock is None else clock, (utc or now_utc()) + timedelta(seconds=seconds))

    def remaining(self, *, utc=None, clock=None):
        return max(0, min(self.seconds, self.seconds - ((monotonic() if clock is None else clock) - self.started_monotonic),
            (self.expires_utc - (utc or now_utc())).total_seconds()))

    @property
    def utc_text(self):
        return self.expires_utc.isoformat().replace('+00:00', 'Z')

    async def watch(self, task, expire):
        while not task.done():
            remaining = self.remaining()
            if remaining <= 0:
                expire()
                task.cancel()
                return
            await asyncio.sleep(min(0.05, remaining))


class PhaseLifecycle:
    """Keep explicitly retained delivery resources through grading, within final expiry.

    Close callbacks belong to live trusted handles. IDs/PIDs loaded from history
    are never sufficient to register or kill a resource.
    """
    def __init__(self, *, agent_seconds, environment_seconds, store=None, turn_id=None):
        if environment_seconds < agent_seconds:
            raise ValueError('Environment lifetime cannot be shorter than agent budget')
        self.agent = Deadline.start(agent_seconds)
        self.environment = Deadline.start(environment_seconds)
        self.grader = None
        self.agent_state = 'running'
        self.grade_state = 'unscored'
        self.grade_result = None
        self.resources = {}
        self.cleanup = None
        self._reports = {}
        self._closing = None
        self._environment_watch = None
        self._agent_task = None
        self._grader_task = None
        self._resource_lock = asyncio.Lock()
        self._store, self._turn_id = store, turn_id
        if (store is None) != (turn_id is None):
            raise ValueError('Persistent phases require a store and an existing turn')
        if store is not None:
            with store.transaction():
                updated = store.connection.execute('INSERT INTO turn_lifecycle(turn_id,owner_epoch,agent_deadline_utc,environment_deadline_utc,cancel_state,cleanup_state) '
                    'VALUES(?,?,?,?,?,?) ON CONFLICT(turn_id) DO UPDATE SET agent_deadline_utc=excluded.agent_deadline_utc, '
                    'environment_deadline_utc=excluded.environment_deadline_utc WHERE owner_epoch=excluded.owner_epoch',
                    (turn_id, store.epoch, self.agent.utc_text, self.environment.utc_text, 'none', 'pending'))
                if updated.rowcount != 1:
                    raise ValueError('Lifecycle belongs to a previous owner; reconcile first')

    def _persist(self, *, grader=False, cleanup=False):
        if self._store is None:
            return
        from forge.engine.persistence import encoded, utc_now
        with self._store.transaction():
            if grader:
                updated = self._store.connection.execute('UPDATE turn_lifecycle SET grader_deadline_utc=? WHERE turn_id=? AND owner_epoch=?',
                    (self.grader.utc_text, self._turn_id, self._store.epoch))
            else:
                updated = self._store.connection.execute('UPDATE turn_lifecycle SET cleanup_state=?,cleanup_json=?,finished_at_utc=? WHERE turn_id=? AND owner_epoch=?',
                    (self.cleanup['state'], encoded(self.cleanup), utc_now(), self._turn_id, self._store.epoch))
            if updated.rowcount != 1:
                raise ValueError('Lifecycle ownership changed; no historical process recovery')

    def _watch_environment(self):
        if self._environment_watch is None:
            async def watch():
                while self.environment.remaining() > 0:
                    await asyncio.sleep(min(0.05, self.environment.remaining()))
                if self._agent_task is not None and not self._agent_task.done():
                    self._agent_task.cancel()
                if self._grader_task is not None and not self._grader_task.done():
                    self._grader_task.cancel()
                await self.close()
            self._environment_watch = asyncio.create_task(watch())

    async def run_agent(self, operation):
        if self.agent_state != 'running' or self._agent_task is not None:
            raise ValueError('Agent phase can run once')
        self._watch_environment()
        self._agent_task = asyncio.create_task(operation())
        timer = asyncio.create_task(self.agent.watch(self._agent_task,
            lambda: setattr(self, 'agent_state', 'deadline_exceeded')))
        try:
            return await self._agent_task
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
            await self.finish_agent()

    def register(self, resource_id, close, *, retain_for_grader=False):
        if self._closing is not None or self.environment.remaining() <= 0 or resource_id in self.resources or resource_id in self._reports or not callable(close):
            raise ValueError('Resource must have one live owner and a unique ID')
        self.resources[resource_id] = (close, retain_for_grader)
        self._watch_environment()

    async def finish_agent(self):
        if self.agent_state == 'running':
            self.agent_state = 'finished'
        for identity, (close, retained) in list(self.resources.items()):
            if not retained:
                await self._close_resource(identity, close)

    def begin_grading(self, seconds):
        if self.agent_state == 'running' or self.cleanup is not None or self.grade_state != 'unscored' or self.environment.remaining() <= 0:
            raise ValueError('Grading is unavailable or already started')
        self._watch_environment()
        self.grader = Deadline.start(min(seconds, self.environment.remaining()))
        self.grade_state = 'grading'
        self._persist(grader=True)

    async def grade(self, operation, seconds):
        self.begin_grading(seconds)
        self._grader_task = asyncio.create_task(operation())
        timer = asyncio.create_task(self.grader.watch(self._grader_task, lambda: None))
        try:
            self.grade_result = await self._grader_task
            self.grade_state = 'graded'
        except BaseException:
            self.grade_state = 'grader_error'
            raise
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
            await self.close()
        return self.grade_result

    async def _close_resource(self, identity, close):
        async with self._resource_lock:
            if identity in self._reports:
                return self._reports[identity]
            try:
                result = await asyncio.wait_for(close(), 3)
                if not isinstance(result, dict) or result.get('state') not in ('clean', 'residual', 'unknown'):
                    result = {'state': 'unknown', 'reason': 'invalid_cleanup_observation'}
            except (Exception, asyncio.CancelledError) as error:
                result = {'state': 'unknown', 'reason': type(error).__name__}
            self.resources.pop(identity, None)
            self._reports[identity] = result
            return result

    async def _close(self):
        for identity, (callback, _) in list(self.resources.items()):
            await self._close_resource(identity, callback)
        states = {value['state'] for value in self._reports.values()}
        self.cleanup = {'state': 'unknown' if 'unknown' in states else 'residual' if 'residual' in states else 'clean',
            'resources': dict(self._reports)}
        self._persist(cleanup=True)
        if self._environment_watch is not None and self._environment_watch is not asyncio.current_task():
            self._environment_watch.cancel()
        return self.cleanup

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        return await asyncio.shield(self._closing)
