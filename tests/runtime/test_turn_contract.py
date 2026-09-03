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

from forge.permissions.policy import PermissionManager
from forge.runtime.agent_loop import Conversation
from forge.runtime.completion import CompletionGate, TaskPolicy
from forge.runtime.executor import ToolExecutor
from forge.runtime.router import RouteResult, TurnDecision
from forge.runtime.state import (
    ModelTextDelta, ModelToolCallCompleted, ModelUsageUpdate,
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
