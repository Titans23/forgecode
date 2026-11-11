"""Provider-directed delays and cancellable adapters share one retry policy."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random


def retry_delay(retry_number: int, error=None) -> float | None:
    """Honor Retry-After up to 60s; a longer request stops automatic retry.

    Never shorten a server-directed wait. Missing/invalid headers use local
    exponential backoff; adapters own the attempt limit and cancellation.
    """
    response = getattr(error, 'response', None)
    headers = getattr(response, 'headers', {})
    delay = None
    for name, scale in (('retry-after-ms', .001), ('retry-after', 1)):
        value = headers.get(name)
        if value is None:
            continue
        try:
            delay = float(value) * scale
        except ValueError:
            if name == 'retry-after':
                try:
                    date = parsedate_to_datetime(value)
                    delay = (date - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    pass
        if delay is not None and math.isfinite(delay) and delay > 0:
            return delay if delay <= 60 else None
        delay = None
    base = min(.5 * 2 ** (retry_number - 1), 8.0)
    return base + random.uniform(0, base * .25)
