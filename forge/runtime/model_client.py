'''Model client boundary backed by the official Anthropic SDK.'''

from __future__ import annotations

from typing import Protocol, runtime_checkable

from anthropic import AsyncAnthropic, omit
from anthropic.types import Message, MessageParam, ToolParam


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

    def __init__(
        self,
        model: str,
        max_tokens: int = 4096,
        client: AsyncAnthropic | None = None,
    ) -> None:
        if not model:
            raise ValueError('model must not be empty')
        if max_tokens < 1:
            raise ValueError('max_tokens must be positive')

        self.model = model
        self.max_tokens = max_tokens
        self._client = client if client is not None else AsyncAnthropic()

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
