'''Tests for the compact append-only M1 trajectory recorder.'''

import json
from pathlib import Path

from forge.runtime.state import (
    CompletionBlocked,
    ModelCallCompleted,
    ModelCallStarted,
    ModelRetryScheduled,
    ModelResponseCompleted,
    ModelToolCallCompleted,
    ModelUsageUpdate,
    TokenUsage,
    ToolCall,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
    TurnResult,
    VerificationCompleted,
    VerificationEvidence,
    WorkspaceChanged,
)
from forge.sessions.trajectory import TrajectoryRecorder
from forge.tools.base import ToolResult


def test_journal_atif_resolves_payloads_and_distinguishes_closure_from_completeness(tmp_path):
    import asyncio
    from forge.sessions.store import SessionStore
    from forge.sessions.trajectory import journal_events, journal_completeness, journal_to_atif
    from forge.runtime.model_budget import BudgetedModelClient
    from forge.runtime.turn_state import TurnState
    from forge.runtime.state import ModelToolCallStarted, ModelToolCallArgumentsDelta, ModelTextDelta
    from harbor.models.trajectories.trajectory import Trajectory
    journal = SessionStore(tmp_path, data_root=tmp_path / 'data').create(model='offline-fixture')
    journal.inline_payload_bytes = 64
    journal.record_turn_started('Use api_key=fixture-secret', None)
    state = TurnState(request_event_sink=journal.append)
    class Client:
        model = 'offline-fixture'
        async def stream(self, **kwargs):
            yield ModelTextDelta('Visible response')
            yield ModelToolCallStarted(0, 'call', 'read_file')
            yield ModelToolCallArgumentsDelta(0, '{"path":"README.md"}')
            yield ModelToolCallCompleted(ToolCall(0, 'call', 'read_file', {'path': 'README.md'}))
            yield ModelResponseCompleted('tool_use')
    async def run():
        async for _ in BudgetedModelClient(Client(), state).stream([{'role': 'user', 'content': 'api_key=fixture-secret'}]): pass
    asyncio.run(run())
    journal.record_tool_started('call', 'read_file', {'path': 'README.md'})
    journal.record_tool_completed('call', 'read_file', True, duration_seconds=.2, exit_code=0)
    journal.record_tool_result_message({'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'call', 'content': 'file contents'}]}, None)
    journal.append('turn_completed', {'status': 'completed'})
    events = journal_events(journal.path)
    assert journal_completeness(events)['record_closed']
    assert journal_completeness(events)['responses_complete']
    assert not journal_completeness(events)['training_eligible']
    before = journal.path.read_bytes()
    atif = journal_to_atif(journal.path)
    Trajectory.model_validate(atif)
    assert atif['schema_version'] == 'ATIF-v1.7' and 'fixture-secret' not in json.dumps(atif)
    agent = next(step for step in atif['steps'] if step['source'] == 'agent')
    assert agent['message'] == 'Visible response'
    assert agent['observation']['results'][0]['extra']['duration_seconds'] == .2
    assert agent['observation']['results'][0]['extra']['exit_code'] == 0
    assert journal.path.read_bytes() == before
    incomplete = [e for e in events if e['type'] != 'model_request_finished']
    assert not journal_completeness(incomplete)['record_closed']
    legacy = [{**e, 'payload': {k: v for k, v in e['payload'].items() if k != 'response_complete'}} for e in events]
    assert journal_completeness(legacy)['record_closed'] and not journal_completeness(legacy)['responses_complete']
    journal.record_turn_started('interrupted', None)
    journal.append('model_request_started', {'request_id': 'unfinished'})
    journal.append('model_request_chunk', {'request_id': 'unfinished', 'text': 'partial text'})
    journal.append('model_tool_chunk', {'request_id': 'unfinished', 'index': 0, 'partial_json': '{"path":'})
    partial = journal_to_atif(journal.path)['steps'][-1]
    assert partial['message'] == 'partial text' and partial['extra']['incomplete']
    assert partial['extra']['partial_tools']['0']['partial_json'] == '{"path":'


def test_trajectory_records_lifecycle_without_large_tool_content(
    tmp_path: Path,
) -> None:
    recorder = TrajectoryRecorder.create(tmp_path)
    call = ToolCall(
        index=0,
        id='toolu_read',
        name='read_file',
        arguments={'path': 'README.md', 'api_key': 'secret-value'},
    )
    recorder.record_user_message('Use API_KEY=secret-value')
    recorder.record_event(ModelCallStarted(iteration=1))
    recorder.record_event(
        ModelRetryScheduled(
            attempt=2,
            reason='rate_limit',
            delay_seconds=0.5,
        )
    )
    recorder.record_event(
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=10, output_tokens=2),
            request_usage=TokenUsage(input_tokens=6, output_tokens=2),
            model_calls=2,
        )
    )
    recorder.record_event(ModelToolCallCompleted(tool_call=call))
    recorder.record_event(ModelResponseCompleted(stop_reason='tool_use'))
    recorder.record_event(ToolExecutionStarted(tool_call=call))
    recorder.record_event(
        ToolExecutionCompleted(
            tool_call=call,
            result=ToolResult.ok(
                'Read file.',
                content='large or sensitive file contents are omitted',
            ),
        )
    )
    recorder.record_event(ModelCallCompleted(iteration=1))
    evidence = VerificationEvidence(
        command='pytest',
        cwd='.',
        exit_code=0,
        duration_seconds=0.25,
        timed_out=False,
        workspace_revision=1,
    )
    recorder.record_event(WorkspaceChanged(revision=1, paths=('a.py',)))
    recorder.record_event(VerificationCompleted(evidence=evidence))
    recorder.record_event(
        CompletionBlocked(attempt=1, reasons=('verification missing',))
    )
    recorder.record_event(
        TurnCompleted(
            result=TurnResult(
                text='Finished',
                usage=TokenUsage(input_tokens=10, output_tokens=2),
                last_request_usage=TokenUsage(
                    input_tokens=6,
                    output_tokens=2,
                ),
                model_calls=2,
                tool_calls=(call,),
                changed_paths=('a.py',),
                verification=evidence,
            )
        )
    )

    records = [
        json.loads(line)
        for line in recorder.path.read_text(encoding='utf-8').splitlines()
    ]

    assert [record['type'] for record in records] == [
        'session_started',
        'user_message',
        'model_call_started',
        'model_retry_scheduled',
        'tool_requested',
        'model_response_completed',
        'tool_execution_started',
        'tool_execution_completed',
        'model_call_completed',
        'workspace_changed',
        'verification_completed',
        'completion_blocked',
        'turn_completed',
    ]
    serialized = json.dumps(records, ensure_ascii=False)
    assert 'secret-value' not in serialized
    assert '[REDACTED]' in serialized
    assert 'large or sensitive file contents are omitted' not in serialized
    completed = next(
        record
        for record in records
        if record['type'] == 'model_call_completed'
    )
    assert completed['turn_usage']['input_tokens'] == 10
    assert completed['turn_usage']['output_tokens'] == 2
    assert completed['turn_usage']['last_request']['input_tokens'] == 6
    assert completed['turn_usage']['model_calls'] == 2
    assert completed['duration_seconds'] is not None
    response = next(
        record
        for record in records
        if record['type'] == 'model_response_completed'
    )
    assert response['stop_reason'] == 'tool_use'
    turn = records[-1]
    assert turn['status'] == 'completed'
    assert turn['last_request_usage']['input_tokens'] == 6
    assert turn['model_calls'] == 2
    assert turn['changed_paths'] == ['a.py']
    assert turn['verification']['workspace_revision'] == 1
