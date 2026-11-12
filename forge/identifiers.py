"""Stable identifiers, UTC timestamps and canonical JSON shared by stored formats."""
from datetime import datetime, timezone
import json
from uuid import uuid4


def new_id(prefix):
    return f'{prefix}-{uuid4()}'


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
