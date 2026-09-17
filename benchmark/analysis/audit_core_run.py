"""Read-only telemetry audit of a completed Harbor run (no task code execution)."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from statistics import median


def payload(record, journal):
    if 'payload' in record:
        return record['payload']
    ref = record['payload_ref']
    path = (journal.parent / ref['path']).resolve()
    if not path.is_relative_to(journal.parent.resolve()):
        raise ValueError('Artifact outside journal directory')
    disk_path = Path('\\\\?\\' + str(path)) if os.name == 'nt' else path
    raw = disk_path.read_bytes()
    if sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('Artifact hash mismatch')
    return json.loads(raw)


def seconds(stamp):
    return datetime.fromisoformat(stamp).timestamp()


def audit(root):
    output = []
    for trial in sorted(root.iterdir()):
        if not trial.is_dir() or not (trial / 'result.json').exists():
            continue
        result = json.loads((trial / 'result.json').read_text())
        journal = next(trial.glob('agent/forgecode-state/**/sessions/*.jsonl'), None)
        if journal is None:
            output.append({'task': trial.name, 'missing_journal': True})
            continue
        rows, consumed, reread_bytes, tail_bytes, previous_length = [], 0, 0, 0, 0
        disk_journal = Path('\\\\?\\' + str(journal.resolve())) if os.name == 'nt' else journal
        for line_number, line in enumerate(disk_journal.read_bytes().splitlines(keepends=True), 1):
            record = json.loads(line)
            # Counterfactual legacy full-history reads versus the bounded-tail
            # algorithm. These are estimates, not measured filesystem I/O.
            reread_bytes += consumed
            if consumed:
                tail_bytes += 1 + min(consumed, (previous_length // 65536 + 1) * 65536)
            consumed += len(line)
            previous_length = len(line)
            rows.append((record, payload(record, journal), line_number))
        completed = [p for r, p, _ in rows if r['type'] == 'turn_completed']
        requests = [p for r, p, _ in rows if r['type'] == 'model_request_finished']
        tools = [p for r, p, _ in rows if r['type'] == 'tool_completed']
        checks = [(p['evidence'], n) for r, p, n in rows if r['type'] == 'verification_recorded']
        wires = [json.loads(p['body_utf8']) for r, p, _ in rows if r['type'] == 'model_wire_snapshot']
        terminal = completed[-1] if completed else {}
        gaps = []
        for (r0, _, _), (r1, _, _) in zip(rows, rows[1:]):
            if (r0['type'], r1['type']) in {
                ('tool_requested', 'permission_decided'),
                ('permission_decided', 'tool_started'),
                ('model_input_snapshot', 'model_request_started'),
                ('model_request_started', 'model_wire_snapshot'),
                ('tool_result_message', 'task_state'),
            }:
                gaps.append(seconds(r1['timestamp']) - seconds(r0['timestamp']))
        status_file = trial / 'agent/forgecode-status.json'
        output.append({
            'task': trial.name.split('__')[0],
            'reward': (result.get('verifier_result') or {}).get('rewards', {}).get('reward'),
            'harbor_exception': (result.get('exception_info') or {}).get('exception_type'),
            'shell_status': json.loads(status_file.read_text()) if status_file.exists() else None,
            'status': terminal.get('status'), 'stop_reason': terminal.get('stop_reason'),
            'completion_reasons': terminal.get('completion_reasons'),
            'completion_report': terminal.get('completion_report'),
            'statistics': terminal.get('statistics'), 'usage': terminal.get('usage'),
            'request_outcomes': dict(Counter(p.get('outcome') for p in requests)),
            'request_error_reasons': dict(Counter(p.get('reason') for p in requests if p.get('reason'))),
            'wire_requests': len(wires),
            'wire_top_level_keys': sorted(set().union(*(set(p) for p in wires))),
            'wire_reasoning_settings': sorted({json.dumps({k: p[k] for k in ('thinking', 'output_config', 'reasoning', 'reasoning_effort') if k in p}, sort_keys=True) for p in wires}),
            'wire_max_tokens': sorted({p.get('max_tokens', p.get('max_output_tokens', 0)) for p in wires}),
            'tools': dict(Counter(p['name'] for p in tools)),
            'tool_errors': dict(Counter(p.get('error_code') for p in tools if not p.get('success'))),
            'checks': [{'line': n, 'command': p['command'], 'exit_code': p['exit_code'],
                        'timed_out': p['timed_out'], 'verification_id': p.get('verification_id'),
                        'workspace_revision': p.get('workspace_revision'),
                        'environment_epoch': p.get('environment_epoch'),
                        'limitations': p.get('limitations'), 'evidence_issues': p.get('evidence_issues')} for p, n in checks],
            'journal': str(journal.resolve()), 'journal_events': len(rows),
            'journal_bytes': consumed, 'counterfactual_legacy_head_read_bytes': reread_bytes,
            'algorithm_estimated_head_read_bytes': tail_bytes,
            'request_seconds': round(sum(p.get('duration_seconds', 0) for p in requests), 3),
            'adjacent_bookkeeping_gaps': {'count': len(gaps), 'sum_seconds': round(sum(gaps), 3),
                'median_seconds': round(median(gaps), 3) if gaps else 0,
                'max_seconds': round(max(gaps), 3) if gaps else 0},
            'first_event': rows[0][0]['timestamp'], 'last_event': rows[-1][0]['timestamp'],
        })
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for row in result:
        print(row['task'], row.get('reward'), row.get('status'), row.get('stop_reason'),
              'requests', row.get('wire_requests'), 'journal_MB', round(row.get('journal_bytes', 0)/1e6, 2),
              'bookkeeping_gaps', row.get('adjacent_bookkeeping_gaps'))
