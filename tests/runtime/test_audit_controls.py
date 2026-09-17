"""Counterexamples for the offline audit repairs; no external model requests."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from forge.runtime.completion import unresolved_verification_failures
from forge.runtime.delivery import completion_report
from forge.runtime.state import VerificationEvidence
from forge.runtime.turn_state import TurnState
from forge.runtime.verification import verification_quality
from forge.sessions.store import SessionError, SessionJournal
from forge.tasks.manager import TaskManager, anchored_source_quote


@pytest.mark.parametrize('command', [
    "python3 - <<'PY'\nassert True\nPY\nfalse; true",
    "python3 - <<'PY'; true\nassert False\nPY",
    "python3 - <<'PY'\nassert False\nMISSING",
    "sh -c 'false; true'", "false; echo OK", "false | cat",
])
def test_shell_masking_still_cannot_establish_coverage(command):
    assert verification_quality(command) in {'unknown', 'negative'}


def test_python_body_punctuation_is_not_shell_syntax():
    assert verification_quality('python3 - <<"PY"\nx = 3; assert x != 0\nPY') == 'behavior'


def test_source_reflow_preserves_user_span_and_identity(tmp_path):
    manager = TaskManager(tmp_path)
    manager.start('Output exactly 42\n  bytes to /app/result.bin.')
    item = {'source_quote': 'exactly 42 bytes', 'requirement': '42 bytes'}
    first = manager.register_acceptance([item])
    assert first.acceptance_criteria[0]['source_quote'] == 'exactly 42\n  bytes'
    second = manager.register_acceptance([{**item, 'source_quote': 'exactly 42\tbytes'}])
    assert second.acceptance_criteria == first.acceptance_criteria


@pytest.mark.parametrize('quote', ['output 42', 'Output 43', '/app/other.bin', 'must write', ''])
def test_source_changes_are_not_whitespace_reflow(quote):
    with pytest.raises(ValueError):
        anchored_source_quote(quote, ('Output 42 to /app/result.bin; do not write elsewhere.',))


def test_budget_reservation_includes_all_ancestors():
    root = TurnState(max_tool_calls=2)
    parent = TurnState(parent=root)
    child = TurnState(parent=parent)
    child.record_tool_request()
    child.record_tool_request()
    assert child.can_request_tool_batch(0)  # Reserved calls may execute at the limit.
    assert not child.can_request_tool_batch(1)
    child.record_tool_request()
    assert not child.can_request_tool_batch(0)


def test_unjustified_supersession_remains_failed():
    old = VerificationEvidence('false', '.', 1, .1, False, 0, verification_id='old')
    new = replace(old, command='true', exit_code=0, verification_id='new', supersedes=('old',))
    assert unresolved_verification_failures((old, new)) == (old,)
    report = completion_report(status='completed', evidence=(old, new), workspace_revision=0,
                               environment_epoch=0, reasons=(), has_contract=False)
    assert report.failed_checks == ('old',)


def test_journal_tail_handles_large_unicode_records_and_stale_writer(tmp_path, monkeypatch):
    path = tmp_path / 'journal.jsonl'
    journal = SessionJournal(path, session_id='audit', project_root=tmp_path,
                             inline_payload_bytes=1_000_000)
    stale = SessionJournal(path, session_id='audit', project_root=tmp_path)
    journal.append('probe', {'text': '\u6d4b' * 100_000})
    # Append must not fall back to a whole-file text read.
    monkeypatch.setattr(Path, 'read_text', lambda *a, **kw: pytest.fail('whole journal reread'))
    journal.append('probe', {'ok': True})
    with pytest.raises(SessionError, match='Session changed'):
        stale.append('probe', {})
    records = [json.loads(line) for line in path.read_bytes().splitlines()]
    assert [r['sequence'] for r in records] == [1, 2]


@pytest.mark.parametrize('suffix', [b'\n \t\r\n', b'\r\n'])
def test_journal_tail_accepts_blank_lines(tmp_path, suffix):
    path = tmp_path / 'journal.jsonl'
    journal = SessionJournal(path, session_id='audit', project_root=tmp_path)
    journal.append('probe', {})
    with path.open('ab') as stream:
        stream.write(suffix)
    journal.append('probe', {})
    assert journal.sequence == 2


@pytest.mark.parametrize('suffix', [b'{broken\n', b'null\n', b'\xff\n', b'{"sequence":1}'])
def test_journal_tail_refuses_corruption_without_mutating(tmp_path, suffix):
    path = tmp_path / 'journal.jsonl'
    journal = SessionJournal(path, session_id='audit', project_root=tmp_path)
    journal.append('probe', {})
    with path.open('ab') as stream:
        stream.write(suffix)
    before = path.read_bytes()
    with pytest.raises(SessionError):
        journal.append('probe', {})
    assert path.read_bytes() == before
    assert journal.sequence == 1


def test_reasoning_override_is_in_container_command(tmp_path):
    from benchmark.harbor.run_dataset import build_command
    command = build_command(harbor='harbor', dataset='offline', env_file=tmp_path / 'env',
                            output_dir=tmp_path, cache_dir=tmp_path, model='offline',
                            base_url='http://localhost', reasoning_effort='high')
    assert 'FORGECODE_REASONING_EFFORT=high' in command
    from benchmark.harbor.forgecode_agent import ForgeCodeHarborAgent
    script = ForgeCodeHarborAgent(logs_dir=tmp_path)._run_command('Offline test')
    assert 'export FORGE_REASONING_EFFORT="${FORGECODE_REASONING_EFFORT:-}"' in script


def test_delivery_review_reports_gaps_without_ending_or_mutating_task(tmp_path):
    import asyncio
    from forge.runtime.agent_loop import Conversation
    from forge.runtime.completion import TaskPolicy
    from forge.runtime.runner import TurnRunner
    from forge.runtime.state import ToolCall, ToolExecutionCompleted
    from forge.runtime.workspace import WorkspaceTracker
    from forge.tools.base import ToolRegistry
    from forge.tools.finish import ReviewDeliveryTool

    async def run():
        tracker = WorkspaceTracker(tmp_path)
        registry = ToolRegistry([ReviewDeliveryTool(tmp_path)], workspace_tracker=tracker)
        conversation = Conversation(client=object(), registry=registry, context_root=tmp_path,
                                    task_policy=TaskPolicy(require_verification=True), include_task_tools=False)
        original = conversation.task_manager.start('Inspect the output')
        runner = TurnRunner(conversation)
        runner.state.record_tool_request()
        events = [event async for event in runner._batch([ToolCall(0, 'review-1', 'review_delivery', {})])]
        result = next(event.result for event in events if isinstance(event, ToolExecutionCompleted))
        assert result.success  # Gaps are observations, not a tool execution failure.
        report = result.metadata['completion_report']
        assert report['unmet_requirements']
        assert report['verification_status'] == 'unverified'
        assert runner.terminal is None
        assert not runner.finished
        assert conversation.task_manager.active == original
        assert not tuple(tmp_path.rglob('*'))

    asyncio.run(run())
