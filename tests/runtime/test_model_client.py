'''Tests for the thin Anthropic SDK adapter.'''

import asyncio
from typing import Any

import pytest
from anthropic import omit
from anthropic.types import MessageParam, ToolParam

from forge.config import ForgeConfig
import forge.runtime.model_client as model_client_module
from forge.runtime.model_client import (
    AnthropicModelClient,
    ModelClient,
)


class FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = object()

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


class FakeAnthropic:
    def __init__(self) -> None:
        self.messages = FakeMessages()


def test_client_passes_explicit_config_to_anthropic_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeAnthropic()
    constructor_calls: list[dict[str, str]] = []

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


def test_generate_delegates_to_anthropic_sdk() -> None:
    sdk = FakeAnthropic()
    client = AnthropicModelClient(
        model='claude-test',
        max_tokens=2048,
        client=sdk,
    )
    messages: list[MessageParam] = [
        {'role': 'user', 'content': 'Read the repository.'},
    ]
    tools: list[ToolParam] = [
        {
            'name': 'read_file',
            'description': 'Read one repository file.',
            'input_schema': {
                'type': 'object',
                'properties': {'path': {'type': 'string'}},
                'required': ['path'],
            },
        },
    ]

    response = asyncio.run(
        client.generate(
            messages=messages,
            tools=tools,
            system='You are a coding agent.',
        )
    )

    assert response is sdk.messages.response
    assert sdk.messages.calls == [
        {
            'model': 'claude-test',
            'max_tokens': 2048,
            'messages': messages,
            'tools': tools,
            'system': 'You are a coding agent.',
            'stream': False,
        }
    ]
    assert isinstance(client, ModelClient)


def test_generate_omits_unused_optional_parameters() -> None:
    sdk = FakeAnthropic()
    client = AnthropicModelClient(model='claude-test', client=sdk)

    asyncio.run(
        client.generate(messages=[{'role': 'user', 'content': 'Hello'}])
    )

    call = sdk.messages.calls[0]
    assert call['tools'] is omit
    assert call['system'] is omit


@pytest.mark.parametrize(
    ('model', 'max_tokens'),
    [('', 1), ('claude-test', 0)],
)
def test_invalid_configuration_is_rejected(
    model: str,
    max_tokens: int,
) -> None:
    with pytest.raises(ValueError):
        AnthropicModelClient(
            model=model,
            max_tokens=max_tokens,
            client=FakeAnthropic(),
        )
