"""Hand-computable protocol metrics; no public benchmark grades are fabricated."""
import json
from pathlib import Path

from benchmark.core.metrics import compute_metrics, compare_specs


def test_planned_fixture_includes_unclaimed_and_retains_first_attempt():
    fixture = json.loads(Path('tests/implementation/fixtures/eval-mini-bundle/input.json').read_text())
    trials = [{'id': case, 'selected_attempt_id': None} for case in fixture['case_ids']]
    attempts = []
    for item in fixture['attempts']:
        identity = item['case_id'] + ':' + str(item['attempt'])
        attempts.append({'id': identity, 'trial_id': item['case_id'], 'attempt_no': item['attempt'],
            'grade_state': 'graded' if item['raw_grade'] else 'unscored', 'grade_result': item['raw_grade'],
            'agent_outcome': 'completed', 'trace_complete': True})
        next(t for t in trials if t['id'] == item['case_id'])['selected_attempt_id'] = identity
    result = compute_metrics(trials, attempts, [])
    assert result['counts'] == {'planned': 4, 'passed': 2, 'failed': 1, 'unscored': 1,
        'graded': 3, 'first_attempt_passed': 1, 'internally_completed_graded': 3,
        'completion_false_positives': 1, 'attempts': 4, 'complete_traces': 4}
    assert result['planned_success_rate']['value'] == fixture['expected']['pass_rate']
    assert result['grading_coverage']['value'] == '0.75'
    assert result['first_attempt_success_rate']['value'] == '0.25'
    assert result['scored_success_rate']['denominator'] == 3
    assert result['cost_per_success']['status'] == 'unknown'


def test_cost_uses_every_request_once_unknown_is_not_zero_and_zero_success_is_na():
    trials = [{'id': 't', 'selected_attempt_id': 'a'}]
    attempts = [{'id': 'a', 'trial_id': 't', 'attempt_no': 1, 'grade_state': 'graded',
        'grade_result': 'pass', 'agent_outcome': 'completed', 'trace_complete': False}]
    requests = [{'id': 'r1', 'quality': 'actual', 'cost': '0.3', 'currency': 'USD'},
        {'id': 'r2', 'quality': 'actual', 'cost': '0.2', 'currency': 'USD'},
        {'id': 'r3', 'quality': 'unknown', 'cost': None, 'currency': 'USD'}]
    result = compute_metrics(trials, attempts, requests + [requests[0]])
    assert result['total_known_cost'] == '0.5'
    assert result['unknown_cost_requests'] == 1
    assert result['cost_per_success']['value'] is None
    attempts[0]['grade_result'] = 'fail'
    assert compute_metrics(trials, attempts, requests)['cost_per_success']['status'] == 'N/A'
    attempts[0]['grade_result'] = 'pass'
    known = compute_metrics(trials, attempts, requests[:2])
    assert known['cost_per_success'] == {'value': '0.5', 'status': 'known', 'currency': 'USD'}
    requests[0].update(cost='0', quality='actual')
    assert compute_metrics(trials, attempts, requests[:1])['cost_per_success']['value'] == '0'
    requests[0]['quality']='estimated'
    assert compute_metrics(trials, attempts, requests[:1])['cost_per_success']['status']=='unknown'
    requests[0].update(quality='actual',cost='0.3',currency='CNY')
    mixed=compute_metrics(trials,attempts,requests[:2])
    assert mixed['total_known_cost'] is None and mixed['mixed_currencies']
    assert mixed['known_cost_by_currency']=={'CNY':'0.3','USD':'0.2'}


def test_comparison_reports_budget_and_protocol_differences_without_inventing_lift():
    first = {'dataset': {'name': 'x', 'revision': '1', 'task_ids': ['b', 'a'],
        'task_revisions': {'a': '1', 'b': '1'}}, 'model': {'requested_model': 'm'},
        'budget': {'max_model_requests_per_attempt': 5}, 'execution': {'concurrency': 1},
        'protocol': {'repeats': 1}, 'harness': {'max_delivery_repairs': 0}, 'source': {'commit': 'a'},
        'grader': {'revision': '1'}, 'model_mode': 'scripted_mock', 'observability': {'capture': 'metadata'}}
    second = json.loads(json.dumps(first))
    second['dataset']['task_ids'].reverse()
    assert compare_specs([first, second])['comparable'] is True
    second['harness']['max_delivery_repairs'] = 2
    treatment=compare_specs([first,second])
    assert treatment['comparable'] and treatment['differences']==['run[1].harness']
    second['budget']['max_model_requests_per_attempt'] = 8
    result = compare_specs([first, second])
    assert not result['comparable']
    assert 'run[1].budget' in result['differences']
    assert 'run[1].harness' in result['differences']
