"""Versioned counts and denominators, independent of official runner metrics."""
from decimal import Decimal, localcontext
import json


def decimal_text(value):
    return format(value, 'f').rstrip('0').rstrip('.') if '.' in format(value, 'f') else format(value, 'f')


def rate(numerator, denominator):
    with localcontext() as context:
        context.prec = 40
        return {'numerator': numerator, 'denominator': denominator,
            'value': decimal_text(Decimal(numerator) / denominator) if denominator else None,
            'status': 'known' if denominator else 'N/A'}


def compute_metrics(trials, attempts, requests):
    trials, attempts = list(trials), list(attempts)
    by_id = {a['id']: a for a in attempts}
    first = {}
    for attempt in attempts:
        previous = first.get(attempt['trial_id'])
        if previous is None or attempt['attempt_no'] < previous['attempt_no']:
            first[attempt['trial_id']] = attempt
    def scored(attempt):
        return bool(attempt and attempt.get('grade_state') == 'graded' and attempt.get('grade_result') in ('pass', 'fail'))
    selected = [by_id.get(t['selected_attempt_id']) for t in trials]
    graded = [a for a in selected if scored(a)]
    passed = sum(a['grade_result'] == 'pass' for a in graded)
    completed = [a for a in graded if a.get('agent_outcome') == 'completed']
    false_positives = sum(a['grade_result'] == 'fail' for a in completed)
    first_passed = sum(scored(a) and a['grade_result'] == 'pass' for a in first.values())
    complete_traces = sum(bool(a.get('trace_complete')) for a in attempts)
    counts = {'planned': len(trials), 'passed': passed, 'failed': len(graded) - passed,
        'unscored': len(trials) - len(graded), 'graded': len(graded), 'first_attempt_passed': first_passed,
        'internally_completed_graded': len(completed), 'completion_false_positives': false_positives,
        'attempts': len(attempts), 'complete_traces': complete_traces}
    seen, known, estimated, unknown, currencies = {}, Decimal(0), Decimal(0), 0, set()
    subtotals, estimated_count = {}, 0
    with localcontext() as context:
        context.prec = 80
        for request in requests:
            if request['id'] in seen:
                if seen[request['id']] != request:
                    raise ValueError('Conflicting request ledger facts')
                continue
            seen[request['id']] = request
            if request['cost'] is None or request['quality'] == 'unknown' or not request.get('currency'):
                unknown += 1
                continue
            amount = Decimal(request['cost'])
            if not amount.is_finite() or amount < 0:
                raise ValueError('Invalid ledger amount')
            currencies.add(request['currency'])
            if request['quality'] == 'actual':
                known += amount
                subtotals[request['currency']] = subtotals.get(request['currency'], Decimal(0)) + amount
            elif request['quality'] == 'estimated':
                estimated += amount
                estimated_count += 1
            else:
                raise ValueError('Invalid ledger quality')
        currency = next(iter(currencies)) if len(currencies) == 1 else None
        cost_status = 'N/A' if not passed else 'unknown' if unknown or not seen or estimated_count or len(currencies) != 1 else 'known'
        cost = {'value': decimal_text(known / passed) if cost_status == 'known' else None,
            'status': cost_status, 'currency': currency}
    return {'schema_version': 'forge.eval.metrics.v1', 'counts': counts,
        'planned_success_rate': rate(passed, len(trials)), 'grading_coverage': rate(len(graded), len(trials)),
        'scored_success_rate': rate(passed, len(graded)), 'first_attempt_success_rate': rate(first_passed, len(trials)),
        'completion_false_positive': rate(false_positives, len(completed)),
        'trace_completeness': rate(complete_traces, len(attempts)), 'total_known_cost': decimal_text(known) if len(currencies) <= 1 else None,
        'known_cost_by_currency': {key:decimal_text(value) for key,value in sorted(subtotals.items())},
        'total_estimated_cost': decimal_text(estimated) if len(currencies) <= 1 else None, 'unknown_cost_requests': unknown,
        'estimated_cost_requests': estimated_count,
        'cost_currency': currency, 'mixed_currencies': len(currencies) > 1, 'cost_per_success': cost,
        'request_count': len(seen), 'official_metrics': {}}


def compare_specs(specs):
    """Show frozen configuration differences; this does not estimate improvement."""
    normalized = []
    for spec in specs:
        value = json.loads(json.dumps(spec))
        value['dataset']['task_ids'].sort()
        normalized.append(value)
    differences, blocking = [], []
    for index, value in enumerate(normalized[1:], 1):
        for group in ('dataset', 'model', 'budget', 'execution', 'protocol', 'harness', 'source', 'grader', 'model_mode', 'observability'):
            if normalized[0].get(group) != value.get(group):
                differences.append(f'run[{index}].{group}')
                # Harness/source are the declared candidate treatment; controlled conditions must match.
                if group not in ('harness','source'):
                    blocking.append(group)
    return {'comparable': not blocking, 'differences': differences[:100]}
