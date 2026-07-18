'''Tests for provider-neutral context accounting.'''

from forge.context.manager import ContextManager, ContextStats, context_stats


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
    assert stats.remaining_tokens == 625
    assert stats.utilization == 0.275


def test_remaining_window_is_unavailable_without_configuration() -> None:
    stats = ContextStats(1, 100, 0, reserved_output_tokens=8_192)

    assert stats.remaining_tokens is None
    assert stats.utilization is None
