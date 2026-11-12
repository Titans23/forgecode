"""External operation deadlines; independent of model and tool usage."""
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
