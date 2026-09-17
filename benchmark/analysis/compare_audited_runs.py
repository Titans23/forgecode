"""Compare audited telemetry without executing tasks or contacting a model."""

import argparse
import json
from pathlib import Path


def totals(rows):
    return {
        'tasks': len(rows), 'passes': sum(r['reward'] == 1 for r in rows),
        'agent_seconds': sum(r['statistics']['elapsed_seconds'] for r in rows),
        'model_requests': sum(r['statistics']['model_requests'] for r in rows),
        'tool_requests': sum(r['statistics']['tool_requests'] for r in rows),
        'input_tokens_including_cache': sum(sum(r['usage'].get(k, 0) for k in (
            'input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')) for r in rows),
        'output_tokens': sum(r['usage']['output_tokens'] for r in rows),
        'unknown_usage_requests': sum(r['statistics']['unknown_usage_requests'] for r in rows),
        'journal_bytes': sum(r['journal_bytes'] for r in rows),
        'adjacent_bookkeeping_gap_seconds': sum(r['adjacent_bookkeeping_gaps']['sum_seconds'] for r in rows),
        'verify_calls': sum(r['tools'].get('verify', 0) for r in rows),
        'review_delivery_calls': sum(r['tools'].get('review_delivery', 0) for r in rows),
    }


def compare(old, new):
    a = {r['task']: r for r in old}
    b = {r['task']: r for r in new}
    if set(a) != set(b):
        raise ValueError('Task sets differ')
    service_faults = {r['task'] for r in old + new if r['stop_reason'] == 'server_error'}
    paired = sorted(set(a) - service_faults)
    return {
        'all_tasks': {'old': totals(old), 'new': totals(new)},
        'common_without_terminal_server_error': {
            'tasks': paired,
            'old': totals([a[t] for t in paired]),
            'new': totals([b[t] for t in paired]),
            'note': 'Diagnostic paired subset only; does not replace the raw 10-task denominator.',
        },
        'per_task': [{
            'task': t,
            **{label: {'reward': r['reward'], 'seconds': r['statistics']['elapsed_seconds'],
                       'model_requests': r['statistics']['model_requests'],
                       'tool_requests': r['statistics']['tool_requests'],
                       'stop_reason': r['stop_reason']}
               for label, r in [('old', a[t]), ('new', b[t])]},
        } for t in sorted(a)],
        'limitations': [
            'Code and concurrency changed together; one trial per task cannot identify causality.',
            'Request token totals omit unknown usage; cached tokens are not equivalent to uncached cost.',
            'Adjacent event gaps include scheduling and runtime work, not exclusively journal I/O.',
        ],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('old', type=Path)
    parser.add_argument('new', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = compare(json.loads(args.old.read_text(encoding='utf-8')),
                     json.loads(args.new.read_text(encoding='utf-8')))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result['all_tasks'], indent=2))
