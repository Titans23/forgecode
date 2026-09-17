"""Offline fault probes discovered during the 2026-09-15 harness audit.

Assertions express the desired invariant. Failures are audit findings, not
permission to weaken an assertion. These tests never contact a model service.
"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from forge.config import ForgeConfig
from forge.runtime.agent_loop import Conversation
from forge.runtime.completion import checker_revision_covers, unresolved_verification_failures
from forge.runtime.delivery import completion_report
from forge.runtime.model_budget import BudgetedModelClient
from forge.runtime.model_client import ModelOutputTruncatedError
from forge.runtime.providers import NativeModelClient
from forge.runtime.runner import TurnRunner
from forge.runtime.state import ToolCall, VerificationEvidence
from forge.runtime.turn_state import TurnState
from forge.runtime.verification import verification_quality
from forge.tools.base import Tool, ToolInput, ToolRegistry, ToolResult


def test_child_dispatch_must_enforce_exhausted_parent_tool_budget(tmp_path):
    calls = []

    class Probe(Tool[ToolInput]):
        name = 'audit_read'
        description = 'Offline read-only counter'
        input_model = ToolInput

        async def execute(self, arguments):
            calls.append('executed')
            return ToolResult.ok('read')

    async def run():
        parent = TurnState(max_tool_calls=1)
        parent.record_tool_request()  # Parent has consumed its final slot.
        conversation = Conversation(client=object(), context_root=tmp_path,
            registry=ToolRegistry([Probe(tmp_path)]), include_task_tools=False)
        runner = TurnRunner(conversation)
        runner.state.parent = parent
        runner.state.record_tool_request()  # Actual model event accounting.
        assert not parent.can_request_tool_batch(1)
        async for _ in runner._batch([ToolCall(0, 'child-call', 'audit_read', {})]):
            pass
        assert calls == [], 'Child execution crossed the exhausted parent tool budget'

    asyncio.run(run())


class OfflineStream:
    def __init__(self, events):
        self.events = events

    async def __aiter__(self):
        for event in self.events:
            yield event

    async def close(self):
        pass


@pytest.mark.parametrize('provider', ['deepseek', 'openai_responses'])
def test_truncated_native_response_preserves_known_usage(provider):
    async def run():
        if provider == 'deepseek':
            delta = SimpleNamespace(content=None, tool_calls=None)
            usage = SimpleNamespace(prompt_tokens=91, completion_tokens=17,
                                    prompt_cache_hit_tokens=0)
            events = [SimpleNamespace(usage=usage, choices=[SimpleNamespace(
                index=0, delta=delta, finish_reason='length')])]
            sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
                create=AsyncMock(return_value=OfflineStream(events)))))
        else:
            usage = SimpleNamespace(input_tokens=91, output_tokens=17,
                                    input_tokens_details=SimpleNamespace(cached_tokens=0))
            events = [SimpleNamespace(type='response.incomplete', response=SimpleNamespace(
                status='incomplete', usage=usage, output=[]))]
            sdk = SimpleNamespace(responses=SimpleNamespace(
                create=AsyncMock(return_value=OfflineStream(events))))
        client = NativeModelClient(ForgeConfig(api_key='offline-fault-probe',
            model_id='audit-' + provider, provider=provider), client=sdk)
        state = TurnState()
        with pytest.raises(ModelOutputTruncatedError):
            async for _ in BudgetedModelClient(client, state).stream([]):
                pass
        assert state.model_calls == 1
        assert state.usage.total_input_tokens == 91, 'Provider-supplied usage was discarded on truncation'
        assert state.usage.output_tokens == 17
        assert state.unknown_usage_requests == 0

    asyncio.run(run())


def test_delivery_report_agrees_with_explicit_checker_supersession():
    check = {'key': 'value', 'operator': 'eq', 'expected': 42,
             'requirement': 'value 42', 'requirement_id': 'req-1',
             'expected_source': 'original source'}
    old = VerificationEvidence('python broken.py', '.', 1, .1, False, 0,
        verification_id='old-run', output_checks=(check,), requirement_ids=('req-1',))
    fixed = replace(old, command='python fixed.py', exit_code=0,
        verification_id='fixed-run', asserted_requirement_ids=('req-1',),
        supersedes=('old-run',), revision_reason='repair checker')
    assert checker_revision_covers(old, fixed)
    assert unresolved_verification_failures((old, fixed)) == ()
    report = completion_report(status='completed', evidence=(old, fixed),
        workspace_revision=0, environment_epoch=0, reasons=(), has_contract=True)
    assert report.failed_checks == (), 'Delivery reports a failed check already resolved by the gate'
    assert report.verification_status == 'passed_checks'


def test_process_reasoning_override_reaches_benchmark_configuration(tmp_path, monkeypatch):
    from benchmark.harbor.run_dataset import configured_values
    env_file = tmp_path / 'audit-settings.txt'
    env_file.write_text('MODEL_ID=audit-model\n', encoding='utf-8')
    monkeypatch.setenv('FORGE_REASONING_EFFORT', 'high')
    values = configured_values(env_file)
    assert values.get('FORGE_REASONING_EFFORT') == 'high', 'Benchmark ignores a process-level reasoning setting'


def test_single_python_heredoc_is_not_an_exit_masking_shell_chain():
    # Reduced from circuit-fibsqrt's legitimate stdin-driven Python checker.
    # No execution: only the static classification used by the completion gate.
    command = "python3 - <<'PY'\nimport json\nassert 2 + 2 == 4\nprint(json.dumps({'ok': True}))\nPY"
    assert verification_quality(command) == 'behavior', 'Heredoc body newlines were mistaken for shell chaining'
