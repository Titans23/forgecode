'''Model client boundary backed by the official Anthropic SDK.'''

from __future__ import annotations

from typing import Protocol, runtime_checkable

from anthropic import AsyncAnthropic, omit
from anthropic.types import Message, MessageParam, ToolParam

from forge.config import ForgeConfig


DEFAULT_MODEL_PROVIDER = 'anthropic'


@runtime_checkable
class ModelClient(Protocol):
    '''Minimal async interface used by the ForgeCode runtime.'''

    provider: str

    async def generate(
        self,
        messages: list[MessageParam],
        tools: list[ToolParam] | None = None,
        system: str | None = None,
    ) -> Message:
        '''Generate one complete assistant message.'''
        ...


class AnthropicModelClient:
    '''Thin adapter around Anthropic AsyncAnthropic.messages.create.'''

    provider = DEFAULT_MODEL_PROVIDER

    @classmethod
    def from_config(
        cls,
        config: ForgeConfig | None = None,
        max_tokens: int = 4096,
        client: AsyncAnthropic | None = None,
    ) -> AnthropicModelClient:
        '''Create a model client from .env or an explicit ForgeConfig.'''
        resolved_config = config if config is not None else ForgeConfig.from_env()
        return cls(
            model=resolved_config.model_id,
            max_tokens=max_tokens,
            config=resolved_config,
            client=client,
        )

    def __init__(
        self,
        model: str,
        max_tokens: int = 4096,
        config: ForgeConfig | None = None,
        client: AsyncAnthropic | None = None,
    ) -> None:
        if not model:
            raise ValueError('model must not be empty')
        if max_tokens < 1:
            raise ValueError('max_tokens must be positive')

        self.model = model
        self.max_tokens = max_tokens
        if client is not None:
            self._client = client
        else:
            resolved_config = (
                config if config is not None else ForgeConfig.from_env()
            )
            self._client = AsyncAnthropic(
                api_key=resolved_config.api_key,
                base_url=resolved_config.base_url,
            )

    async def generate(
        self,
        messages: list[MessageParam],
        tools: list[ToolParam] | None = None,
        system: str | None = None,
    ) -> Message:
        return await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
            tools=tools if tools else omit,
            system=system if system is not None else omit,
            stream=False,
        )
