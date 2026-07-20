'''Tests for the thin Anthropic SDK adapter.'''

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from forge.config import ForgeConfig
import forge.runtime.model_client as model_client_module
from forge.runtime.model_client import (
    AnthropicModelClient,
    ModelClient,
    ModelOutputTruncatedError,
    ModelProtocolError,
)
from forge.runtime.state import (
    ModelTextDelta,
    ModelToolCallArgumentsDelta,
    ModelToolCallCompleted,
    ModelToolCallStarted,
    ModelRetryScheduled,
    ModelResponseCompleted,
    ModelUsageUpdate,
    TokenUsage,
    ToolCall,
)


def usage(
    *,
    input_tokens: int | None,
    output_tokens: int,
    cache_creation_input_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
    )


class FakeStream:
    def __init__(self, events: list[Any], final_message: Any) -> None:
        self.events = events
        self.final_message = final_message

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def __aiter__(self) -> Any:
        async def iterate() -> Any:
            for event in self.events:
                if isinstance(event, Exception):
                    raise event
                yield event

        return iterate()

    async def get_final_message(self) -> Any:
        return self.final_message


class FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.events: list[Any] = []
        self.errors: list[Exception] = []
        self.final_message = SimpleNamespace(
            usage=usage(input_tokens=0, output_tokens=0),
            stop_reason='end_turn',
            content=[SimpleNamespace(type='text', text='ok')],
        )

    def stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        return FakeStream(self.events, self.final_message)


class FakeAnthropic:
    def __init__(self) -> None:
        self.messages = FakeMessages()


def test_client_passes_explicit_config_to_anthropic_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeAnthropic()
    constructor_calls: list[dict[str, Any]] = []

    def create_sdk(**kwargs: str) -> FakeAnthropic:
        constructor_calls.append(kwargs)
        return sdk

    monkeypatch.setattr(model_client_module, 'AsyncAnthropic', create_sdk)

    AnthropicModelClient(
        model='claude-test',
        config=ForgeConfig(
            api_key='test-api-key',
            model_id='claude-test',
            base_url='https://gateway.example.com/anthropic',
        ),
    )

    assert constructor_calls == [
        {
            'api_key': 'test-api-key',
            'base_url': 'https://gateway.example.com/anthropic',
            'timeout': 120.0,
            'max_retries': 0,
        }
    ]


def test_client_can_use_model_id_from_config() -> None:
    sdk = FakeAnthropic()

    client = AnthropicModelClient.from_config(
        config=ForgeConfig(
            api_key='test-api-key',
            model_id='configured-model',
        ),
        client=sdk,
    )

    assert client.model == 'configured-model'
    assert client.max_tokens == 8_192
    assert client.request_timeout_seconds == 120.0


def test_client_uses_configured_max_tokens() -> None:
    sdk = FakeAnthropic()
    client = AnthropicModelClient.from_config(
        config=ForgeConfig(
            api_key='test-api-key',
            model_id='configured-model',
            max_tokens=16_384,
            context_window=128_000,
        ),
        client=sdk,
    )

    collect_stream(client, messages=[{'role': 'user', 'content': 'Hello'}])

    assert sdk.messages.calls[0]['max_tokens'] == 16_384
    assert client.context_window == 128_000


def collect_stream(
    client: AnthropicModelClient,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    system: str | None = None,
) -> list[Any]:
    async def collect() -> list[Any]:
        return [
            event
            async for event in client.stream(
                messages=messages,
                tools=tools,
                system=system,
            )
        ]

    return asyncio.run(collect())


def test_stream_delegates_events_and_merges_usage() -> None:
    sdk = FakeAnthropic()
    sdk.messages.events = [
        SimpleNamespace(
            type='message_start',
            message=SimpleNamespace(
                usage=usage(input_tokens=100, output_tokens=0)
            ),
        ),
        SimpleNamespace(
            type='content_block_delta',
            index=0,
            delta=SimpleNamespace(type='text_delta', text='Hello'),
        ),
        SimpleNamespace(
            type='content_block_delta',
            index=0,
            delta=SimpleNamespace(type='text_delta', text=' world'),
        ),
        SimpleNamespace(
            type='message_delta',
            usage=usage(input_tokens=None, output_tokens=2),
        ),
    ]
    sdk.messages.final_message = SimpleNamespace(
        usage=usage(input_tokens=100, output_tokens=2)
    )
    client = AnthropicModelClient(
        model='claude-test',
        max_tokens=2048,
        client=sdk,
    )
    messages = [{'role': 'user', 'content': 'Read the repository.'}]
    tools = [
        {
            'name': 'read_file',
            'description': 'Read one repository file.',
            'input_schema': {
                'type': 'object',
                'properties': {'path': {'type': 'string'}},
                'required': ['path'],
            },
        }
    ]

    events = collect_stream(
        client,
        messages=messages,
        tools=tools,
        system='You are a coding agent.',
    )

    assert events == [
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=100, output_tokens=0)
        ),
        ModelTextDelta(text='Hello'),
        ModelTextDelta(text=' world'),
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=100, output_tokens=2)
        ),
        ModelResponseCompleted(stop_reason=None),
    ]
    assert sdk.messages.calls == [
        {
            'model': 'claude-test',
            'max_tokens': 2048,
            'messages': [
                {'role': 'user', 'content': 'Read the repository.'}
            ],
            'tools': [
                {
                    'name': 'read_file',
                    'description': 'Read one repository file.',
                    'input_schema': {
                        'type': 'object',
                        'properties': {'path': {'type': 'string'}},
                        'required': ['path'],
                    },
                }
            ],
            'system': 'You are a coding agent.',
        }
    ]
    assert isinstance(client, ModelClient)


def test_stream_recovers_text_from_final_message_when_deltas_are_missing() -> None:
    sdk = FakeAnthropic()
    sdk.messages.events = [
        SimpleNamespace(
            type='message_start',
            message=SimpleNamespace(
                usage=usage(input_tokens=12, output_tokens=0)
            ),
        )
    ]
    sdk.messages.final_message = SimpleNamespace(
        usage=usage(input_tokens=12, output_tokens=3),
        stop_reason='end_turn',
        content=[SimpleNamespace(type='text', text='Recovered text.')],
    )
    client = AnthropicModelClient(model='compatible-test', client=sdk)

    events = collect_stream(
        client,
        messages=[{'role': 'user', 'content': 'Reply'}],
    )

    assert ModelTextDelta(text='Recovered text.') in events
    assert events[-1] == ModelResponseCompleted(stop_reason='end_turn')


def test_stream_rejects_empty_provider_response_with_stop_reason() -> None:
    sdk = FakeAnthropic()
    sdk.messages.events = [
        SimpleNamespace(
            type='message_start',
            message=SimpleNamespace(
                usage=usage(input_tokens=0, output_tokens=0)
            ),
        )
    ]
    sdk.messages.final_message = SimpleNamespace(
        usage=usage(input_tokens=0, output_tokens=0),
        stop_reason='end_turn',
        content=[],
    )
    client = AnthropicModelClient(model='compatible-test', client=sdk)

    with pytest.raises(ModelProtocolError) as captured:
        collect_stream(
            client,
            messages=[{'role': 'user', 'content': 'Continue'}],
        )

    assert captured.value.reason == 'empty_model_response'
    assert 'stop_reason=end_turn' in str(captured.value)


def test_stream_rejects_empty_response_without_stop_reason() -> None:
    sdk = FakeAnthropic()
    sdk.messages.events = []
    sdk.messages.final_message = SimpleNamespace(
        usage=usage(input_tokens=0, output_tokens=0),
        stop_reason=None,
        content=[],
    )
    client = AnthropicModelClient(model='compatible-test', client=sdk)

    with pytest.raises(ModelProtocolError) as captured:
        collect_stream(
            client,
            messages=[{'role': 'user', 'content': 'Continue'}],
        )

    assert captured.value.reason == 'empty_model_response'
    assert 'stop_reason=unknown' in str(captured.value)


def test_adapter_passes_plain_message_dicts_to_sdk() -> None:
    sdk = FakeAnthropic()
    client = AnthropicModelClient(model='claude-test', client=sdk)
    messages = [
        {
            'role': 'assistant',
            'content': [
                {'type': 'text', 'text': 'I will inspect it.'},
                {
                    'type': 'tool_use',
                    'id': 'toolu_read',
                    'name': 'read_file',
                    'input': {'path': 'README.md'},
                },
            ],
        },
        {
            'role': 'user',
            'content': [
                {
                    'type': 'tool_result',
                    'tool_use_id': 'toolu_read',
                    'content': 'file contents',
                    'is_error': False,
                }
            ],
        },
    ]

    collect_stream(client, messages=messages)

    assert sdk.messages.calls[0]['messages'] == messages


def test_stream_emits_multiple_completed_tool_calls() -> None:
    sdk = FakeAnthropic()
    sdk.messages.events = [
        SimpleNamespace(
            type='message_start',
            message=SimpleNamespace(
                usage=usage(input_tokens=80, output_tokens=0)
            ),
        ),
        SimpleNamespace(
            type='content_block_start',
            index=0,
            content_block=SimpleNamespace(
                type='tool_use',
                id='toolu_read',
                name='read_file',
                input={},
            ),
        ),
        SimpleNamespace(
            type='content_block_delta',
            index=0,
            delta=SimpleNamespace(
                type='input_json_delta',
                partial_json='{"path":',
            ),
        ),
        SimpleNamespace(
            type='content_block_delta',
            index=0,
            delta=SimpleNamespace(
                type='input_json_delta',
                partial_json='"README.md"}',
            ),
        ),
        SimpleNamespace(type='content_block_stop', index=0),
        SimpleNamespace(
            type='content_block_start',
            index=1,
            content_block=SimpleNamespace(
                type='tool_use',
                id='toolu_grep',
                name='grep',
                input={},
            ),
        ),
        SimpleNamespace(
            type='content_block_delta',
            index=1,
            delta=SimpleNamespace(
                type='input_json_delta',
                partial_json='{"pattern":"TODO","path":"forge"}',
            ),
        ),
        SimpleNamespace(type='content_block_stop', index=1),
        SimpleNamespace(
            type='message_delta',
            usage=usage(input_tokens=None, output_tokens=24),
        ),
    ]
    sdk.messages.final_message = SimpleNamespace(
        usage=usage(input_tokens=80, output_tokens=24)
    )
    client = AnthropicModelClient(model='claude-test', client=sdk)

    events = collect_stream(
        client,
        messages=[
            {'role': 'user', 'content': 'Inspect the repository.'}
        ],
        tools=[
            {
                'name': 'read_file',
                'description': 'read',
                'input_schema': {'type': 'object'},
            },
            {
                'name': 'grep',
                'description': 'search',
                'input_schema': {'type': 'object'},
            },
        ],
    )

    assert events == [
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=80, output_tokens=0)
        ),
        ModelToolCallStarted(
            index=0,
            id='toolu_read',
            name='read_file',
        ),
        ModelToolCallArgumentsDelta(index=0, partial_json='{"path":'),
        ModelToolCallArgumentsDelta(
            index=0,
            partial_json='"README.md"}',
        ),
        ModelToolCallCompleted(
            tool_call=ToolCall(
                index=0,
                id='toolu_read',
                name='read_file',
                arguments={'path': 'README.md'},
            )
        ),
        ModelToolCallStarted(
            index=1,
            id='toolu_grep',
            name='grep',
        ),
        ModelToolCallArgumentsDelta(
            index=1,
            partial_json='{"pattern":"TODO","path":"forge"}',
        ),
        ModelToolCallCompleted(
            tool_call=ToolCall(
                index=1,
                id='toolu_grep',
                name='grep',
                arguments={'pattern': 'TODO', 'path': 'forge'},
            )
        ),
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=80, output_tokens=24)
        ),
        ModelResponseCompleted(stop_reason=None),
    ]


def test_stream_rejects_invalid_tool_argument_json() -> None:
    sdk = FakeAnthropic()
    sdk.messages.events = [
        SimpleNamespace(
            type='content_block_start',
            index=0,
            content_block=SimpleNamespace(
                type='tool_use',
                id='toolu_bad',
                name='read_file',
                input={},
            ),
        ),
        SimpleNamespace(
            type='content_block_delta',
            index=0,
            delta=SimpleNamespace(
                type='input_json_delta',
                partial_json='["README.md"]',
            ),
        ),
        SimpleNamespace(type='content_block_stop', index=0),
    ]
    client = AnthropicModelClient(model='claude-test', client=sdk)

    with pytest.raises(ModelProtocolError, match='must be a JSON object'):
        collect_stream(
            client,
            messages=[{'role': 'user', 'content': 'Read a file.'}],
            tools=[
                {
                    'name': 'read_file',
                    'description': 'read',
                    'input_schema': {'type': 'object'},
                }
            ],
        )


def test_stream_reports_unterminated_tool_json_with_tool_details() -> None:
    sdk = FakeAnthropic()
    sdk.messages.events = [
        SimpleNamespace(
            type='content_block_start',
            index=2,
            content_block=SimpleNamespace(
                type='tool_use',
                id='toolu_bad',
                name='write_file',
                input={},
            ),
        ),
        SimpleNamespace(
            type='content_block_delta',
            index=2,
            delta=SimpleNamespace(
                type='input_json_delta',
                partial_json='{"path":"index.html","content":"<html>',
            ),
        ),
        SimpleNamespace(type='content_block_stop', index=2),
    ]
    sdk.messages.final_message = SimpleNamespace(
        usage=usage(input_tokens=20, output_tokens=10),
        stop_reason='end_turn',
    )
    client = AnthropicModelClient(model='test', client=sdk)

    with pytest.raises(ModelProtocolError) as captured:
        collect_stream(
            client,
            messages=[{'role': 'user', 'content': 'build'}],
            tools=[
                {
                    'name': 'write_file',
                    'description': 'write',
                    'input_schema': {'type': 'object'},
                }
            ],
        )

    assert captured.value.reason == 'invalid_tool_arguments'
    assert captured.value.tool_name == 'write_file'
    assert 'Unterminated string' in str(captured.value)


def test_stream_classifies_pending_tool_call_at_max_tokens() -> None:
    sdk = FakeAnthropic()
    sdk.messages.events = [
        SimpleNamespace(
            type='content_block_start',
            index=0,
            content_block=SimpleNamespace(
                type='tool_use',
                id='toolu_patch',
                name='apply_patch',
                input={},
            ),
        ),
        SimpleNamespace(
            type='content_block_delta',
            index=0,
            delta=SimpleNamespace(
                type='input_json_delta',
                partial_json='{"patch":"*** Begin Patch',
            ),
        ),
    ]
    sdk.messages.final_message = SimpleNamespace(
        usage=usage(input_tokens=30, output_tokens=4096),
        stop_reason='max_tokens',
    )
    client = AnthropicModelClient(model='test', client=sdk)

    with pytest.raises(ModelOutputTruncatedError) as captured:
        collect_stream(client, messages=[{'role': 'user', 'content': 'build'}])

    assert captured.value.reason == 'output_truncated'
    assert captured.value.tool_name == 'apply_patch'
    assert captured.value.tool_names == ('apply_patch',)
    assert 'max_tokens' in str(captured.value)


def test_stream_rejects_unavailable_tool_before_parsing_bad_json() -> None:
    sdk = FakeAnthropic()
    sdk.messages.events = [
        SimpleNamespace(
            type='content_block_start',
            index=0,
            content_block=SimpleNamespace(
                type='tool_use',
                id='toolu_unknown',
                name='invented_writer',
                input={},
            ),
        ),
        SimpleNamespace(
            type='content_block_delta',
            index=0,
            delta=SimpleNamespace(
                type='input_json_delta',
                partial_json='not valid json',
            ),
        ),
        SimpleNamespace(type='content_block_stop', index=0),
    ]
    sdk.messages.final_message = SimpleNamespace(
        usage=usage(input_tokens=20, output_tokens=5),
        stop_reason='end_turn',
    )
    client = AnthropicModelClient(model='test', client=sdk)
    tools = [
        {
            'name': 'apply_patch',
            'description': 'patch',
            'input_schema': {'type': 'object'},
        }
    ]

    with pytest.raises(ModelProtocolError) as captured:
        collect_stream(
            client,
            messages=[{'role': 'user', 'content': 'build'}],
            tools=tools,
        )

    assert captured.value.reason == 'unavailable_tool'
    assert captured.value.tool_name == 'invented_writer'


def test_stream_omits_unused_optional_parameters() -> None:
    sdk = FakeAnthropic()
    client = AnthropicModelClient(model='claude-test', client=sdk)

    collect_stream(
        client,
        messages=[{'role': 'user', 'content': 'Hello'}],
    )

    call = sdk.messages.calls[0]
    assert 'tools' not in call
    assert 'system' not in call


def test_stream_retries_connection_error_before_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeAnthropic()
    sdk.messages.errors.append(
        model_client_module.APIConnectionError(request=SimpleNamespace())
    )
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(model_client_module.asyncio, 'sleep', record_sleep)
    monkeypatch.setattr(model_client_module.random, 'uniform', lambda *_: 0)
    client = AnthropicModelClient(
        model='claude-test',
        max_retries=1,
        client=sdk,
    )

    events = collect_stream(
        client,
        messages=[{'role': 'user', 'content': 'Hello'}],
    )

    assert events[0] == ModelRetryScheduled(
        attempt=2,
        reason='connection_error',
        delay_seconds=0.5,
    )
    assert delays == [0.5]
    assert len(sdk.messages.calls) == 2


def test_stream_does_not_retry_after_text_started() -> None:
    sdk = FakeAnthropic()
    sdk.messages.events = [
        SimpleNamespace(
            type='content_block_delta',
            index=0,
            delta=SimpleNamespace(type='text_delta', text='Partial'),
        ),
        model_client_module.APIConnectionError(request=SimpleNamespace()),
    ]
    client = AnthropicModelClient(model='claude-test', client=sdk)
    collected: list[Any] = []

    async def collect() -> model_client_module.ModelCallError:
        with pytest.raises(
            model_client_module.ModelCallError,
            match='avoid duplicate output',
        ) as captured:
            async for event in client.stream(
                messages=[{'role': 'user', 'content': 'Hello'}]
            ):
                collected.append(event)
        return captured.value

    error = asyncio.run(collect())

    assert collected == [ModelTextDelta(text='Partial')]
    assert error.reason == 'stream_interrupted'
    assert error.retryable is False
    assert len(sdk.messages.calls) == 1


@pytest.mark.parametrize(
    ('model', 'max_tokens', 'max_retries'),
    [('', 1, 3), ('claude-test', 0, 3), ('claude-test', 1, -1)],
)
def test_invalid_configuration_is_rejected(
    model: str,
    max_tokens: int,
    max_retries: int,
) -> None:
    with pytest.raises(ValueError):
        AnthropicModelClient(
            model=model,
            max_tokens=max_tokens,
            max_retries=max_retries,
            client=FakeAnthropic(),
        )
