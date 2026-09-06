import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys

from forge.runtime.agent_loop import verification_from_result
from forge.runtime.completion import CompletionGate, TaskPolicy, unresolved_verification_failures
from forge.runtime.state import VerificationEvidence
from forge.runtime.workspace import WorkspaceTracker
from forge.tools.verify import VerifyInput, VerifyTool
from forge.tools.verification_checks import dormant_python_tests, OutputCheck, evaluate_output_checks


def test_python_definitions_do_not_execute_test_body(tmp_path):
    script = tmp_path / 'checks.py'
    script.write_text('def test_behavior():\n    raise AssertionError("must run")\n')

    async def run():
        tracker = WorkspaceTracker(tmp_path)
        await tracker.begin_turn()
        tool = VerifyTool(tmp_path, tracker)
        result = await tool.execute(VerifyInput(command=f'"{sys.executable}" checks.py'))
        assert result.metadata['exit_code'] == 0
        assert not result.success
        assert 'no visible test invocation' in result.summary
        assert verification_from_result(result) is None

    asyncio.run(run())


def test_real_test_runner_and_main_guard_are_not_flagged(tmp_path):
    path = tmp_path / 'checks.py'
    path.write_text('def test_behavior():\n    assert True\nif __name__ == "__main__":\n    test_behavior()\n')
    assert dormant_python_tests('python checks.py', tmp_path, tmp_path) is None
    assert dormant_python_tests('python -m pytest checks.py', tmp_path, tmp_path) is None


def test_exit_zero_does_not_override_failed_numeric_requirement(tmp_path):
    async def run():
        tracker = WorkspaceTracker(tmp_path)
        await tracker.begin_turn()
        tool = VerifyTool(tmp_path, tracker)
        result = await tool.execute(VerifyInput(
            command=f'"{sys.executable}" -', stdin='print(\'{"quality":0.8}\')',
            covers=['Required quality'],
            output_checks=[OutputCheck(key='quality', operator='ge', expected=0.95,
                                       requirement='Quality at least 0.95')],
        ))
        assert result.metadata['exit_code'] == 0
        assert not result.success
        evidence = verification_from_result(result)
        assert evidence is not None and not evidence.success
        gate = CompletionGate(tmp_path, TaskPolicy(require_verification=True))
        decision = await gate.evaluate(tracker, evidence, mutation_attempted=False)
        assert not decision.allowed
        assert any('observed 0.8' in reason for reason in decision.reasons)

    asyncio.run(run())


def test_labels_cannot_erase_independent_failures():
    failed = VerificationEvidence('python behavior.py', '.', 1, 0.1, False, 1,
                                  coverage=('user goal',))
    structural = replace(failed, command='python shape.py', exit_code=0)
    assert unresolved_verification_failures((failed, structural)) == (failed,)
    assert unresolved_verification_failures((failed, replace(failed, exit_code=0))) == ()
    failed_check = replace(failed, exit_code=0, evidence_valid=False, check_signature='strict')
    weaker = replace(failed_check, evidence_valid=True, check_signature='weak')
    assert unresolved_verification_failures((failed_check, weaker)) == (failed_check,)


def test_output_assertions_fail_closed_without_matching_values():
    check = OutputCheck(key='quality', operator='ge', expected=0.95, requirement='quality')
    for output in ('', '{}', '{"quality":true}', '{"quality":"0.99"}', '{"quality":NaN}'):
        assert evaluate_output_checks(output, [check])
    assert evaluate_output_checks('{"quality":0.97}', [check]) == []


def test_acceptance_failure_becomes_stale_obligation_after_environment_change(tmp_path):
    async def run():
        tracker = WorkspaceTracker(tmp_path)
        await tracker.begin_turn()
        failed = VerificationEvidence('python check.py', '.', 1, .1, False, 0,
                                      coverage=('required behavior',), requirement_ids=('r1',))
        tracker.mark_environment_change()
        weak = VerificationEvidence('python weak.py', '.', 0, .1, False, 0,
                                    environment_epoch=1, coverage=('format',))
        gate = CompletionGate(tmp_path, TaskPolicy(require_verification=True))
        result = await gate.evaluate(tracker, weak, verification_history=(failed, weak), mutation_attempted=False)
        assert not result.allowed
        strong = replace(failed, exit_code=0, environment_epoch=1)
        result = await gate.evaluate(tracker, strong, verification_history=(failed, strong), mutation_attempted=False)
        assert result.allowed
    asyncio.run(run())


def test_each_source_anchored_criterion_needs_executed_assertion(tmp_path):
    async def run():
        tracker = WorkspaceTracker(tmp_path)
        await tracker.begin_turn()
        criteria = ({'id': 'r1', 'source_quote': 'preserve words', 'condition': 'allowed replacements'},)
        gate = CompletionGate(tmp_path, TaskPolicy(require_acceptance_reconciliation=True))
        weak = VerificationEvidence('python check.py', '.', 0, .1, False, 0,
                                    coverage=('preserve words',), requirement_ids=('r1',))
        result = await gate.evaluate(tracker, weak, mutation_attempted=False, acceptance_criteria=criteria)
        assert not result.allowed
        strong = replace(weak, asserted_requirement_ids=('r1',), check_signature='actual-assertion')
        result = await gate.evaluate(tracker, strong, mutation_attempted=False, acceptance_criteria=criteria)
        assert result.allowed
    asyncio.run(run())


def test_explicit_reconciliation_cannot_be_bypassed_by_omitting_a_plan(tmp_path):
    async def run():
        tracker = WorkspaceTracker(tmp_path)
        await tracker.begin_turn()
        evidence = VerificationEvidence('python -c "print(1)"', '.', 0, .1, False, 0,
                                        coverage=('format',))
        result = await CompletionGate(tmp_path, TaskPolicy(require_acceptance_reconciliation=True)).evaluate(
            tracker, evidence, mutation_attempted=False)
        assert not result.allowed
        assert 'no source-anchored criteria' in ' '.join(result.reasons)
    asyncio.run(run())


def test_real_output_assertions_reconcile_each_requirement_without_plan(tmp_path):
    from forge.tasks.manager import TaskManager
    manager = TaskManager(tmp_path)
    manager.start('Return two records. Preserve the supplied input.')
    task = manager.register_acceptance([
        {'source_quote': 'Return two records.', 'condition': 'count is two', 'check': 'count the result'},
        {'source_quote': 'Preserve the supplied input.', 'condition': 'input unchanged', 'check': 'compare bytes'},
    ])
    assert not task.planned
    first, second = [item['id'] for item in task.acceptance_criteria]

    async def run():
        tracker = WorkspaceTracker(tmp_path)
        await tracker.begin_turn()
        tool = VerifyTool(tmp_path, tracker)
        gate = CompletionGate(tmp_path, TaskPolicy(require_acceptance_reconciliation=True))
        (tmp_path / 'input').write_text('original', encoding='utf-8')
        script = 'import json\nfrom pathlib import Path\nprint(json.dumps({"count":len([1,2]),"preserved":Path("input").read_text()=="original"}))'
        checks = [OutputCheck(key='count', expected=2, requirement='two records',
                              requirement_id=first, expected_source='Return two records.')]
        args = dict(command=f'"{sys.executable}" -', stdin=script)
        result = await tool.execute(VerifyInput(**args, output_checks=checks))
        evidence = verification_from_result(result)
        decision = await gate.evaluate(tracker, evidence, mutation_attempted=False, acceptance_criteria=task.acceptance_criteria)
        assert not decision.allowed and second in ' '.join(decision.reasons)
        checks.append(OutputCheck(key='preserved', expected=True, requirement='preserve input',
                                  requirement_id=second, expected_source='initial fixture bytes: original'))
        result = await tool.execute(VerifyInput(**args, output_checks=checks))
        evidence = verification_from_result(result)
        assert evidence.asserted_requirement_ids == tuple(sorted((first, second)))
        decision = await gate.evaluate(tracker, evidence, mutation_attempted=False, acceptance_criteria=task.acceptance_criteria)
        assert decision.allowed
    asyncio.run(run())
