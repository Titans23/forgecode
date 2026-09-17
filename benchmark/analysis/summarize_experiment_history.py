"""Read-only historical inventory and explicit latest-attempt reconciliation."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from benchmark.harbor.summarize import _trial_payloads, assess_trial


def native(path):
    text = str(Path(path).resolve())
    return Path('\\\\?\\' + text) if os.name == 'nt' and not text.startswith('\\\\?\\') else Path(text)


def display(path):
    return str(path).removeprefix('\\\\?\\')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def find_jobs(directory, depth=0):
    config = directory / 'config.json'
    if config.is_file():
        value = read(config)
        if 'agents' in value and ('datasets' in value or 'tasks' in value):
            yield directory, value
            return
    if depth >= 3:
        return
    for child in sorted(directory.iterdir()):
        if child.is_dir() and not child.name.startswith(('.', 'paused-', 'baselines')):
            yield from find_jobs(child, depth + 1)


def trial_row(directory, detailed=False):
    result = read(directory / 'result.json')
    reward = (result.get('verifier_result') or {}).get('rewards') or {}
    row = {'task': directory.name.split('__')[0], 'reward': reward.get('reward'),
           'exception': (result.get('exception_info') or {}).get('exception_type'),
           'finished_at': result.get('finished_at'), 'path': display(directory)}
    if detailed:
        payloads = _trial_payloads(directory)
        row['assessment'] = asdict(assess_trial(directory, result, payloads))
        row['stop_reasons'] = sorted({p['stop_reason'] for p in payloads if p.get('stop_reason')})
        row['internal_statuses'] = sorted({p['status'] for p in payloads if p.get('status')})
        row['model_calls'] = sum(p.get('model_calls', 0) for p in payloads)
        row['tool_calls'] = sum(p.get('tool_calls', 0) for p in payloads)
        row['usage'] = dict(sum((Counter(p.get('usage') or {}) for p in payloads), Counter()))
    return row


def totals(rows):
    return {'count': len(rows), 'pass': sum(r['reward'] == 1 for r in rows),
            'zero': sum(r['reward'] == 0 for r in rows),
            'missing_reward': sum(r['reward'] is None for r in rows),
            'stop_reasons': dict(Counter(reason for r in rows for reason in r.get('stop_reasons', []))),
            'model_calls': sum(r.get('model_calls', 0) for r in rows),
            'tool_calls': sum(r.get('tool_calls', 0) for r in rows)}


def combine(paths):
    combined = {}
    for path in paths:
        for directory in native(path).iterdir():
            if directory.is_dir() and (directory / 'config.json').is_file() and (directory / 'result.json').is_file():
                row = trial_row(directory, True)
                if row['task'] in combined:
                    raise ValueError('Duplicate completed task: ' + row['task'])
                combined[row['task']] = row
    return combined


def main():
    inventory = []
    for job, config in find_jobs(native(ROOT / 'benchmark/runs/harbor')):
        trials = [d for d in job.iterdir() if d.is_dir() and (d / 'config.json').is_file()]
        rows = [trial_row(d) for d in trials if (d / 'result.json').is_file()]
        datasets = config.get('datasets', [])
        filters = [t for d in datasets for t in (d.get('task_names') or [])]
        planned = len(filters) if filters and not any('*' in t or '?' in t for t in filters) else config.get('n_tasks')
        control_file = job.parent / 'controller-state.json'
        control = read(control_file) if control_file.is_file() else {}
        metadata = control.get('metadata') or {}
        source = metadata.get('source_snapshot') or next((a.get('kwargs', {}).get('source_dir') for a in config.get('agents', []) if a.get('kwargs', {}).get('source_dir')), None)
        manifest = native(source).parent / 'manifest.json' if source else None
        digest = read(manifest).get('content_sha256') if manifest and manifest.is_file() else None
        result = read(job / 'result.json') if (job / 'result.json').is_file() else {}
        inventory.append({'run': job.parent.name, 'job': job.name, 'path': display(job),
            'dataset': [d.get('name') or d.get('path') for d in datasets],
            'model': [a.get('model_name') for a in config.get('agents', [])],
            'concurrency': config.get('n_concurrent_trials', 4),
            'planned_from_explicit_config': planned, 'created_trials': len(trials),
            'finished_at': result.get('finished_at'), 'controller_status': control.get('status'),
            'source_digest': digest, 'summary': totals(rows), 'rows': rows})

    original_root = ROOT / 'benchmark/runs/harbor/terminal-bench-2-full89-luna-c5-20260916-110647'
    retry_root = ROOT / 'benchmark/runs/harbor/tb2-se38-c4-0916-173742'
    baseline = combine(read(original_root / 'evaluation-chain.json')['official_job_directories'])
    retry = combine(read(retry_root / 'reevaluation-chain.json')['official_job_directories'])
    selected = {r['task'] for r in read(ROOT / 'docs/reviews/2026-09-16-full89-final-summary.json')['rows']
                if 'server_error' in r['stop_reasons']}
    assert len(baseline) == 89 and len(retry) == 38 and set(retry) == selected
    merged = {**baseline, **retry}  # Latest scheduled attempt, NOT best reward.
    transitions = {'new_passes': [], 'lost_passes': [], 'retained_passes': [], 'still_not_passed': []}
    for task, row in retry.items():
        before, after = baseline[task]['reward'] == 1, row['reward'] == 1
        key = 'retained_passes' if before and after else 'new_passes' if after else 'lost_passes' if before else 'still_not_passed'
        transitions[key].append(task)
    stable = [r for t, r in baseline.items() if t not in retry]
    output = {'generated_at': datetime.now(timezone.utc).isoformat(),
        'method': 'Raw official rewards; preserve old attempts; replacement uses all 38 selected retry results, never maximum reward. Archived/incomplete attempts are excluded from official task scoring.',
        'historical_jobs': inventory, 'baseline89': {'summary': totals(list(baseline.values())), 'rows': list(baseline.values())},
        'retry38': {'summary': totals(list(retry.values())), 'rows': list(retry.values())},
        'unchanged51': totals(stable), 'latest89': {'summary': totals(list(merged.values())), 'rows': list(merged.values())},
        'transitions': transitions,
        'latest_primary_outcomes': dict(Counter(r['assessment']['primary_outcome'] for r in merged.values())),
        'latest_internal_vs_official': dict(Counter('|'.join(r['internal_statuses']) + '/' + str(r['reward']) for r in merged.values()))}
    target = ROOT / 'docs/reviews/2026-09-17-experiment-history-data.json'
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'jobs': len(inventory), **{k: output[k]['summary'] for k in ('baseline89', 'retry38', 'latest89')},
                     'outcomes': output['latest_primary_outcomes'], 'internal_vs_official': output['latest_internal_vs_official'],
                     'output': str(target)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
