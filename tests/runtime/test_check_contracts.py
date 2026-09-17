import asyncio
from dataclasses import replace
import sys

import pytest

from forge.runtime.check_contracts import inherit_check_arguments
from forge.runtime.completion import checker_revision_covers
from forge.runtime.state import VerificationEvidence


def record():
    return VerificationEvidence('python old.py', '.', 1, .1, False, 0,
        verification_id='old', requirement_ids=('r1',), output_checks=({
            'key': 'quality', 'operator': 'ge', 'expected': .95,
            'requirement': 'accurate result', 'requirement_id': 'r1', 'expected_source': 'reference A',
        },))


def test_source_reference_separates_prose_but_preserves_authority():
    old = record()
    old = replace(old, output_checks=({**old.output_checks[0], 'source_ref': 'spec:hash-A'},))
    new = replace(old, exit_code=0, verification_id='new', supersedes=('old',),
                  revision_reason='Repair checker', asserted_requirement_ids=('r1',),
                  output_checks=({**old.output_checks[0], 'expected_source': 'Rephrased explanation'},))
    assert checker_revision_covers(old, new)
    for key, value in [('source_ref', 'spec:hash-B'), ('requirement_id', 'r2'), ('expected', .8)]:
        assert not checker_revision_covers(old, replace(new, output_checks=({**new.output_checks[0], key: value},)))
    legacy = record()
    assert not checker_revision_covers(legacy, replace(new, output_checks=({**legacy.output_checks[0], 'expected_source': 'changed'},)))


def test_inherited_legacy_check_executes_and_clears_only_matching_failure(tmp_path):
    from forge.runtime.agent_loop import verification_from_result
    from forge.runtime.workspace import WorkspaceTracker
    from forge.tools.verify import VerifyInput, VerifyTool

    async def exercise():
        old = record()
        arguments = inherit_check_arguments(dict(command=f'"{sys.executable}" -',
            stdin='import json; print(json.dumps({"quality":0.97}))',
            inherit_checks_from=['old'], revision_reason='Repair Python checker'), [old])
        tracker = WorkspaceTracker(tmp_path)
        await tracker.begin_turn()
        result = await VerifyTool(tmp_path, tracker).execute(VerifyInput.model_validate(arguments))
        new = verification_from_result(result)
        assert result.success
        assert checker_revision_covers(old, new)
        assert not checker_revision_covers(replace(old, cwd='elsewhere'), new)
        failed_args = {**arguments, 'stdin': 'import json; print(json.dumps({"quality":0.8}))'}
        failed = await VerifyTool(tmp_path, tracker).execute(VerifyInput.model_validate(failed_args))
        assert not failed.success
        assert not checker_revision_covers(old, verification_from_result(failed))
    asyncio.run(exercise())


def test_inheritance_rejects_unknown_and_weaker_assertions():
    args = dict(command='python fixed.py', inherit_checks_from=['old'], revision_reason='repair')
    with pytest.raises(ValueError, match='Unknown'):
        inherit_check_arguments(args, [])
    for key, value in [('expected', .8), ('requirement_id', 'r2'), ('expected_source', 'other source')]:
        with pytest.raises(ValueError, match='Conflicting'):
            inherit_check_arguments({**args, 'output_checks': [{**record().output_checks[0], key: value}]}, [record()])


def test_multiple_legacy_sources_are_preserved_as_conjunctive_checks():
    old = record()
    other = replace(old, verification_id='other', output_checks=({**old.output_checks[0],
        'expected_source': 'rephrased reference A'},))
    args = inherit_check_arguments(dict(command='python fixed.py', inherit_checks_from=['old', 'other'],
        revision_reason='Repair both historical checkers without dropping either assertion'), [old, other])
    assert len(args['output_checks']) == 2
    repaired = replace(old, verification_id='new', exit_code=0, output_checks=tuple(args['output_checks']),
        supersedes=tuple(args['supersedes']), revision_reason=args['revision_reason'], asserted_requirement_ids=('r1',))
    assert checker_revision_covers(old, repaired)
    assert checker_revision_covers(other, repaired)


def test_delivery_retains_failure_without_completion_retry_loop():
    from forge.runtime.delivery import completion_report
    report = completion_report(status='partial', evidence=(record(),),
        workspace_revision=0, environment_epoch=0, reasons=('unmet',), has_contract=True)
    assert report.run_status == 'completed'
    assert report.acceptance_status == 'unmet'
    assert report.verification_status == 'failed_checks'


def test_new_execution_identity_cannot_change_delivery_verdict():
    from forge.runtime.delivery import completion_report
    reports = [completion_report(status='partial', evidence=(replace(record(), verification_id=str(i)),),
        workspace_revision=0, environment_epoch=0, reasons=('unmet',), has_contract=True)
        for i in range(4)]
    assert {report.verification_status for report in reports} == {'failed_checks'}
    assert {report.acceptance_status for report in reports} == {'unmet'}


def test_protocol_total_budget_survives_local_recovery(tmp_path):
    from forge.runtime.agent_loop import Conversation
    from forge.runtime.runner import TurnRunner
    runner = TurnRunner(Conversation(client=object(), context_root=tmp_path))
    for _ in range(3):
        runner._protocol_feedback('invalid argument')
        runner.consecutive_tool_protocol_errors = 0
    assert runner.terminal is None
    for _ in range(30):
        runner._protocol_feedback('invalid argument')
        runner.consecutive_tool_protocol_errors = 0
    assert runner.terminal[1] == 'tool_protocol_exhausted'


def test_runner_resolves_checks_before_execution_and_recovers_after_valid_call(tmp_path):
    from forge.runtime.agent_loop import Conversation
    from forge.runtime.runner import TurnRunner
    from forge.runtime.state import ToolCall
    from forge.tools import create_default_registry
    from forge.permissions.policy import PermissionManager
    conversation = Conversation(client=object(), context_root=tmp_path,
        registry=create_default_registry(tmp_path),
        permission_manager=PermissionManager(tmp_path, user_path=tmp_path / 'permissions.json'))
    runner = TurnRunner(conversation)
    runner.state.evidence.add(record())
    runner.consecutive_tool_protocol_errors = 1

    async def exercise():
        await runner.tracker.begin_turn()
        arguments = dict(command=f'"{sys.executable}" -', stdin='print(\'{"quality":0.97}\')',
                         inherit_checks_from=['old'], revision_reason='Repair checker')
        async for _ in runner._batch([ToolCall(0, 'repair', 'verify', arguments)]):
            pass
        assert len(runner.state.evidence.verification) == 2
        assert checker_revision_covers(record(), runner.state.evidence.verification[-1])
        assert runner.consecutive_tool_protocol_errors == 0
        assert runner.state.execution_records[-1].status == 'executed'
    asyncio.run(exercise())
