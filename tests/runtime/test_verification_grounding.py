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
