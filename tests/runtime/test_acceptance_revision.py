import asyncio
from dataclasses import replace

from forge.tasks.manager import TaskManager
from forge.tasks.state import ActiveTask
from forge.runtime.state import VerificationEvidence
from forge.runtime.completion import unresolved_verification_failures
from forge.tools.finish import FinishTaskInput


def test_completion_retries_cannot_mutate_existing_contract(tmp_path):
    from forge.runtime.agent_loop import Conversation
    from forge.runtime.runner import TurnRunner
    from forge.tools.base import ToolResult
    from forge.runtime.completion import TaskPolicy
    from forge.tools import create_default_registry
    conversation = Conversation(client=object(), context_root=tmp_path,
                                registry=create_default_registry(tmp_path),
                                task_policy=TaskPolicy(require_acceptance_reconciliation=True))
    conversation.task_manager.start('Preserve input. Produce output.')
    initial = conversation.task_manager.register_acceptance([
        dict(source_quote='Preserve input.', condition='bytes unchanged', check='hash')])
    runner = TurnRunner(conversation)
    async def run():
        await runner.tracker.begin_turn()
        for i in range(25):
            result = await runner._finish_declaration(ToolResult.ok('finish', metadata={
                'finish_task': True, 'status': 'completed', 'task_kind': 'change', 'summary': 'Done',
                'acceptance_criteria': [dict(source_quote='Produce output.', condition=f'output {i}', check='check')],
            }))
            assert not result.success  # Still lacks real evidence; no bypass.
            assert conversation.task_manager.active.acceptance_criteria == initial.acceptance_criteria
    asyncio.run(run())


def test_distinct_clauses_and_legacy_ids_survive_restore(tmp_path):
    manager = TaskManager(tmp_path)
    manager.start('Preserve input and produce output.')
    task = manager.register_acceptance([
        dict(source_quote='Preserve input and produce output.', clause_id=c, condition=c, check='check')
        for c in ('preserve', 'output')])
    assert len(task.acceptance_criteria) == 2
    serialized = task.as_dict()
    serialized['acceptance_criteria'][0]['id'] = 'legacy-id'
    restored = ActiveTask.from_dict(serialized)
    assert restored.acceptance_criteria[0]['id'] == 'legacy-id'


def test_real_checker_repair_requires_preserved_assertion(tmp_path):
    import sys
    from forge.runtime.workspace import WorkspaceTracker
    from forge.runtime.agent_loop import verification_from_result
    from forge.runtime.completion import CompletionGate, TaskPolicy
    from forge.tools.verify import VerifyTool, VerifyInput
    async def run():
        tracker = WorkspaceTracker(tmp_path)
        await tracker.begin_turn()
        tool = VerifyTool(tmp_path, tracker)
        checks = [dict(key='count', expected=2, requirement='two records', requirement_id='r1', expected_source='spec')]
        first = await tool.execute(VerifyInput(command=f'"{sys.executable}" -', stdin='raise SyntaxError("bad checker")', output_checks=checks))
        failed = verification_from_result(first)
        fixed = await tool.execute(VerifyInput(command=f'"{sys.executable}" -', stdin='import json; print(json.dumps({"count":len([1,2])}))',
            output_checks=checks, supersedes=[failed.verification_id], revision_reason='Repair checker syntax, keep count assertion.'))
        success = verification_from_result(fixed)
        assert success.supersedes == (failed.verification_id,)
        gate = CompletionGate(tmp_path, TaskPolicy(require_acceptance_reconciliation=True))
        result = await gate.evaluate(tracker, success, verification_history=(failed, success),
            mutation_attempted=False, acceptance_criteria=({'id':'r1', 'source_quote':'two records'},))
        assert result.allowed, result.reasons
    asyncio.run(run())


def test_rephrasing_does_not_create_or_weaken_requirement(tmp_path):
    manager = TaskManager(tmp_path)
    manager.start('Preserve input.')
    first = manager.register_acceptance([dict(source_quote='Preserve input.', condition='bytes unchanged', check='hash')])
    for i in range(25):
        current = manager.register_acceptance([dict(source_quote='Preserve input.', condition=f'exists {i}', check='exists')])
        assert current.acceptance_criteria == first.acceptance_criteria
    assert ActiveTask.from_dict(current.as_dict()).acceptance_criteria == first.acceptance_criteria


def test_failed_summary_is_sufficient_for_terminal_declaration():
    value = FinishTaskInput(task_kind='change', status='failed', summary='Simulator returns zero.')
    assert value.blocked_reasons == ['Simulator returns zero.']


def test_explicit_checker_revision_preserves_assertions():
    check = dict(key='quality', operator='ge', expected=.95, requirement_id='r1', expected_source='spec')
    old = VerificationEvidence('python broken.py', '.', 1, .1, False, 0,
        verification_id='old', output_checks=(check,), requirement_ids=('r1',))
    new = replace(old, command='python fixed.py', exit_code=0, verification_id='new',
        supersedes=('old',), revision_reason='Repair checker syntax; preserve the quality assertion.',
        asserted_requirement_ids=('r1',))
    assert unresolved_verification_failures((old, new)) == ()
    for bad in (replace(new, supersedes=()), replace(new, revision_reason=''),
                replace(new, exit_code=1), replace(new, cwd='elsewhere'),
                replace(new, output_checks=()),
                replace(new, output_checks=({**check, 'expected': .8},))):
        assert old in unresolved_verification_failures((old, bad))
