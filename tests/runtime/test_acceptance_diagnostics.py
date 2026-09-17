import asyncio
import json
from dataclasses import replace

import pytest

from forge.runtime.acceptance import requirement_observation, requirement_reason
from forge.runtime.check_contracts import assertion_contract, inherit_check_arguments
from forge.runtime.completion import CompletionGate, TaskPolicy, checker_revision_covers
from forge.runtime.state import VerificationEvidence
from forge.runtime.workspace import WorkspaceTracker
from forge.tools.verification_checks import OutputCheck, evaluate_output_checks


CRITERION = {'id': 'r1', 'source_quote': 'Preserve the measured response',
             'condition': 'Response follows the input transformation', 'check': 'Compare measured differences'}


def evidence(**kwargs):
    item = VerificationEvidence('python check.py', '.', 0, .1, False, 0,
        requirement_ids=('r1',), asserted_requirement_ids=('r1',), check_signature='signature',
        verification_id='v1', check_id='stored-1')
    return replace(item, **kwargs)


@pytest.mark.parametrize('items,state', [
    ([], 'unverified'),
    ([evidence(workspace_revision=1)], 'stale_evidence'),
    ([evidence(freshness='unknown')], 'stale_evidence'),
    ([evidence(exit_code=1)], 'check_failed'),
    ([evidence(timed_out=True)], 'check_failed'),
    ([evidence(evidence_valid=False)], 'check_failed'),
    ([evidence(asserted_requirement_ids=())], 'assertion_binding_missing'),
    ([evidence(command='false; echo OK')], 'execution_contract_unestablished'),
    ([evidence(command='python -m py_compile app.py')], 'execution_contract_unestablished'),
    ([evidence()], 'recorded_assertion_passed'),
])
def test_diagnostics_distinguish_missing_failed_stale_and_unclassified(items, state):
    result = requirement_observation(CRITERION, items, 0, 0)
    assert result['state'] == state
    assert (requirement_reason(result) is None) == (state == 'recorded_assertion_passed')


def test_shell_diagnostic_preserves_existing_assertion_and_gate_rejection(tmp_path):
    async def run():
        tracker = WorkspaceTracker(tmp_path)
        await tracker.refresh()
        item = evidence(command='set -eu\npython test.py', workspace_revision=tracker.revision)
        gate = CompletionGate(tmp_path, TaskPolicy(require_acceptance_reconciliation=True))
        decision = await gate.evaluate(tracker, item, mutation_attempted=False, acceptance_criteria=(CRITERION,))
        assert not decision.allowed
        assert any('execution_contract_unestablished' in reason for reason in decision.reasons)
        assert not any('no current executed output assertion' in reason for reason in decision.reasons)
    asyncio.run(run())


def relation(**kwargs):
    return OutputCheck(key='transformed', operator='delta_eq', reference_key='original', expected=12,
                       requirement='Translation changes response by 12', requirement_id='r1',
                       expected_source='Independent translation contract', **kwargs)


def test_transformation_checks_actual_difference_not_order_or_range():
    check = relation()
    assert not evaluate_output_checks(json.dumps({'original': 20, 'transformed': 32}), [check])
    # Both outputs are ordered and in range, but the transformation relation is wrong.
    failures = evaluate_output_checks(json.dumps({'original': 20, 'transformed': 25}), [check])
    assert failures and 'observed 5' in failures[0]


@pytest.mark.parametrize('observations', [
    {'original': True, 'transformed': 13}, {'original': '20', 'transformed': 32},
    {'transformed': 32}, {'original': float('nan'), 'transformed': 32},
    {'original': 20, 'transformed': float('inf')},
])
def test_relation_rejects_missing_non_numeric_and_nonfinite(observations):
    assert evaluate_output_checks(json.dumps(observations), [relation()])


def test_relation_tolerance_has_real_bounds_and_cannot_be_weakened_by_supersession():
    check = relation(tolerance=.25)
    assert not evaluate_output_checks('{"original":20,"transformed":32.25}', [check])
    assert evaluate_output_checks('{"original":20,"transformed":32.26}', [check])
    old = evidence(exit_code=1, output_checks=(check.model_dump(),))
    new = replace(old, exit_code=0, verification_id='v2', supersedes=('v1',), revision_reason='Fix checker')
    assert checker_revision_covers(old, new)
    for change in ({'tolerance': 100}, {'reference_key': 'other'}, {'expected': 5}):
        altered = {**check.model_dump(), **change}
        assert not checker_revision_covers(old, replace(new, output_checks=(altered,)))
        with pytest.raises(ValueError, match='Conflicting assertion'):
            inherit_check_arguments({'command': 'python fixed.py', 'inherit_checks_from': ['v1'],
                                    'revision_reason': 'Fix', 'output_checks': [altered]}, [old])


def test_legacy_check_serialization_and_contract_identity_remain_unchanged():
    legacy = dict(key='digest', operator='eq', expected='reference-digest', requirement='Exact component',
                  requirement_id='r1', expected_source='Independent source', source_ref='')
    parsed = OutputCheck(**legacy)
    assert parsed.model_dump() == legacy
    assert assertion_contract(legacy) == assertion_contract(parsed.model_dump())
    assert not evaluate_output_checks('{"digest":"reference-digest"}', [parsed])
    assert evaluate_output_checks('{"digest":"different-digest","exists":true,"version":"correct"}', [parsed])


@pytest.mark.parametrize('change', [
    {'reference_key': ''}, {'reference_key': 'transformed'}, {'expected': True},
    {'expected': '12'}, {'tolerance': -1}, {'tolerance': float('inf')},
])
def test_invalid_relation_definitions_rejected(change):
    values = relation().model_dump()
    values.update(change)
    with pytest.raises(ValueError):
        OutputCheck(**values)


def test_relation_failure_invalidates_zero_exit_verification(tmp_path):
    import sys
    from forge.tools.verify import VerifyInput, VerifyTool

    async def run():
        tool = VerifyTool(tmp_path, WorkspaceTracker(tmp_path))
        result = await tool.execute(VerifyInput(command=f'"{sys.executable}" -',
            stdin='import json; print(json.dumps({"original":20,"transformed":25}))',
            output_checks=[relation()]))
        assert result.metadata['exit_code'] == 0
        assert not result.success
        assert result.error.code == 'verification_not_established'
        assert not result.metadata['evidence_valid']
        assert 'observed 5' in result.metadata['evidence_issues'][0]
    asyncio.run(run())


def test_review_returns_linked_evidence_and_does_not_submit(tmp_path):
    from forge.runtime.agent_loop import Conversation
    from forge.runtime.runner import TurnRunner
    from forge.tools.base import ToolResult

    async def run():
        conversation = Conversation(client=object(), context_root=tmp_path, include_task_tools=False)
        conversation.task_manager.start(CRITERION['source_quote'])
        conversation.task_manager.register_acceptance([CRITERION])
        runner = TurnRunner(conversation)
        runner.state.evidence.add(evidence(exit_code=1, diagnostic='Expected reference digest differs'))
        result = await runner._review_delivery(ToolResult.ok('review', metadata={'review_delivery': True}))
        observation = result.metadata['requirement_observations'][0]
        assert observation['state'] == 'check_failed'
        assert observation['checks'][0]['check_id'] == 'stored-1'
        assert 'reference digest differs' in observation['checks'][0]['diagnostic']
        assert runner.terminal is None
        assert conversation.task_manager.active.status == 'in_progress'
    asyncio.run(run())


def test_unknown_command_reports_why_existing_assertions_are_insufficient(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from forge.tools.shell import ProcessResult
    from forge.tools.verify import VerifyInput, VerifyTool
    import forge.tools.verify as module

    async def run():
        monkeypatch.setattr(module, 'run_process', AsyncMock(return_value=ProcessResult(
            exit_code=0, stdout='{"measured":42}', stderr='', duration_seconds=.01, timed_out=False)))
        result = await VerifyTool(tmp_path, WorkspaceTracker(tmp_path)).execute(VerifyInput(
            command='python check.py; echo done', output_checks=[OutputCheck(key='measured', expected=42,
                requirement='Measured value', requirement_id='r1', expected_source='Independent reference')]))
        assert result.success  # Preserve execution fact; do not silently relax the gate.
        assert result.metadata['asserted_requirement_ids'] == ['r1']
        assert result.metadata['verification_quality'] == 'unknown'
        assert 'Existing assertions are retained' in result.content
    asyncio.run(run())
