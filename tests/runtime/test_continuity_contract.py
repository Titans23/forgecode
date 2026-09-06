'''Regression probes for cross-turn identity and lossless failure context.'''

import asyncio
import json
import hashlib
from pathlib import Path

from forge.context.compactor import CompactionConfig
from forge.context.manager import ContextManager
from forge.runtime.state import ModelTextDelta, ModelUsageUpdate, TokenUsage
from forge.sessions.store import SessionStore
from forge.tasks.manager import TaskManager
from forge.tasks.state import ActiveTask
from forge.runtime.completion import CompletionGate, TaskPolicy
from forge.runtime.workspace import WorkspaceTracker
from forge.runtime.state import VerificationEvidence
import pytest


def test_reused_provider_id_does_not_hide_interrupted_second_turn(tmp_path: Path):
    store = SessionStore(tmp_path, data_root=tmp_path / 'sessions')
    journal = store.create(model='test')
    journal.record_turn_started('first', None)
    journal.record_tool_started('call-1', 'run_command', {'command': 'first'})
    journal.record_tool_completed('call-1', 'run_command', True)
    journal.record_turn_started('second', None)
    journal.record_tool_started('call-1', 'run_command', {'command': 'second'})
    # A crash here must not be covered by the first turn's successful result.
    state, reopened = store.open(journal.session_id)
    assert len(state.indeterminate_tools) == 1
    assert state.indeterminate_tools[0]['arguments']['command'] == 'second'
    # Consumer compatibility events remain idempotent within this turn.
    before = reopened.sequence
    reopened.record_tool_started('call-1', 'run_command', {'command': 'second'})
    assert reopened.sequence == before
    reopened.record_tool_completed('call-1', 'run_command', False, status='indeterminate')
    restored, _ = store.open(journal.session_id)
    assert len(restored.indeterminate_tools) == 1


def pair(identifier: str, output: str, *, failed: bool = False):
    return [
        {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': identifier,
          'name': 'verify', 'input': {'command': 'check'}}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': identifier,
          'content': output, 'is_error': failed}]},
    ]


def test_medium_failure_reaches_summarizer_before_shortening(tmp_path: Path):
    diagnostic = 'a' * 6000 + 'ROOT_CAUSE_IN_MIDDLE' + 'z' * 6000
    messages = [{'role': 'user', 'content': 'Produce result; keep original input unchanged.'}]
    messages += pair('failed', diagnostic, failed=True)
    for i in range(5):
        messages += pair(str(i), 'ordinary output')

    class Summarizer:
        async def stream(self, messages, **kwargs):
            assert 'ROOT_CAUSE_IN_MIDDLE' in json.dumps(messages)
            yield ModelTextDelta(json.dumps({'goal': 'result', 'failed_attempts': ['root cause']}))
            yield ModelUsageUpdate(TokenUsage(1, 1))

    manager = ContextManager(messages, tmp_path)
    report = asyncio.run(manager.compact_history(messages, Summarizer(), force=True))
    assert report.success, report.reason


def test_shortened_medium_output_has_recoverable_artifact(tmp_path: Path):
    diagnostic = 'a' * 6000 + 'ROOT_CAUSE_IN_MIDDLE' + 'z' * 6000
    messages = pair('failed', diagnostic, failed=True)
    for i in range(5):
        messages += pair(str(i), 'ordinary output')
    manager = ContextManager(messages, tmp_path)
    prepared = manager.prepare(messages)
    output = prepared[1]['content'][0]['content']
    import re
    match = re.search(r'sha256: ([0-9a-f]{64})', output)
    assert match, output
    assert manager.read_artifact(match[1], offset=0, max_characters=20000) == diagnostic


def test_compaction_bounds_history_and_pins_original_user_constraints(tmp_path: Path):
    original = 'Produce result; DO NOT MODIFY the supplied input.'
    messages = [{'role': 'user', 'content': original}]
    messages += pair('large', 'x' * 25000)

    class Summarizer:
        async def stream(self, **kwargs):
            # Deliberately omits the user's restriction.
            yield ModelTextDelta('{"goal":"result","constraints":[]}')
            yield ModelUsageUpdate(TokenUsage(1, 1))

    manager = ContextManager(messages, tmp_path, CompactionConfig())
    for _ in range(3):
        report = asyncio.run(manager.compact_history(
            messages, Summarizer(), force=True, context_window_tokens=8000,
            reserved_output_tokens=1000, task_goal='result',
        ))
        assert report.success
        assert original in json.dumps(manager.prepare(messages))
        assert manager.stats.estimated_tokens <= 4200


def test_acceptance_map_quotes_user_and_never_grants_write_scope(tmp_path: Path):
    manager = TaskManager(tmp_path)
    manager.start('Write a converter. Return one integer. Keep input.txt unchanged.')
    criterion = {'source_quote': 'Return one integer.', 'condition': 'scalar not list',
                 'check': 'compare known fixture result and output type'}
    task = manager.plan(['implement', 'test'], acceptance_criteria=[criterion], scope_hints=['src/**'])
    assert all(task.acceptance_criteria[0][key] == value for key, value in criterion.items())
    assert task.acceptance_criteria[0]['id'].startswith('req-')
    assert task.acceptance_criteria[0]['origin'] == 'model_proposal'
    assert ActiveTask.from_dict(task.as_dict()).acceptance_criteria == task.acceptance_criteria
    assert 'not a hard write boundary' in manager.system_suffix()
    assert manager.outside_scope(('other/file.txt',)) == ()
    with pytest.raises(ValueError, match='original user goal'):
        manager.plan(['implement', 'test'], replace_existing=True,
                     acceptance_criteria=[{**criterion, 'source_quote': 'A list is acceptable.'}])


def test_modified_reference_cannot_be_covered_by_successful_self_test(tmp_path: Path):
    reference = tmp_path / 'reference.txt'
    reference.write_text('original', encoding='utf-8')
    expected = hashlib.sha256(reference.read_bytes()).hexdigest()
    gate = CompletionGate(tmp_path, TaskPolicy(
        required_file_hashes=(('reference.txt', expected),), required_coverage=('scalar result',),
    ))

    async def check():
        tracker = WorkspaceTracker(tmp_path)
        await tracker.begin_turn()
        reference.write_text('tampered', encoding='utf-8')
        await tracker.refresh()
        evidence = VerificationEvidence('python test.py', '.', 0, 0, False, tracker.revision,
                                        coverage=('file exists',))
        decision = await gate.evaluate(tracker, evidence, mutation_attempted=True)
        assert not decision.allowed
        assert any('input content was changed' in reason for reason in decision.reasons)
        assert any('scalar result' in reason for reason in decision.reasons)
    asyncio.run(check())


def test_stdout_artifact_retains_middle_and_tail_with_small_memory_limit(tmp_path: Path):
    from forge.tools.shell import run_process
    import sys
    root = tmp_path / 'work'
    root.mkdir()
    nested = root / 'nested'
    nested.mkdir()
    output = 'HEAD' + 'x' * 1000 + 'MIDDLE' + 'y' * 1000 + 'TAIL'
    result = asyncio.run(run_process(
        [sys.executable, '-c', f'print({output!r}, end="")'], cwd=nested,
        timeout_seconds=10, max_output_bytes=100, artifact_root=root,
    ))
    assert result.stdout.startswith('HEAD') and result.stdout.endswith('TAIL')
    assert len(result.stdout) == 100
    assert result.stdout_artifact == hashlib.sha256(output.encode()).hexdigest()
    assert ContextManager([], root).read_artifact(result.stdout_artifact, offset=0) == output


def test_explicit_task_contract_skips_model_routing(tmp_path: Path):
    from forge.runtime.agent_loop import Conversation
    from forge.runtime.state import TurnCompleted

    class Client:
        async def stream(self, **kwargs):
            yield ModelTextDelta('Done')
            yield ModelUsageUpdate(TokenUsage(1, 1))

    class Router:
        async def route(self, *args):
            raise AssertionError('Explicit caller task must not be reclassified by a model.')

    conversation = Conversation(client=Client(), intent_router=Router(),
                                task_relation='new', context_root=tmp_path)
    async def collect():
        return [event async for event in conversation.stream('Explain the result')]
    events = asyncio.run(collect())
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.model_calls == 1
    assert conversation.task_manager.active.goal == 'Explain the result'
