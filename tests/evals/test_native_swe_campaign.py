"""Campaign admission/accounting faults with no model/network calls."""
import asyncio
import json
from types import SimpleNamespace

import pytest

import evals.native_swe_campaign as campaign


def result(*, passed=False, requests=2, reason='', complete=True):
    return {'passed': passed, 'task_status': 'completed' if passed else 'partial',
        'cleanup': {'state': 'clean'}, 'usage': {'input_tokens': 10, 'output_tokens': 2},
        'agent_seconds': 3, 'total_seconds': 5, 'statistics': {'tool_requests': 4},
        'request_evidence': {'observed_requests': requests, 'usage_complete': complete,
            'reported_usage': {'input_tokens': 10, 'output_tokens': 2},
            'request_outcomes': [{'outcome': 'failed' if reason else 'completed', 'reason': reason}]}}


def test_failures_stay_in_denominator_and_unknown_tokens_are_not_zero():
    slots = [
        {'slot': 1, 'task': 'a', 'state': 'finished', 'outcome': result(passed=True)},
        {'slot': 2, 'task': 'b', 'state': 'finished', 'outcome': result(reason='rate_limit', complete=False)},
        {'slot': 3, 'task': 'a', 'state': 'finished', 'outcome': result(passed=True)},
        {'slot': 4, 'task': 'b', 'state': 'not_started'}]
    summary = campaign.summarize(slots)
    assert summary['attempted'] == 3 and summary['complete'] == 2 and summary['remaining'] == 1
    assert summary['completion_rate'] == 2 / 3
    assert len(summary['successful_costs']) == 2
    assert len(summary['two_success_repeat_pairs']) == 1
    assert summary['all_attempts_reported_tokens_lower_bound']['input_tokens'] == 30
    assert campaign.costs(slots[1]['outcome'])['tokens'] is None
    assert campaign.stop_reason(slots[1]['outcome']) == 'rate_limit'


def test_durable_admission_stops_at_rate_limit_and_never_replays_slot(tmp_path, monkeypatch):
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text('{}')
    plan = {'status': 'authorized_bounded_execution', 'per_run': {}, 'schedule': [
        {'slot': index, 'task': 'a', 'repeat': index} for index in (1, 2, 3)]}
    monkeypatch.setattr(campaign, 'validate_inputs', lambda *args: (plan, {'cases': [{'id': 'a'}]}, object()))
    monkeypatch.setattr(campaign, 'model_identity', lambda config: {'model_id': 'scripted'})
    monkeypatch.setattr(campaign, 'load_rows', lambda cache: {'a': {}})
    monkeypatch.setattr(campaign.subprocess, 'check_output',
                        lambda args, **kwargs: '' if 'status' in args else 'frozen-commit')
    monkeypatch.setattr(campaign, 'NativeTaskAdapter',
                        lambda *args: SimpleNamespace(case=lambda index, limits: index))
    output = tmp_path / 'out'
    calls = []
    async def execute(adapter, case, **kwargs):
        # No call can happen before its admission record has reached disk.
        persisted = json.loads((output / 'campaign.json').read_text())
        assert persisted['slots'][case - 1]['state'] == 'started'
        calls.append(case)
        value = result(passed=case != 2, reason='rate_limit' if case == 2 else '')
        return SimpleNamespace(to_dict=lambda: value, passed=value['passed'],
                               request_evidence=value['request_evidence'])
    monkeypatch.setattr(campaign, 'run_native_case', execute)
    asyncio.run(campaign.campaign(plan_path, tmp_path, output))
    assert calls == [1, 2]
    with pytest.raises(ValueError, match='explicit authorized resume'):
        asyncio.run(campaign.campaign(plan_path, tmp_path, output))
    asyncio.run(campaign.campaign(plan_path, tmp_path, output, resume=True))
    assert calls == [1, 2, 3]
    state = json.loads((output / 'campaign.json').read_text())
    assert state['state'] == 'complete' and state['summary']['complete'] == 2
    assert state['slots'][1]['outcome']['passed'] is False


def test_interrupted_admission_requires_audit_even_with_resume(tmp_path, monkeypatch):
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text('{}')
    monkeypatch.setattr(campaign, 'validate_inputs', lambda *args: (
        {'status': 'authorized_bounded_execution'}, {}, object()))
    monkeypatch.setattr(campaign, 'model_identity', lambda config: {})
    monkeypatch.setattr(campaign.subprocess, 'check_output',
                        lambda args, **kwargs: '' if 'status' in args else 'commit')
    output = tmp_path / 'out'
    output.mkdir()
    state = {'freeze': {'runtime_commit': 'commit', 'plan_sha256': campaign.digest(plan_path), 'model': {}},
             'state': 'blocked', 'slots': [{'slot': 1, 'task': 'a', 'state': 'started'}]}
    campaign.save(output / 'campaign.json', state)
    with pytest.raises(ValueError, match='will not be replayed'):
        asyncio.run(campaign.campaign(plan_path, tmp_path, output, resume=True))
