"""Offline persistence cost probe; compares production and bounded-tail heads.

The baseline retains the pre-repair whole-file head validation.
Both arms preserve locking, UUID/sequence checks, and fsync in SessionJournal.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

from forge.sessions.store import SessionError, SessionJournal


class LegacyHeadJournal(SessionJournal):
    """Pre-repair whole-file validation, with identical append locking/fsync."""
    def _assert_append_head(self):
        if not self.path.exists():
            if self.sequence or self.parent_uuid is not None:
                raise SessionError('Missing journal')
            return
        lines = self.path.read_text(encoding='utf-8').splitlines()
        durable = json.loads(next(line for line in reversed(lines) if line.strip()))
        if durable.get('sequence') != self.sequence or durable.get('uuid') != self.parent_uuid:
            raise SessionError('Changed journal')


def run_arm(root, cls, count, payload_bytes):
    path = root / (cls.__name__ + '.jsonl')
    journal = cls(path, session_id='session-' + '0' * 24, project_root=root)
    journal.bytes_read = 0
    original = Path.open

    class CountedReader:
        def __init__(self, stream):
            self.stream = stream
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def __enter__(self):
            self.stream.__enter__()
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def read(self, *args):
            data = self.stream.read(*args)
            journal.bytes_read += len(data.encode('utf-8') if isinstance(data, str) else data)
            return data

    def measured_open(p, mode='r', *args, **kwargs):
        stream = original(p, mode, *args, **kwargs)
        return CountedReader(stream) if p == path and 'r' in mode else stream

    durations = []
    with patch.object(Path, 'open', measured_open):
        for i in range(count):
            start = perf_counter()
            journal.append('audit_probe', {'index': i, 'text': 'x' * payload_bytes})
            durations.append(perf_counter() - start)
    # A stale writer must still fail, in both arms.
    stale = cls(path, session_id=journal.session_id, project_root=root)
    stale.bytes_read = 0
    try:
        stale.append('audit_probe', {})
    except SessionError:
        refused_stale = True
    else:
        refused_stale = False
    return {'arm': cls.__name__, 'events': count, 'journal_bytes': path.stat().st_size,
            'head_bytes_read': journal.bytes_read, 'seconds': sum(durations),
            'first_20_median_ms': median(durations[:20]) * 1000,
            'last_20_median_ms': median(durations[-20:]) * 1000,
            'stale_writer_refused': refused_stale}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--events', type=int, default=500)
    args = parser.parse_args()
    root = Path('benchmark/.cache/audit-perf') / uuid4().hex[:8]
    root.mkdir(parents=True)
    results = [run_arm(root, cls, args.events, 20000) for cls in (LegacyHeadJournal, SessionJournal)]
    args.output.write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))
