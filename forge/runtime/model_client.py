'''Model client boundary backed by the official Anthropic SDK.'''

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import json
import random
from typing import Any, Protocol, runtime_checkable

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    InternalServerError,
    RateLimitError,
)

from forge.config import DEFAULT_MODEL_MAX_TOKENS, ForgeConfig
from forge.runtime.state import (
    ModelStreamEvent,
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


DEFAULT_MODEL_PROVIDER = 'anthropic'


class ModelProtocolError(RuntimeError):
    '''Raised when a provider emits an invalid model stream.'''

    def __init__(
        self,
        message: str,
        *,
        reason: str = 'invalid_model_protocol',
        tool_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.tool_name = tool_name


class ModelOutputTruncatedError(ModelProtocolError):
    '''Raised when the provider stops because max_tokens was reached.'''

    def __init__(self, tool_names: tuple[str, ...] = ()) -> None:
        self.tool_names = tool_names
        detail = (
            f' while generating tool arguments for {", ".join(tool_names)}'
            if tool_names
            else ''
        )
        super().__init__(
            'Model output was truncated at the max_tokens limit'
            f'{detail}.',
            reason='output_truncated',
            tool_name=tool_names[0] if len(tool_names) == 1 else None,
        )


class ModelCallError(RuntimeError):
    '''Provider-neutral model request failure exposed to the runtime.'''

    def __init__(self, reason: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable


@dataclass(slots=True)
class _PendingToolCall:
    id: str
    name: str
    initial_input: dict[str, Any]
    json_parts: list[str] = field(default_factory=list)
    available: bool = True


@runtime_checkable
class ModelClient(Protocol):
    '''Minimal async interface used by the ForgeCode runtime.'''

    provider: str

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        '''Stream text and exact provider usage updates.'''
        ...


class AnthropicModelClient:
    '''Thin adapter around Anthropic AsyncAnthropic.messages.stream.'''

    provider = DEFAULT_MODEL_PROVIDER

    @classmethod
    def from_config(
        cls,
        config: ForgeConfig | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
        client: AsyncAnthropic | None = None,
    ) -> AnthropicModelClient:
        '''Create a model client from .env or an explicit ForgeConfig.'''
        resolved_config = config if config is not None else ForgeConfig.from_env()
        return cls(
            model=resolved_config.model_id,
            max_tokens=(
                resolved_config.max_tokens
                if max_tokens is None
                else max_tokens
            ),
            context_window=resolved_config.context_window,
            request_timeout_seconds=(
                resolved_config.request_timeout_seconds
            ),
            max_retries=max_retries,
            config=resolved_config,
            client=client,
        )

    def __init__(
        self,
        model: str,
        max_tokens: int = DEFAULT_MODEL_MAX_TOKENS,
        max_retries: int = 3,
        context_window: int | None = None,
        request_timeout_seconds: float = 120.0,
        config: ForgeConfig | None = None,
        client: AsyncAnthropic | None = None,
    ) -> None:
        if not model:
            raise ValueError('model must not be empty')
        if max_tokens < 1:
            raise ValueError('max_tokens must be positive')
        if max_retries < 0:
            raise ValueError('max_retries must not be negative')
        if request_timeout_seconds <= 0:
            raise ValueError('request_timeout_seconds must be positive')

        self.model = model
        self.max_tokens = max_tokens
        self.context_window = context_window
        self.max_retries = max_retries
        self.request_timeout_seconds = request_timeout_seconds
        if client is not None:
            self._client = client
        else:
            resolved_config = (
                config if config is not None else ForgeConfig.from_env()
            )
            self._client = AsyncAnthropic(
                api_key=resolved_config.api_key,
                base_url=resolved_config.base_url,
                timeout=resolved_config.request_timeout_seconds,
                max_retries=0,
            )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        sdk_arguments: dict[str, Any] = {
            'model': self.model,
            'max_tokens': self.max_tokens,
            'messages': messages,
        }
        if tools:
            sdk_arguments['tools'] = tools
        if system is not None:
            sdk_arguments['system'] = system

        for attempt in range(1, self.max_retries + 2):
            response_started = False
            try:
                async for event in self._stream_once(sdk_arguments):
                    if isinstance(
                        event,
                        (
                            ModelTextDelta,
                            ModelToolCallStarted,
                            ModelToolCallArgumentsDelta,
                            ModelToolCallCompleted,
                        ),
                    ):
                        response_started = True
                    yield event
                return
            except (
                APIConnectionError,
                APIStatusError,
            ) as error:
                reason, retryable = classify_provider_error(error)
                can_retry = (
                    retryable
                    and not response_started
                    and attempt <= self.max_retries
                )
                if not can_retry:
                    if response_started and retryable:
                        reason = 'stream_interrupted'
                    raise ModelCallError(
                        reason,
                        model_error_message(reason, response_started),
                        retryable=retryable and not response_started,
                    ) from error

                delay = retry_delay(attempt)
                yield ModelRetryScheduled(
                    attempt=attempt + 1,
                    reason=reason,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)

        raise AssertionError('model retry loop ended unexpectedly')

    async def _stream_once(
        self,
        sdk_arguments: dict[str, Any],
    ) -> AsyncIterator[ModelStreamEvent]:
        '''Perform one provider request without retry policy.'''
        current_usage: TokenUsage | None = None
        semantic_output = False
        pending_tool_calls: dict[int, _PendingToolCall] = {}
        protocol_error: ModelProtocolError | None = None
        allowed_tool_names = {
            str(tool.get('name', ''))
            for tool in sdk_arguments.get('tools', [])
        }
        async with self._client.messages.stream(**sdk_arguments) as stream:
            async for event in stream:
                if (
                    event.type == 'content_block_delta'
                    and event.delta.type == 'text_delta'
                ):
                    if event.delta.text:
                        semantic_output = True
                    yield ModelTextDelta(
                        text=event.delta.text,
                        index=event.index,
                    )
                elif (
                    event.type == 'content_block_start'
                    and event.content_block.type == 'tool_use'
                ):
                    block = event.content_block
                    available = block.name in allowed_tool_names
                    pending_tool_calls[event.index] = _PendingToolCall(
                        id=block.id,
                        name=block.name,
                        initial_input=dict(block.input),
                        available=available,
                    )
                    if not available:
                        protocol_error = protocol_error or ModelProtocolError(
                            f'Model requested unavailable tool: {block.name}.',
                            reason='unavailable_tool',
                            tool_name=block.name,
                        )
                    else:
                        yield ModelToolCallStarted(
                            index=event.index,
                            id=block.id,
                            name=block.name,
                        )
                elif (
                    event.type == 'content_block_delta'
                    and event.delta.type == 'input_json_delta'
                ):
                    pending = pending_tool_calls.get(event.index)
                    if pending is None:
                        raise ModelProtocolError(
                            'Received tool arguments before tool_use started '
                            f'at content block {event.index}.'
                        )
                    pending.json_parts.append(event.delta.partial_json)
                    if pending.available:
                        yield ModelToolCallArgumentsDelta(
                            index=event.index,
                            partial_json=event.delta.partial_json,
                        )
                elif (
                    event.type == 'content_block_stop'
                    and event.index in pending_tool_calls
                ):
                    pending = pending_tool_calls.pop(event.index)
                    if not pending.available:
                        continue
                    try:
                        arguments = parse_tool_arguments(
                            pending,
                            index=event.index,
                        )
                    except ModelProtocolError as error:
                        protocol_error = protocol_error or error
                    else:
                        semantic_output = True
                        yield ModelToolCallCompleted(
                            tool_call=ToolCall(
                                index=event.index,
                                id=pending.id,
                                name=pending.name,
                                arguments=arguments,
                            )
                        )
                elif event.type == 'message_start':
                    current_usage = merge_usage(
                        event.message.usage,
                        current_usage,
                    )
                    yield ModelUsageUpdate(usage=current_usage)
                elif event.type == 'message_delta':
                    current_usage = merge_usage(
                        event.usage,
                        current_usage,
                    )
                    yield ModelUsageUpdate(usage=current_usage)

            final_message = await stream.get_final_message()

        stop_reason = getattr(final_message, 'stop_reason', None)
        if stop_reason == 'max_tokens':
            names = tuple(
                pending.name for pending in pending_tool_calls.values()
            )
            if not names and protocol_error is not None:
                names = tuple(
                    name
                    for name in (protocol_error.tool_name,)
                    if name is not None
                )
            raise ModelOutputTruncatedError(names)
        if protocol_error is not None:
            raise protocol_error
        if pending_tool_calls:
            indexes = ', '.join(str(index) for index in pending_tool_calls)
            raise ModelProtocolError(
                f'Tool calls did not finish at content blocks: {indexes}.',
                reason='incomplete_tool_call',
            )

        if not semantic_output:
            async for fallback_event in final_content_events(
                final_message,
                allowed_tool_names,
            ):
                semantic_output = True
                yield fallback_event

        final_usage = merge_usage(final_message.usage, current_usage)
        if final_usage != current_usage:
            yield ModelUsageUpdate(usage=final_usage)

        if not semantic_output:
            raise ModelProtocolError(
                'Provider returned no text or tool calls '
                '(stop_reason=%s).' % (stop_reason or 'unknown'),
                reason='empty_model_response',
            )
        yield ModelResponseCompleted(stop_reason=stop_reason)


async def final_content_events(
    final_message: Any,
    allowed_tool_names: set[str],
) -> AsyncIterator[ModelStreamEvent]:
    '''Recover semantic blocks when a compatible provider omits deltas.'''
    content = getattr(final_message, 'content', None)
    if not isinstance(content, list):
        return
    for index, block in enumerate(content):
        block_type = getattr(block, 'type', None)
        if block_type == 'text':
            text = str(getattr(block, 'text', ''))
            if text:
                yield ModelTextDelta(text=text, index=index)
            continue
        if block_type != 'tool_use':
            continue
        name = str(getattr(block, 'name', ''))
        tool_id = str(getattr(block, 'id', ''))
        arguments = getattr(block, 'input', None)
        if name not in allowed_tool_names:
            raise ModelProtocolError(
                f'Model requested unavailable tool: {name}.',
                reason='unavailable_tool',
                tool_name=name,
            )
        if not tool_id or not isinstance(arguments, dict):
            raise ModelProtocolError(
                'Invalid final tool block for %s.' % (name or 'unknown'),
                reason='invalid_tool_arguments',
                tool_name=name or None,
            )
        yield ModelToolCallStarted(index=index, id=tool_id, name=name)
        yield ModelToolCallCompleted(
            tool_call=ToolCall(
                index=index,
                id=tool_id,
                name=name,
                arguments=dict(arguments),
            )
        )


def classify_provider_error(error: Exception) -> tuple[str, bool]:
    '''Map Anthropic transport failures to stable ForgeCode reasons.'''
    if isinstance(error, RateLimitError):
        return 'rate_limit', True
    if isinstance(error, APITimeoutError):
        return 'timeout', True
    if isinstance(error, APIConnectionError):
        return 'connection_error', True
    if isinstance(error, InternalServerError):
        if getattr(error, 'status_code', None) == 529:
            return 'overloaded', True
        return 'server_error', True
    if isinstance(error, APIStatusError):
        details = str(error).lower()
        if error.status_code == 400 and any(
            marker in details
            for marker in (
                'context window',
                'prompt is too long',
                'too many tokens',
                'input length',
            )
        ):
            return 'context_overflow', False
        return f'http_{error.status_code}', False
    return 'provider_error', False


def retry_delay(retry_number: int) -> float:
    '''Return bounded exponential backoff with up to 25% jitter.'''
    base = min(0.5 * (2 ** (retry_number - 1)), 8.0)
    return base + random.uniform(0, base * 0.25)


def model_error_message(reason: str, response_started: bool) -> str:
    '''Build a concise error safe to show in the terminal.'''
    if response_started:
        return (
            'The model stream was interrupted after output started; '
            'ForgeCode did not retry to avoid duplicate output.'
        )
    return f'Model request failed: {reason}.'


def parse_tool_arguments(
    pending: _PendingToolCall,
    *,
    index: int,
) -> dict[str, Any]:
    '''Parse and validate one completed tool call JSON object.'''
    raw_json = ''.join(pending.json_parts)
    if not raw_json:
        return pending.initial_input

    try:
        arguments = json.loads(raw_json)
    except json.JSONDecodeError as error:
        raise ModelProtocolError(
            f'Invalid JSON arguments for tool {pending.name!r} '
            f'at content block {index}: {error.msg}.',
            reason='invalid_tool_arguments',
            tool_name=pending.name,
        ) from error
    if not isinstance(arguments, dict):
        raise ModelProtocolError(
            f'Arguments for tool {pending.name!r} at content block '
            f'{index} must be a JSON object.',
            reason='invalid_tool_arguments',
            tool_name=pending.name,
        )
    return arguments


def merge_usage(
    usage: Any,
    previous: TokenUsage | None = None,
) -> TokenUsage:
    '''Merge partial streaming usage fields into one exact snapshot.'''
    fallback = previous or TokenUsage(input_tokens=0, output_tokens=0)

    def value(name: str, default: int) -> int:
        reported = getattr(usage, name, None)
        return default if reported is None else reported

    return TokenUsage(
        input_tokens=value('input_tokens', fallback.input_tokens),
        output_tokens=value('output_tokens', fallback.output_tokens),
        cache_creation_input_tokens=value(
            'cache_creation_input_tokens',
            fallback.cache_creation_input_tokens,
        ),
        cache_read_input_tokens=value(
            'cache_read_input_tokens',
            fallback.cache_read_input_tokens,
        ),
    )
