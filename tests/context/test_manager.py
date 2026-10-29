'''Tests for provider-neutral context accounting.'''

from pathlib import Path

from forge.context.compactor import CompactionConfig
from forge.context.manager import ContextManager, ContextStats, context_stats


def test_large_tool_result_is_bounded_before_history_storage(
    tmp_path: Path,
) -> None:
    manager = ContextManager(
        [],
        tmp_path,
        CompactionConfig(),
    )
    message = {
        'role': 'user',
        'content': [
            {
                'type': 'tool_result',
                'tool_use_id': 'large-result',
                'content': 'x' * 9_000,
            }
        ],
    }

    artifacts = manager.persist_tool_result_message(message)

    assert artifacts
    stored = message['content'][0]['content']
    assert stored.startswith('[ForgeCode stored a large tool result]')
    assert Path(artifacts[0]).read_text(encoding='utf-8') == 'x' * 9_000


def test_context_stats_count_messages_and_tool_results() -> None:
    messages = [
        {'role': 'user', 'content': 'hello'},
        {
            'role': 'user',
            'content': [
                {
                    'type': 'tool_result',
                    'tool_use_id': 'toolu_1',
                    'content': 'result text',
                }
            ],
        },
    ]

    stats = context_stats(messages)

    assert stats.message_count == 2
    assert stats.tool_result_characters == len('result text')
    assert stats.estimated_characters > stats.tool_result_characters
    assert stats.estimated_tokens == (stats.estimated_characters + 3) // 4


def test_context_manager_reflects_mutated_history() -> None:
    messages: list[dict[str, object]] = []
    manager = ContextManager(messages)

    messages.append({'role': 'user', 'content': 'new message'})

    assert manager.stats.message_count == 1


def test_context_stats_calculate_categories_and_remaining_window() -> None:
    stats = ContextStats(
        message_count=2,
        estimated_characters=400,
        tool_result_characters=40,
        system_characters=400,
        repository_characters=200,
        tool_schema_characters=100,
        context_window_tokens=1_000,
        reserved_output_tokens=100,
    )

    assert stats.history_tokens == 100
    assert stats.system_tokens == 100
    assert stats.repository_tokens == 50
    assert stats.tool_schema_tokens == 25
    assert stats.estimated_tokens == 275
    assert stats.projected_tokens == 375
    assert stats.remaining_tokens == 625
    assert stats.utilization == 0.375


def test_remaining_window_is_unavailable_without_configuration() -> None:
    stats = ContextStats(1, 100, 0, reserved_output_tokens=8_192)

    assert stats.remaining_tokens is None
    assert stats.utilization is None


def test_request_stats_use_cheap_compaction_and_keep_stored_totals() -> None:
    messages = [
        {'role': 'assistant', 'content': f'message {index} ' + 'x' * 100}
        for index in range(10)
    ]
    manager = ContextManager(
        messages,
        config=CompactionConfig(
            message_limit=4,
            keep_first_messages=1,
            keep_recent_messages=2,
        ),
    )

    stats = manager.stats_for_request(
        system_prompt='system',
        repository_context='',
        tools=None,
        context_window_tokens=1_000,
        reserved_output_tokens=100,
    )

    assert stats.stored_messages == 10
    assert stats.message_count == 4
    assert stats.stored_characters > stats.estimated_characters
    assert stats.projected_tokens == (
        stats.estimated_tokens + stats.reserved_output_tokens
    )


def test_compaction_accounts_for_request_envelope_and_single_response_reserve(tmp_path):
    messages = [{'role': 'user', 'content': 'short request'}]
    context = ContextManager(messages, tmp_path, CompactionConfig(reserve_tokens=512, keep_recent_tokens=200))
    assert not context.compaction_required(messages, context_window_tokens=4096, reserved_output_tokens=1024)
    assert context.compaction_required(messages, system_prompt='s' * 15_000,
        context_window_tokens=4096, reserved_output_tokens=1024)
    import pytest
    with pytest.raises(ValueError, match='reserve'):
        context.compaction_required(messages, context_window_tokens=512, reserved_output_tokens=100)
    assert messages == [{'role': 'user', 'content': 'short request'}]


def test_cheap_crop_preserves_user_constraints_without_a_model_summary(tmp_path):
    from forge.context.manager import original_user_instructions
    constraints = ['Do not modify supplied inputs.', 'Keep all output inside this workspace.']
    messages = [{'role': 'user', 'content': text} for text in constraints]
    messages += [{'role': 'assistant', 'content': 'Inspecting.'}] * 10
    context = ContextManager(messages, tmp_path, CompactionConfig(message_limit=4, keep_recent_messages=2))
    prepared = context.prepare(messages)
    assert original_user_instructions(prepared) == constraints
    assert len(prepared) < len(messages)
    assert messages[:2] == [{'role': 'user', 'content': text} for text in constraints]
