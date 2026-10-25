"""One frozen, serial campaign using the shared native task adapter and Journal."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

from dotenv import dotenv_values

from evals.native_swe import ROOT, NativeTaskAdapter, run_native_case
from evals.runner import EvalOutcome
from forge.config import ForgeConfig
from forge.sessions.locking import exclusive_append


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def model_identity(config):
    return {key: getattr(config, key) for key in (
        'provider', 'model_id', 'max_tokens', 'context_window',
        'request_timeout_seconds', 'reasoning_effort')} | {
        'endpoint_sha256': hashlib.sha256(config.base_url.encode()).hexdigest()}


def validate_scope(plan, manifest):
    if 'purpose' in plan:
        expected_limits = {
            'max_model_calls': 18, 'max_tool_calls': 48, 'max_turn_seconds': 900,
            'verifier_timeout_seconds': 600, 'intent_routing': False, 'sandbox': 'workspace-write',
            'require_task_verification': True, 'require_positive_verification': True}
        expected_schedule = [
            {'slot': 1, 'task': 'pytest-dev__pytest-5413', 'repeat': 1},
            {'slot': 2, 'task': 'sphinx-doc__sphinx-10325', 'repeat': 1}]
        if (plan['purpose'] != 'seen_task_development_validation'
                or plan.get('runs') != 2 or plan.get('repetitions_per_task') != 1
                or plan.get('concurrency') != 1 or plan.get('total_model_request_cap') != 36
                or plan.get('automatic_retries') != 0 or plan.get('failed_reruns') != 0
                or plan.get('replacement_tasks') != 0 or plan.get('per_run') != expected_limits
                or any(type(plan['per_run'][key]) is not type(value) for key, value in expected_limits.items())
                or plan.get('schedule') != expected_schedule
                or not {slot['task'] for slot in expected_schedule}.issubset({case['id'] for case in manifest['cases']})):
            raise ValueError('Development validation differs from the reviewed two-slot scope')
        return
    # Preserve the original twelve-slot admission contract and task order.
    if (plan['runs'] != 12 or plan['repetitions_per_task'] != 2 or plan['concurrency'] != 1
            or plan['per_run']['max_model_calls'] != 18 or plan['total_model_request_cap'] != 216
            or plan['automatic_retries'] != 0 or plan['failed_reruns'] != 0
            or type(plan['per_run'].get('max_delivery_repairs', 0)) is not int
            or plan['per_run'].get('max_delivery_repairs', 0) != 0
            or plan['per_run']['sandbox'] != 'workspace-write' or plan['per_run']['intent_routing']):
        raise ValueError('Campaign exceeds the authorized frozen execution scope')
    expected = [(case['id'], repetition) for repetition in (1, 2) for case in manifest['cases']]
    if [(slot['task'], slot['repeat']) for slot in plan['schedule']] != expected or len(expected) != 12:
        raise ValueError('Schedule differs from frozen six-task order')
    if [slot['slot'] for slot in plan['schedule']] != list(range(1, 13)):
        raise ValueError('Duplicate or missing slots')


def validate_inputs(plan_path, cache):
    if sys.platform != 'win32':
        raise ValueError('This frozen native task environment is verified for Windows only')
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    manifest_path = ROOT / plan['task_manifest']
    if digest(manifest_path) != plan['task_manifest_sha256']:
        raise ValueError('Frozen task manifest changed')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    # Deliberately no user configuration or inherited credential/model merge.
    config = ForgeConfig.from_env(dotenv_values(ROOT / '.env'))
    if model_identity(config) != plan['model']:
        raise ValueError('Project model configuration differs from frozen plan')
    validate_scope(plan, manifest)
    for environment in manifest['environments'].values():
        if digest(ROOT / environment['requirements']) != environment['sha256']:
            raise ValueError('Frozen dependencies changed')
    for case in manifest['cases']:
        if digest(ROOT / case['suite']) != case['suite_sha256']:
            raise ValueError('Frozen test mapping changed')
    if digest(cache / 'test.parquet') != manifest['dataset']['sha256']:
        raise ValueError('Frozen dataset changed')
    return plan, manifest, config


def stop_reason(outcome):
    if (outcome.get('cleanup') or {}).get('state') != 'clean':
        return 'unconfirmed_cleanup_or_admission_failure'
    independent = outcome.get('independent_grading') or {}
    if independent.get('cleanup', {'state': 'clean'}).get('state') != 'clean':
        return 'unconfirmed_verifier_cleanup'
    evidence = outcome.get('request_evidence')
    if evidence is None:
        return 'request_accounting_unavailable'
    if evidence.get('unfinished_request_ids'):
        return 'unfinished_model_request'
    for request in evidence.get('request_outcomes', []):
        if request.get('reason') in {'rate_limit', 'timeout', 'stream_interrupted', 'network_error'}:
            return request['reason']
        if request.get('outcome') not in {'success', 'completed'}:
            return 'model_request_failed:' + str(request.get('reason'))
    if evidence.get('accounting_complete') is not True:
        return 'request_accounting_unavailable'
    if outcome.get('error'):
        return 'runner_error'
    if outcome.get('task_status') == 'cancelled':
        return 'cancelled'
    return None


def costs(outcome):
    evidence = outcome.get('request_evidence') or {}
    accounted = evidence.get('accounting_complete') is True
    return {'requests': evidence.get('observed_requests') if accounted else None,
            'reported_requests_lower_bound': evidence.get('observed_requests'),
            'tool_requests': (outcome.get('statistics') or {}).get('tool_requests'),
            'agent_seconds': outcome.get('agent_seconds'), 'total_seconds': outcome.get('total_seconds'),
            'tokens': outcome.get('usage') if accounted and evidence.get('usage_complete') else None,
            'reported_tokens_lower_bound': evidence.get('reported_usage')}


def summarize(slots):
    attempted = [slot for slot in slots if slot['state'] != 'not_started']
    successful = [slot for slot in attempted if slot.get('outcome', {}).get('passed') is True]
    success_costs = [{'slot': slot['slot'], 'task': slot['task'], **costs(slot['outcome'])}
                     for slot in successful]
    pairs = []
    for task in dict.fromkeys(slot['task'] for slot in slots):
        pair = [slot for slot in successful if slot['task'] == task]
        if len(pair) == 2:
            pairs.append({'task': task, 'repeats': [costs(slot['outcome']) for slot in pair]})
    observed = [slot.get('outcome', {}).get('request_evidence') for slot in attempted]
    reported = [item['reported_usage'] for item in observed if item and item.get('reported_usage')]
    return {'attempted': len(attempted), 'complete': len(successful),
            'completion_rate': len(successful) / len(attempted) if attempted else None,
            'remaining': len(slots) - len(attempted),
            'observed_requests_lower_bound': sum((item or {}).get('observed_requests', 0) for item in observed),
            'request_accounting_complete': all(item is not None and item.get('accounting_complete') is True
                                                for item in observed),
            'unfinalized_slots': [slot['slot'] for slot in attempted if slot['state'] != 'finished'],
            'failures': [{'slot': slot['slot'], 'task': slot['task'],
                          'status': slot.get('outcome', {}).get('task_status', 'interrupted'),
                          'stop_reason': slot.get('outcome', {}).get('stop_reason'),
                          'reasons': slot.get('outcome', {}).get('reasons', []),
                          'error': slot.get('outcome', {}).get('error')}
                         for slot in attempted if slot['state'] == 'finished' and slot not in successful],
            'all_attempts_reported_tokens_lower_bound': {
                key: sum(usage[key] for usage in reported) for key in reported[0]} if reported else None,
            'successful_costs': success_costs,
            'successful_medians': {name: statistics.median(values) if values else None
                for name in ('requests', 'tool_requests', 'agent_seconds', 'total_seconds')
                for values in [[entry[name] for entry in success_costs if entry[name] is not None]]},
            'two_success_repeat_pairs': pairs,
            'interpretation': 'Public known tasks, native adaptation; repeats measure variability, not causal improvement.'}


def save(path, state):
    state['summary'] = summarize(state['slots'])
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(state, stream, indent=2, ensure_ascii=False)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    temporary.replace(path)


def load_rows(cache):
    import pyarrow.parquet as parquet
    return {row['instance_id']: row for row in parquet.read_table(cache / 'test.parquet').to_pylist()}


async def campaign(plan_path, cache, output, *, resume=False):
    plan, manifest, config = validate_inputs(plan_path, cache)
    if plan['status'] != 'authorized_bounded_execution':
        raise ValueError('Plan has no verified bounded execution authorization')
    dirty = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)
    if dirty.strip():
        raise ValueError('Commit and verify the runtime before paid execution')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if plan.get('purpose') == 'seen_task_development_validation' and plan.get('runtime_commit') != commit:
        raise ValueError('Development runtime differs from the reviewed commit')
    freeze = {'runtime_commit': commit, 'plan_sha256': digest(plan_path), 'model': model_identity(config)}
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'campaign.json'
    with exclusive_append(path):
        if path.exists():
            state = json.loads(path.read_text(encoding='utf-8'))
            if state['freeze'] != freeze:
                raise ValueError('Existing campaign freeze differs; no execution')
            if any(slot['state'] == 'started' for slot in state['slots']):
                raise ValueError('An interrupted slot needs Journal/cleanup audit; it will not be replayed')
            if state['state'] == 'complete':
                return state['summary']
            if not resume:
                raise ValueError('Stopped campaign requires explicit authorized resume')
        else:
            if resume:
                raise ValueError('Cannot resume a missing campaign')
            state = {'freeze': freeze, 'state': 'ready',
                     'slots': [{**slot, 'state': 'not_started'} for slot in plan['schedule']]}
            save(path, state)
        rows = load_rows(cache)
        specs = {case['id']: case for case in manifest['cases']}
        for slot in state['slots']:
            if slot['state'] != 'not_started':
                continue
            slot['state'] = 'started'
            slot['admitted_at'] = datetime.now(timezone.utc).isoformat()
            state['state'] = 'running'
            save(path, state)  # Durable admission before setup or any model client.
            try:
                adapter = NativeTaskAdapter(manifest, specs[slot['task']], rows[slot['task']], cache,
                                            output / f"slot-{slot['slot']:02d}")
                outcome = await run_native_case(adapter, adapter.case(slot['slot'], plan['per_run']), config=config)
                slot['outcome'] = outcome.to_dict()
                slot['state'] = 'finished'
                reason = stop_reason(slot['outcome'])
                if (outcome.request_evidence or {}).get('observed_requests', 0) > 18:
                    reason = 'request_budget_evidence_violation'
                state['state'] = 'blocked' if reason else 'running'
                state['stop_reason'] = reason
                save(path, state)
                print(json.dumps({'slot': slot['slot'], 'passed': outcome.passed,
                                  'stop_reason': reason, 'summary': state['summary']}, ensure_ascii=False), flush=True)
                if reason:
                    return state['summary']
            except BaseException as error:
                # Keep started ownership for explicit audit; never resubmit it.
                state['state'] = 'blocked'
                state['stop_reason'] = type(error).__name__
                save(path, state)
                raise
        state['state'] = 'complete'
        save(path, state)
        return state['summary']


async def replay_outcome(record, plan_path, cache, output):
    """Regrade a saved candidate in a fresh verifier; never rerun the Agent."""
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    manifest_path = ROOT / plan['task_manifest']
    if digest(manifest_path) != plan['task_manifest_sha256']:
        raise ValueError('Frozen manifest changed')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if digest(cache / 'test.parquet') != manifest['dataset']['sha256']:
        raise ValueError('Frozen dataset changed')
    original = json.loads(record.read_text(encoding='utf-8'))
    grading = original['independent_grading']
    candidate = grading['candidate']
    if digest(candidate['path']) != candidate['sha256']:
        raise ValueError('Saved candidate digest changed')
    spec = next(case for case in manifest['cases'] if case['id'] == grading['instance_id'])
    if digest(ROOT / spec['suite']) != spec['suite_sha256']:
        raise ValueError('Frozen suite changed')
    adapter = NativeTaskAdapter(manifest, spec, load_rows(cache)[spec['id']], cache, output)
    adapter.candidate = json.loads(Path(candidate['path']).read_text(encoding='utf-8'))
    outcome = EvalOutcome(case_id='candidate-replay')
    outcome.independent_grading = {'input_audit': grading['input_audit']}
    await adapter.grade(adapter.case(1, plan['per_run']), None, None, outcome)
    report = {'original_outcome': str(record), 'candidate_sha256': candidate['sha256'],
              'model_api_calls': 0, 'independent_grading': outcome.independent_grading,
              'reasons': outcome.reasons,
              'scope': 'Functional candidate replay only; original completion status and failed attempt remain unchanged.'}
    (output / 'replay.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=ROOT / 'experiments/native-swe-lite-plan.json')
    parser.add_argument('--cache', type=Path, default=ROOT / '.local/implementation/real-repo-preflight')
    parser.add_argument('--output', type=Path, default=ROOT / '.local/implementation/native-swe-live')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--replay-outcome', type=Path)
    args = parser.parse_args()
    if args.replay_outcome is not None:
        if args.execute or args.resume:
            parser.error('Candidate replay cannot start or resume a model campaign')
        print(json.dumps(asyncio.run(replay_outcome(args.replay_outcome, args.plan, args.cache, args.output))))
        return
    if not args.execute:
        plan, manifest, config = validate_inputs(args.plan, args.cache)
        print(json.dumps({'validation': 'passed_no_model_calls', 'tasks': len(manifest['cases']),
                          'slots': plan['runs'], 'model': model_identity(config)}))
        return
    print(json.dumps(asyncio.run(campaign(args.plan, args.cache, args.output, resume=args.resume))))


if __name__ == '__main__':
    main()
