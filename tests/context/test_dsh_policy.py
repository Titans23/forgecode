'''DSH policy boundaries, lossless artifacts and shared compaction budgets.'''

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from forge.context.compactor import CompactionConfig, cheap_compact, prune_tool_content
from forge.context.manager import ContextManager
from forge.runtime.model_budget import BudgetedModelClient
from forge.runtime.state import ModelTextDelta, ModelUsageUpdate, TokenUsage
from forge.runtime.turn_state import BudgetExhausted, TurnState


def test_256k_pressure_reserves_output_and_separate_headroom(tmp_path):
    config = CompactionConfig()
    assert config.pressure_threshold(262_144, 65_536) == 131_072
    assert int((262_144 - 65_536) * config.retain_ratio) == 31_457
    empty_size = len(json.dumps({'role': 'assistant', 'content': ''}))
    messages = [{'role': 'assistant', 'content': 'x' * (131_071 * 4 - empty_size)}]
    manager = ContextManager(messages, tmp_path)
    kwargs = dict(context_window_tokens=262_144, reserved_output_tokens=65_536)
    assert not manager.compaction_required(messages, **kwargs)
    # The fixed system envelope counts toward the same threshold, exactly once.
    assert manager.compaction_required(messages, system_prompt='xxxx', **kwargs)
    with pytest.raises(ValueError, match='headroom'):
        config.pressure_threshold(131_072, 65_536)


def test_pruner_preserves_unicode_rich_blocks_and_original_artifact(tmp_path):
    image = {'type': 'image', 'source': {'type': 'base64', 'data': 'original-image'}}
    blocks = [{'type': 'text', 'text': '😀' * 4_095},
              image, {'type': 'text', 'text': '中' * 5_000 + '🧪' * 1_024}]
    original = deepcopy(blocks)
    pruned = prune_tool_content(blocks, CompactionConfig())
    assert blocks == original
    assert pruned[1] == image
    assert ''.join(block['text'] for block in pruned if block['type'] == 'text') == (
        '😀' * 4_095 + '中' + '\n\n[... tool result middle pruned ...]\n\n' + '🧪' * 1_024)
    messages = [{'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'image', 'content': blocks}]}]
    manager = ContextManager(messages, tmp_path)
    prepared = manager.prepare(messages)
    assert messages[0]['content'][0]['content'] == original
    assert image in prepared[0]['content'][0]['content']
    artifact = manager.last_compaction.artifacts[0]
    assert json.loads(Path(artifact).read_text(encoding='utf-8')) == original
    assert manager.prepare(prepared) == prepared


def test_pruning_threshold_is_per_result_not_age_or_combined_size(tmp_path):
    messages = [{'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': str(index), 'content': 'x' * 8_192}
        for index in range(30)]}]
    result = cheap_compact(messages, tmp_path)
    assert result.messages == messages and result.artifacts == ()
    messages[0]['content'][0]['content'] += 'y'
    result = cheap_compact(messages, tmp_path)
    assert len(result.artifacts) == 1
    assert result.messages[0]['content'][1:] == messages[0]['content'][1:]
    assert result.messages[0]['content'][0]['content'].endswith('x' * 1_023 + 'y')


class Summarizer:
    def __init__(self, summaries):
        self.summaries = iter(summaries)
        self.inputs = []

    async def stream(self, messages, **kwargs):
        self.inputs.append(deepcopy(messages))
        yield ModelTextDelta(json.dumps(next(self.summaries)))
        yield ModelUsageUpdate(TokenUsage(100, 20))


def test_summary_selects_token_tail_not_six_messages_and_keeps_pairs(tmp_path):
    messages = [{'role': 'user', 'content': 'Never modify input.dat.'}]
    messages += [{'role': 'assistant', 'content': f'old-{index} ' + 'x' * 60_000}
                 for index in range(10)]
    pair = [
        {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'last', 'name': 'verify',
                                         'input': {'command': 'check'}}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'last',
                                    'content': 'failed check'}]},
    ]
    messages += pair
    original = deepcopy(messages)
    client = Summarizer([{'goal': 'repair', 'failed_attempts': ['failed check']}])
    manager = ContextManager(messages, tmp_path)
    report = asyncio.run(manager.compact_history(
        messages, client, context_window_tokens=262_144, reserved_output_tokens=65_536))
    assert report.success, report.reason
    # Three 15K-token messages plus the final atomic pair satisfy the 31,457-token tail.
    assert messages[-5:] == original[-5:]
    prompt = client.inputs[0][0]['content']
    assert 'old-6 ' in prompt and 'old-7 ' not in prompt
    assert 'Never modify input.dat.' in json.dumps(messages)
    saved = [json.loads(line) for line in Path(report.transcript_path).read_text().splitlines()]
    assert saved == original


@pytest.mark.parametrize('budget', [1, 2])
def test_pressure_retry_is_bounded_and_charges_shared_budget(tmp_path, budget):
    messages = [{'role': 'assistant', 'content': str(index) + 'x' * 40_000}
                for index in range(20)]
    original = deepcopy(messages)
    client = Summarizer([{'goal': 'repair', 'findings': ['y' * 400_000]}, {'goal': 'repair'}])
    state = TurnState(max_model_calls=budget)
    manager = ContextManager(messages, tmp_path)
    request = manager.compact_history(messages, BudgetedModelClient(client, state, 'compaction'),
                                     context_window_tokens=262_144, reserved_output_tokens=65_536)
    if budget == 1:
        with pytest.raises(BudgetExhausted):
            asyncio.run(request)
        assert messages == original
    else:
        report = asyncio.run(request)
        assert report.success, report.reason
        assert report.usage == TokenUsage(200, 40)
        assert not manager.compaction_required(messages, context_window_tokens=262_144,
                                               reserved_output_tokens=65_536)
    assert len(client.inputs) == state.model_calls == budget
    assert state.usage == TokenUsage(100 * budget, 20 * budget)


def test_pruning_can_remove_pressure_without_a_summary_request(tmp_path):
    messages = [{'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'huge', 'content': 'x' * 600_000}]}]
    client = Summarizer([])
    manager = ContextManager(messages, tmp_path)
    report = asyncio.run(manager.compact_history(
        messages, client, context_window_tokens=262_144, reserved_output_tokens=65_536))
    assert report is None and client.inputs == []
    assert len(messages[0]['content'][0]['content']) == 600_000


def test_two_summaries_still_above_threshold_preserve_original_history(tmp_path):
    messages = [{'role': 'assistant', 'content': str(index) + 'x' * 40_000}
                for index in range(20)]
    original = deepcopy(messages)
    client = Summarizer([{'goal': 'repair', 'findings': ['y' * size]}
                         for size in (600_000, 500_000)])
    report = asyncio.run(ContextManager(messages, tmp_path).compact_history(
        messages, client, context_window_tokens=262_144, reserved_output_tokens=65_536))
    assert not report.success and 'still above threshold' in report.reason
    assert len(client.inputs) == 2 and report.usage == TokenUsage(200, 40)
    assert messages == original


@pytest.mark.parametrize('failure', ['invalid_capacity', 'fixed_envelope', 'summary_error'])
def test_runtime_stops_before_normal_generation_when_compaction_cannot_fit(tmp_path, failure):
    from forge.runtime.agent_loop import Conversation
    from forge.runtime.state import TurnCompleted
    client = Summarizer([{}])  # Invalid summary; no normal-generation response is available.
    client.context_window = 131_072 if failure == 'invalid_capacity' else 262_144
    client.max_tokens = 65_536
    conversation = Conversation(
        client=client, context_root=tmp_path, task_relation='new',
        system_prompt='x' * 530_000 if failure == 'fixed_envelope' else 'Repair the code.',
    )
    if failure == 'summary_error':
        conversation.messages.extend(
            {'role': 'assistant', 'content': 'x' * 40_000} for _ in range(20))
    original = deepcopy(conversation.messages)

    async def collect():
        return [event async for event in conversation.stream('Keep the input unchanged.')]

    events = asyncio.run(collect())
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.stop_reason == 'context_compaction_failed'
    assert len(client.inputs) == (1 if failure == 'summary_error' else 0)
    assert conversation.messages[:len(original)] == original
