"""Campaign admission/accounting faults with no model/network calls."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import evals.native_swe_campaign as campaign


@pytest.fixture
def development_plan():
    plan = json.loads((campaign.ROOT / 'experiments/native-swe-lite-plan.json').read_text())
    plan.update(purpose='seen_task_development_validation', runs=2, repetitions_per_task=1,
        total_model_request_cap=36, replacement_tasks=0, runtime_commit='reviewed-commit', schedule=[
            {'slot': 1, 'task': 'pytest-dev__pytest-5413', 'repeat': 1},
            {'slot': 2, 'task': 'sphinx-doc__sphinx-10325', 'repeat': 1}])
    plan['per_run'].update(require_task_verification=True, require_positive_verification=True)
    return plan


def test_reviewed_development_and_original_frozen_scopes_are_both_admitted(development_plan):
    manifest = json.loads((campaign.ROOT / 'experiments/native-swe-lite.json').read_text())
    original = json.loads((campaign.ROOT / 'experiments/native-swe-lite-plan.json').read_text())
    campaign.validate_scope(original, manifest)
    campaign.validate_scope(development_plan, manifest)
    original['schedule'][1]['task'] = original['schedule'][0]['task']
    with pytest.raises(ValueError, match='frozen six-task order'):
        campaign.validate_scope(original, manifest)


@pytest.mark.parametrize('repair', [0, 1, 2, False, True, 0.0, None, '0'])
def test_original_campaign_cannot_enable_new_repair_behavior(repair):
    plan=json.loads((campaign.ROOT/'experiments/native-swe-lite-plan.json').read_text())
    manifest=json.loads((campaign.ROOT/'experiments/native-swe-lite.json').read_text())
    plan['per_run']['max_delivery_repairs']=repair
    if type(repair) is int and repair==0:
        campaign.validate_scope(plan,manifest)
    else:
        with pytest.raises(ValueError,match='authorized frozen execution scope'):
            campaign.validate_scope(plan,manifest)


@pytest.mark.parametrize('field,value', [
    ('purpose', 'unreviewed'), ('runs', 3), ('repetitions_per_task', 2), ('concurrency', 2),
    ('total_model_request_cap', 37), ('automatic_retries', 1), ('failed_reruns', 1), ('replacement_tasks', 1),
    ('per_run.max_model_calls', 19), ('per_run.max_tool_calls', 49), ('per_run.max_turn_seconds', 901),
    ('per_run.verifier_timeout_seconds', 601), ('per_run.intent_routing', True), ('per_run.sandbox', 'full-access'),
    ('per_run.require_task_verification', False), ('per_run.require_positive_verification', False),
    ('per_run.require_task_verification', 1), ('per_run.max_delivery_repairs', 2)])
def test_development_admission_rejects_budget_or_policy_drift(development_plan, field, value):
    parts = field.split('.')
    container = development_plan['per_run'] if len(parts) == 2 else development_plan
    container[parts[-1]] = value
    manifest = {'cases': [{'id': slot['task']} for slot in development_plan['schedule']]}
    with pytest.raises(ValueError, match='reviewed two-slot scope'):
        campaign.validate_scope(development_plan, manifest)


@pytest.mark.parametrize('fault', ['duplicate', 'reordered', 'replacement', 'repeat', 'extra', 'missing', 'no_task_gate', 'no_positive_gate'])
def test_development_admission_rejects_unreviewed_slots_or_missing_gates(development_plan, fault):
    manifest = {'cases': [{'id': slot['task']} for slot in development_plan['schedule']]}
    schedule = development_plan['schedule']
    if fault == 'duplicate':
        schedule[1] = dict(schedule[0])
    elif fault == 'reordered':
        schedule.reverse()
    elif fault == 'replacement':
        schedule[1]['task'] = 'sphinx-doc__sphinx-11445'
    elif fault == 'repeat':
        schedule[1]['repeat'] = 2
    elif fault == 'extra':
        schedule.append(dict(schedule[0]))
    elif fault == 'missing':
        schedule.pop()
    else:
        development_plan['per_run'].pop('require_task_verification' if fault == 'no_task_gate' else 'require_positive_verification')
    with pytest.raises(ValueError, match='reviewed two-slot scope'):
        campaign.validate_scope(development_plan, manifest)


def test_development_wrong_runtime_refuses_before_output_admission(tmp_path, monkeypatch, development_plan):
    monkeypatch.setattr(campaign, 'validate_inputs', lambda *args: (development_plan, {}, object()))
    monkeypatch.setattr(campaign.subprocess, 'check_output', lambda args, **kwargs: '' if 'status' in args else 'other-commit')
    with pytest.raises(ValueError, match='reviewed commit'):
        asyncio.run(campaign.campaign(Path('unused-plan.json'), tmp_path, tmp_path / 'out'))
    assert not (tmp_path / 'out').exists()


def result(*, passed=False, requests=2, reason='', complete=True):
    return {'passed': passed, 'task_status': 'completed' if passed else 'partial',
        'cleanup': {'state': 'clean'}, 'usage': {'input_tokens': 10, 'output_tokens': 2},
        'agent_seconds': 3, 'total_seconds': 5, 'statistics': {'tool_requests': 4},
        'request_evidence': {'observed_requests': requests, 'usage_complete': complete, 'accounting_complete': True,
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


def test_unfinalized_admission_is_visible_without_inventing_a_failure_or_usage():
    summary = campaign.summarize([{'slot': 1, 'task': 'a', 'state': 'started'},
        {'slot': 2, 'task': 'b', 'state': 'not_started'}])
    assert summary['attempted'] == 1 and summary['complete'] == 0
    assert summary['unfinalized_slots'] == [1] and summary['failures'] == []
    assert not summary['request_accounting_complete']
    assert summary['all_attempts_reported_tokens_lower_bound'] is None
