'''End-to-end contracts derived from the first runtime-v2 benchmark audit.

These deliberately exercise Conversation.stream, not just the helper objects:
an execution boundary can be correct while its caller still cancels good work.
'''

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any
import pytest

from forge.permissions.policy import PermissionManager
from forge.runtime.agent_loop import Conversation
from forge.runtime.completion import CompletionGate, TaskPolicy
from forge.runtime.executor import ToolExecutor
from forge.runtime.router import RouteResult, TurnDecision
from forge.runtime.state import (
    ModelTextDelta, ModelToolCallCompleted, ModelUsageUpdate,
    ModelReasoningDelta,
    TokenUsage, ToolCall, ToolExecutionCompleted, TurnCompleted,
    VerificationEvidence,
)
from forge.sessions.store import SessionStore
from forge.tools import create_default_registry


class ScriptedClient:
    provider = 'contract-test'

    def __init__(self, *responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def stream(self, messages, tools=None, system=None):
        self.calls.append({'messages': messages, 'tools': tools, 'system': system})
        assert self.responses, 'Kernel requested an unplanned model response.'
        for event in self.responses.pop(0):
            if isinstance(event, BaseException):
                raise event
            yield event


def request(*calls: ToolCall) -> list[Any]:
    return [
        ModelUsageUpdate(TokenUsage(11, 3)),
        *(ModelToolCallCompleted(call) for call in calls),
    ]


def stop() -> list[Any]:
    return request(ToolCall(0, 'stop', 'finish_task', {
        'task_kind': 'change', 'status': 'failed',
        'summary': 'End of the deterministic probe.',
        'blocked_reasons': ['The probe intentionally stops here.'],
    }))


def python_command(code: str) -> str:
    argv = [sys.executable, '-c', code]
    return subprocess.list2cmdline(argv) if sys.platform == 'win32' else shlex.join(argv)


def exercise(tmp_path: Path, client: ScriptedClient, **kwargs):
    conversation = Conversation(
        client=client, registry=create_default_registry(tmp_path),
        context_root=tmp_path, max_iterations=4, **kwargs,
    )

    async def collect():
        return [event async for event in conversation.stream('Run the execution contract probe.')]

    return conversation, asyncio.run(collect())


def tool_results(events):
    return {
        event.tool_call.id: event.result
        for event in events if isinstance(event, ToolExecutionCompleted)
    }


def test_reasoning_is_visible_as_an_event_without_becoming_the_answer(tmp_path):
    client = ScriptedClient([ModelUsageUpdate(TokenUsage(10, 3)),
        ModelReasoningDelta('Inspect the two numbers.'), ModelTextDelta('2')])
    conversation, events = exercise(tmp_path, client)
    assert any(isinstance(e, ModelReasoningDelta) for e in events)
    assert events[-1].result.text == '2'
    assert 'Inspect the two numbers.' not in json.dumps(conversation.messages)


def test_visible_reasoning_prevents_empty_response_recovery(tmp_path):
    from forge.runtime.state import ModelProtocolError
    client = ScriptedClient([ModelReasoningDelta('Partial reasoning'), ModelProtocolError('stream lost')])
    _, events = exercise(tmp_path, client)
    assert len(client.calls) == 1 and events[-1].result.status == 'failed'


def test_successful_edit_does_not_cancel_following_process(tmp_path: Path) -> None:
    client = ScriptedClient(request(
        ToolCall(0, 'write', 'write_file', {'path': 'value.txt', 'content': 'B'}),
        ToolCall(1, 'observe', 'run_command', {'command': python_command(
            "from pathlib import Path; print(Path('value.txt').read_text())"
        )}),
    ), stop())
    _, events = exercise(tmp_path, client)
    result = tool_results(events)['observe']
    assert result.metadata['execution_status'] == 'executed'
    assert result.success and 'B' in result.content


def test_failed_process_cancels_tail_but_allows_replanning(tmp_path: Path) -> None:
    client = ScriptedClient(request(
        ToolCall(0, 'failure', 'run_command', {'command': python_command('raise SystemExit(7)')}),
        ToolCall(1, 'tail', 'write_file', {'path': 'must-not-exist.txt', 'content': 'wrong'}),
    ), request(
        ToolCall(0, 'retry', 'run_command', {'command': python_command("print('replanned')")}),
    ), stop())
    _, events = exercise(tmp_path, client)
    results = tool_results(events)
    assert not results['failure'].success
    assert not (tmp_path / 'must-not-exist.txt').exists()
    assert results['tail'].metadata['execution_status'] == 'cancelled'
    assert results['retry'].success


def test_permission_denial_returns_evidence_and_model_can_replan(tmp_path: Path) -> None:
    client = ScriptedClient(
        request(ToolCall(0, 'denied-write', 'write_file', {
            'path': 'value.txt', 'content': 'not authorized',
        })),
        request(ToolCall(0, 'blocked', 'finish_task', {
            'task_kind': 'change', 'status': 'blocked',
            'summary': 'The requested write was not authorized.',
            'blocked_reasons': ['Approval is unavailable.'],
        })),
    )
    registry = create_default_registry(tmp_path)
    conversation = Conversation(
        client=client, registry=registry, context_root=tmp_path,
        permission_manager=PermissionManager(
            tmp_path, mode='supervised',
            user_path=tmp_path / 'user-permissions.json',
        ),
    )

    async def collect():
        return [event async for event in conversation.stream('Write value.txt.')]

    events = asyncio.run(collect())
    assert len(client.calls) == 2
    assert tool_results(events)['denied-write'].error.code == 'permission_denied'
    final = next(event.result for event in events if isinstance(event, TurnCompleted))
    assert final.status == 'blocked'
    assert not (tmp_path / 'value.txt').exists()


def test_kernel_persists_final_statistics_without_event_consumer(tmp_path: Path) -> None:
    store = SessionStore(tmp_path, data_root=tmp_path / '.forge-data')
    journal = store.create(model='contract-test')
    conversation = Conversation(
        client=ScriptedClient([ModelUsageUpdate(TokenUsage(11, 3)), ModelTextDelta('done')]),
        context_root=tmp_path, session_journal=journal,
    )

    async def collect():
        return [event async for event in conversation.stream('Answer briefly.')]

    events = asyncio.run(collect())
    final = next(event.result for event in events if isinstance(event, TurnCompleted))
    records = [json.loads(line) for line in journal.path.read_text(encoding='utf-8').splitlines()]
    completed = [record for record in records if record['type'] == 'turn_completed']
    assert len(completed) == 1
    assert completed[0]['payload']['statistics'] == final.statistics
    assert final.statistics['model_requests'] == 1


def test_router_consumes_the_same_model_budget(tmp_path: Path) -> None:
    class Router:
        async def route(self, prompt, active_task, recent_messages):
            return RouteResult(
                decision=TurnDecision(
                    intent='read_only', task_relation='none',
                    requires_workspace_change=False, confidence=1, reason='probe',
                ),
                usage=TokenUsage(7, 2),
            )

    client = ScriptedClient([
        ModelUsageUpdate(TokenUsage(11, 3)),
        ModelTextDelta('This request exceeds the budget.'),
    ])
    conversation = Conversation(
        client=client, context_root=tmp_path, intent_router=Router(), max_iterations=1,
    )

    async def collect():
        return [event async for event in conversation.stream('Inspect the repository.')]

    events = asyncio.run(collect())
    final = next(event.result for event in events if isinstance(event, TurnCompleted))
    assert not client.calls
    assert final.status == 'failed'
    assert final.stop_reason == 'model_budget_exhausted'
    assert final.usage == TokenUsage(7, 2)


@pytest.mark.parametrize('mode', [None, 'full-access'])
def test_inferred_inspection_does_not_add_an_execution_fence(tmp_path, mode):
    class Router:
        async def route(self, *args, **kwargs):
            return RouteResult(decision=TurnDecision(intent='read_only', task_relation='none',
                requires_workspace_change=False, confidence=1, reason='diagnosis'), usage=TokenUsage(0, 0))
    client = ScriptedClient(request(ToolCall(0, 'inspect', 'run_command', {
        'command': python_command("print('diagnostic command')")})),
        [ModelUsageUpdate(TokenUsage(1, 1)), ModelTextDelta('Inspected.')])
    _, events = exercise(tmp_path, client, intent_router=Router(),
        permission_manager=PermissionManager(tmp_path, mode='ask', execution_mode=mode, load_stored_rules=False))
    result = tool_results(events)['inspect']
    if mode is None:
        assert result.error.code == 'read_only_intent'
    else:
        assert result.success and 'diagnostic command' in result.content


def test_completion_requires_every_explicit_check(tmp_path: Path) -> None:
    async def check():
        tracker = create_default_registry(tmp_path).workspace_tracker
        await tracker.begin_turn()
        first = VerificationEvidence('pytest unit', '.', 0, .01, False, 0)
        gate = CompletionGate(tmp_path, TaskPolicy(
            required_verification_commands=('pytest unit', 'pytest integration'),
        ))
        missing = await gate.evaluate(tracker, first, mutation_attempted=False)
        assert not missing.allowed
        second = VerificationEvidence('pytest integration', '.', 0, .01, False, 0)
        complete = await gate.evaluate(
            tracker, second, verification_history=(first, second), mutation_attempted=False,
        )
        assert complete.allowed

    asyncio.run(check())


def test_coverage_label_alone_cannot_resolve_a_different_failed_check(tmp_path: Path) -> None:
    async def check():
        tracker = create_default_registry(tmp_path).workspace_tracker
        await tracker.begin_turn()
        failed = VerificationEvidence(
            'python weak_check.py', '.', 1, .01, False, 0,
            coverage=('rendered output matches reference',),
        )
        corrected = VerificationEvidence(
            'python corrected_check.py', '.', 0, .01, False, 0,
            coverage=('rendered output matches reference',),
        )
        unrelated = VerificationEvidence(
            'python syntax_check.py', '.', 0, .01, False, 0,
            coverage=('program parses',),
        )
        gate = CompletionGate(tmp_path, TaskPolicy(require_verification=True))
        still_failed = await gate.evaluate(
            tracker, unrelated, verification_history=(failed, unrelated),
            mutation_attempted=False,
        )
        assert not still_failed.allowed
        resolved = await gate.evaluate(
            tracker, corrected, verification_history=(failed, unrelated, corrected),
            mutation_attempted=False,
        )
        # Same natural-language label is not proof of an equivalent check.
        # A real script edit is handled by workspace revision invalidation.
        assert not resolved.allowed
        rerun = VerificationEvidence(
            'python weak_check.py', '.', 0, .01, False, 0,
            coverage=('rendered output matches reference',),
        )
        resolved = await gate.evaluate(
            tracker, rerun, verification_history=(failed, unrelated, corrected, rerun),
            mutation_attempted=False,
        )
        assert resolved.allowed

    asyncio.run(check())


def test_read_only_execution_does_not_rescan_workspace(tmp_path: Path) -> None:
    async def check():
        registry = create_default_registry(tmp_path)
        tracker = registry.workspace_tracker
        await tracker.begin_turn()
        scans = 0
        refresh = tracker.refresh

        async def counted_refresh():
            nonlocal scans
            scans += 1
            return await refresh()

        tracker.refresh = counted_refresh
        executor = ToolExecutor(registry, PermissionManager(
            tmp_path, user_path=tmp_path / 'user-permissions.json',
        ), workspace_tracker=tracker)
        outcome = await executor.execute(ToolCall(0, 'read', 'list_directory', {}))
        assert outcome.result.success
        assert scans == 0

    asyncio.run(check())


@pytest.mark.parametrize('read_file', [False, True])
def test_plain_turn_streams_and_finishes_without_full_workspace_scan(tmp_path, read_file):
    (tmp_path / 'sample.txt').write_text('readable')
    responses = ([request(ToolCall(0, 'read', 'read_file', {'path': 'sample.txt'}))] if read_file else [])
    client = ScriptedClient(*responses, [*request(), ModelTextDelta('ready')], [*request(), ModelTextDelta('next turn')])
    registry = create_default_registry(tmp_path)
    async def unexpected_capture():
        pytest.fail('Plain text and read-only tools must not scan the entire workspace')
    registry.workspace_tracker._capture = unexpected_capture
    conversation = Conversation(client=client, registry=registry, context_root=tmp_path)
    async def check():
        for prompt in ('Read or answer.', 'Another question.'):
            events = [event async for event in conversation.stream(prompt)]
            assert events[-1].result.status == 'completed'
            assert registry.workspace_tracker.baseline_pending
            assert not registry.workspace_tracker.available
            assert registry.workspace_tracker.changed_paths == ()
        assert all('Git is unavailable' not in call['system'] for call in client.calls)
    asyncio.run(check())


@pytest.mark.parametrize('effect', ['workspace_write', 'process'])
def test_baseline_precedes_first_effect_but_not_first_text(tmp_path, effect):
    (tmp_path / 'user.txt').write_text('original')
    registry = create_default_registry(tmp_path)
    tracker = registry.workspace_tracker
    capture, scans = tracker._capture, []
    async def counted_capture():
        scans.append(True)
        if len(scans) == 1:
            assert not (tmp_path / 'agent.txt').exists()
        return await capture()
    tracker._capture = counted_capture
    call = (ToolCall(0, 'edit', 'write_file', {'path': 'agent.txt', 'content': 'agent'})
        if effect == 'workspace_write' else ToolCall(0, 'command', 'run_command',
            {'command': python_command("print('command executed')")}))
    class Client(ScriptedClient):
        async def stream(self, *args, **kwargs):
            if not self.calls:
                assert not scans
                (tmp_path / 'user.txt').write_text('external edit while model is thinking')
            async for event in super().stream(*args, **kwargs):
                yield event
    client = Client([ModelTextDelta('Starting now.'), *request(call)], [*request(), ModelTextDelta('Done.')])
    conversation = Conversation(client=client, registry=registry, context_root=tmp_path,
        permission_manager=PermissionManager(tmp_path, mode='auto', load_stored_rules=False))
    async def check():
        events = [event async for event in conversation.stream('Create agent.txt.')]
        assert tool_results(events)[call.id].success
        assert tracker.changed_paths == (('agent.txt',) if effect == 'workspace_write' else ())
        assert scans and tracker.environment_epoch == (1 if effect == 'process' else 0)
        assert not tracker.baseline_pending and tracker.available
        assert (tmp_path / 'user.txt').read_text() == 'external edit while model is thinking'
    asyncio.run(check())


def test_denied_first_mutation_never_starts_workspace_scan(tmp_path):
    registry = create_default_registry(tmp_path)
    async def unexpected_capture():
        pytest.fail('A denied tool must not trigger a deferred workspace baseline')
    registry.workspace_tracker._capture = unexpected_capture
    client = ScriptedClient(request(ToolCall(0, 'denied', 'write_file', {'path': 'no.txt', 'content': 'no'})),
                            [*request(), ModelTextDelta('Permission was denied.')])
    conversation = Conversation(client=client, registry=registry, context_root=tmp_path,
        permission_manager=PermissionManager(tmp_path, mode='plan', load_stored_rules=False))
    async def check():
        events = [event async for event in conversation.stream('Create no.txt.')]
        assert tool_results(events)['denied'].error.code == 'permission_denied'
        assert not (tmp_path / 'no.txt').exists()
    asyncio.run(check())


def test_explicit_acceptance_captures_baseline_before_model(tmp_path):
    registry = create_default_registry(tmp_path)
    class Client(ScriptedClient):
        async def stream(self, *args, **kwargs):
            assert not registry.workspace_tracker.baseline_pending
            assert registry.workspace_tracker.available
            async for event in super().stream(*args, **kwargs):
                yield event
    conversation = Conversation(client=Client([*request(), ModelTextDelta('No changes made.')]), registry=registry,
        context_root=tmp_path, task_policy=TaskPolicy(require_changes=True))
    async def check():
        events = [event async for event in conversation.stream('A required change.')]
        assert events[-1].result.status == 'partial'
        assert 'Diff is empty' in str(events[-1].result)
    asyncio.run(check())


def test_cancel_during_deferred_baseline_never_executes_tool(tmp_path):
    async def check():
        registry = create_default_registry(tmp_path)
        tracker = registry.workspace_tracker
        await tracker.begin_turn(deferred=True)
        scanning = asyncio.Event()
        async def capture():
            scanning.set()
            await asyncio.Event().wait()
        tracker._capture = capture
        executor = ToolExecutor(registry, PermissionManager(tmp_path, mode='auto', load_stored_rules=False),
                                workspace_tracker=tracker)
        task = asyncio.create_task(executor.execute(ToolCall(0, 'edit', 'write_file', {'path': 'no.txt', 'content': 'no'})))
        await asyncio.wait_for(scanning.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not (tmp_path / 'no.txt').exists()
        assert tracker.baseline_pending and not tracker.available
    asyncio.run(check())
