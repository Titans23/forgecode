'''Native Responses and DeepSeek adapters behind the common event boundary.

The runtime owns dispatch. Even fully parsed calls are emitted only after the
provider has supplied an explicit successful terminal event.
'''

from collections.abc import AsyncIterator
import asyncio
import json
import random
import httpx

from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError

from forge.config import ForgeConfig
from forge.runtime.model_client import (
    AnthropicModelClient, ModelCallError, ModelProtocolError, ModelOutputTruncatedError,
)
from forge.runtime.state import (
    ModelTextDelta, ModelToolCallStarted, ModelToolCallArgumentsDelta,
    ModelToolCallCompleted, ModelUsageUpdate, ModelResponseCompleted,
    ModelRetryScheduled, ModelProviderState, TokenUsage, ToolCall,
)


def create_model_client(config=None, *, max_tokens=None):
    config = config or ForgeConfig.from_env()
    if config.provider == 'anthropic':
        return AnthropicModelClient.from_config(config, max_tokens=max_tokens)
    return NativeModelClient(config, max_tokens=max_tokens)


def _parts(message):
    value = message.get('content', '')
    return [{'type': 'text', 'text': value}] if isinstance(value, str) else value


def _text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def chat_messages(messages, system=None):
    output = [{'role': 'system', 'content': system}] if system else []
    for message in messages:
        content, calls, results = [], [], []
        for part in _parts(message):
            kind = part.get('type')
            if kind == 'text':
                content.append(part.get('text', ''))
            elif kind == 'tool_use':
                calls.append({'id': part['id'], 'type': 'function', 'function': {
                    'name': part['name'], 'arguments': json.dumps(part['input'], ensure_ascii=False)}})
            elif kind == 'tool_result':
                results.append({'role': 'tool', 'tool_call_id': part['tool_use_id'],
                                'content': _text(part.get('content', ''))})
            else:
                raise ModelProtocolError(f'Unsupported message part for DeepSeek: {kind}')
        if content or calls:
            item = {'role': message['role'], 'content': '\n'.join(content)}
            if calls:
                item['tool_calls'] = calls
            if message['role'] == 'assistant':
                state = message.get('provider_state', {})
                item['reasoning_content'] = (state.get('data', {}).get('reasoning_content', '')
                                             if state.get('provider') == 'deepseek' else '')
            output.append(item)
        output.extend(results)
    return output


def responses_input(messages):
    output = []
    for message in messages:
        state = message.get('provider_state', {})
        if state.get('provider') == 'openai_responses':
            output.extend(state.get('data', {}).get('reasoning', []))
        for part in _parts(message):
            kind = part.get('type')
            if kind == 'text':
                output.append({'role': message['role'], 'content': part.get('text', '')})
            elif kind == 'tool_use':
                output.append({'type': 'function_call', 'call_id': part['id'],
                               'name': part['name'], 'arguments': json.dumps(part['input'], ensure_ascii=False)})
            elif kind == 'tool_result':
                output.append({'type': 'function_call_output', 'call_id': part['tool_use_id'],
                               'output': _text(part.get('content', ''))})
            else:
                raise ModelProtocolError(f'Unsupported message part for Responses: {kind}')
    return output


def _call(index, identity, name, arguments):
    try:
        parsed = json.loads(arguments, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if not isinstance(parsed, dict):
            raise ValueError('Tool arguments must be an object')
        return ToolCall(index, identity, name, parsed)
    except (TypeError, ValueError) as error:
        raise ModelProtocolError('Incomplete or invalid tool arguments', reason='incomplete_tool_call',
                                 tool_name=name) from error


class NativeModelClient:
    observes_request_budget = True

    def __init__(self, config: ForgeConfig, *, max_tokens=None, client=None, max_attempts=3):
        if config.provider not in {'deepseek', 'openai_responses'}:
            raise ValueError('NativeModelClient requires a native OpenAI/DeepSeek profile')
        if max_attempts < 1:
            raise ValueError('max_attempts must be positive')
        self.provider, self.model = config.provider, config.model_id
        self.context_window = config.context_window
        self.max_tokens = max_tokens or config.max_tokens
        self.reasoning_effort = config.reasoning_effort
        self.max_attempts = max_attempts
        from forge.runtime.service_health import profile_health
        self.health = profile_health(config)
        from forge.runtime.model_budget import observe_wire_request
        if client is not None and hasattr(client, 'with_options'):
            client = client.with_options(max_retries=0)
        self._client = client or AsyncOpenAI(api_key=config.api_key, base_url=config.base_url,
            timeout=config.request_timeout_seconds, max_retries=0,
            http_client=httpx.AsyncClient(follow_redirects=False, event_hooks={'request': [observe_wire_request]}))

    def request_arguments(self, messages, tools, system):
        definitions = [{'name': item['name'], 'description': item.get('description', ''),
                        'parameters': item['input_schema']} for item in tools or ()]
        if self.provider == 'deepseek':
            args = dict(model=self.model, messages=chat_messages(messages, system), stream=True,
                        max_tokens=self.max_tokens, stream_options={'include_usage': True})
            if definitions:
                args['tools'] = [{'type': 'function', 'function': item} for item in definitions]
            if self.reasoning_effort:
                args['extra_body'] = {'reasoning_effort': self.reasoning_effort}
        else:
            args = dict(model=self.model, input=responses_input(messages), stream=True, store=False,
                        max_output_tokens=self.max_tokens, include=['reasoning.encrypted_content'])
            if system:
                args['instructions'] = system
            if definitions:
                # Local validation remains authoritative. Strict schema restrictions
                # must not silently change optional tool parameters into required ones.
                args['tools'] = [{'type': 'function', 'strict': False, **item} for item in definitions]
            if self.reasoning_effort:
                args['reasoning'] = {'effort': self.reasoning_effort}
        return args

    async def aclose(self):
        await self._client.close()

    async def stream(self, messages, tools=None, system=None) -> AsyncIterator:
        from forge.runtime.model_budget import request_observer
        arguments = self.request_arguments(messages, tools, system)
        for attempt in range(1, self.max_attempts + 1):
            self.health.before_request()
            observer = request_observer.get()
            semantic_output = False
            try:
                if observer:
                    observer()
                generator = self._chat(arguments) if self.provider == 'deepseek' else self._responses(arguments)
                try:
                    async for event in generator:
                        semantic_output = semantic_output or isinstance(event, (ModelTextDelta, ModelToolCallStarted))
                        yield event
                finally:
                    await generator.aclose()
                self.health.succeeded()
                return
            except (APIConnectionError, APIStatusError) as error:
                status = getattr(error, 'status_code', None)
                retryable = isinstance(error, APIConnectionError) or status in {408, 409, 429} or (status and status >= 500)
                if retryable:
                    self.health.failed()
                reason = ('timeout' if isinstance(error, APITimeoutError) else 'connection_error'
                          if status is None else 'rate_limit' if status == 429 else 'server_error'
                          if status >= 500 else 'request_rejected')
                if retryable and not semantic_output and attempt < self.max_attempts:
                    delay = min(8.0, 2 ** (attempt - 1)) + random.random() * .25
                    yield ModelRetryScheduled(attempt, reason, delay)
                    await asyncio.sleep(delay)
                    continue
                raise ModelCallError(reason, f'{self.provider} request failed ({status or reason})',
                                     retryable=bool(retryable)) from error
            finally:
                self.health.probe_inflight = False

    async def _chat(self, arguments):
        pending, reasoning, finish, usage = {}, [], None, None
        stream = await self._client.chat.completions.create(**arguments)
        try:
            async for chunk in stream:
                if chunk.usage is not None:
                    usage = chunk.usage
                    cached = getattr(usage, 'prompt_cache_hit_tokens', 0) or 0
                    yield ModelUsageUpdate(TokenUsage(max(0, usage.prompt_tokens - cached), usage.completion_tokens,
                                                          cache_read_input_tokens=cached))
                for choice in chunk.choices:
                    if choice.index != 0:
                        raise ModelProtocolError('Only one completion choice is supported')
                    delta = choice.delta
                    if delta.content:
                        yield ModelTextDelta(delta.content)
                    if getattr(delta, 'reasoning_content', None):
                        reasoning.append(delta.reasoning_content)
                    for part in delta.tool_calls or ():
                        item = pending.setdefault(part.index, {'id': '', 'name': '', 'arguments': ''})
                        if part.id:
                            item['id'] = part.id
                        if part.function:
                            if part.function.name:
                                item['name'] = part.function.name
                                yield ModelToolCallStarted(part.index, item['id'], item['name'])
                            if part.function.arguments:
                                item['arguments'] += part.function.arguments
                                yield ModelToolCallArgumentsDelta(part.index, part.function.arguments)
                    if choice.finish_reason:
                        finish = choice.finish_reason
        finally:
            await stream.close()
        if finish == 'length':
            raise ModelOutputTruncatedError(tuple(item['name'] for item in pending.values()))
        if finish not in {'stop', 'tool_calls'} or (pending and finish != 'tool_calls'):
            raise ModelProtocolError('Missing successful stream terminal', reason='stream_termination_missing')
        calls = [_call(index, item['id'], item['name'], item['arguments']) for index, item in sorted(pending.items())]
        if len({call.id for call in calls}) != len(calls):
            raise ModelProtocolError('Duplicate tool call identity')
        yield ModelProviderState(self.provider, {'reasoning_content': ''.join(reasoning)})
        for call in calls:
            yield ModelToolCallCompleted(call)
        yield ModelResponseCompleted('tool_use' if calls else 'end_turn')

    async def _responses(self, arguments):
        final = None
        stream = await self._client.responses.create(**arguments)
        try:
            async for event in stream:
                response = getattr(event, 'response', None)
                usage = getattr(response, 'usage', None)
                if usage is not None:
                    cached = getattr(getattr(usage, 'input_tokens_details', None), 'cached_tokens', 0) or 0
                    yield ModelUsageUpdate(TokenUsage(max(0, usage.input_tokens - cached), usage.output_tokens,
                                                      cache_read_input_tokens=cached))
                if event.type == 'response.output_text.delta':
                    yield ModelTextDelta(event.delta, getattr(event, 'output_index', 0))
                elif event.type == 'response.output_item.added' and event.item.type == 'function_call':
                    yield ModelToolCallStarted(event.output_index, event.item.call_id, event.item.name)
                elif event.type == 'response.function_call_arguments.delta':
                    yield ModelToolCallArgumentsDelta(event.output_index, event.delta)
                elif event.type == 'response.completed':
                    final = event.response
                elif event.type == 'response.incomplete':
                    raise ModelOutputTruncatedError()
                elif event.type in {'response.failed', 'error'}:
                    raise ModelCallError('response_failed', 'Responses request failed', retryable=False)
        finally:
            await stream.close()
        if final is None or final.status != 'completed':
            raise ModelProtocolError('Missing completed response', reason='stream_termination_missing')
        calls, reasoning = [], []
        for index, item in enumerate(final.output):
            if item.type == 'function_call':
                calls.append(_call(index, item.call_id, item.name, item.arguments))
            elif item.type == 'reasoning':
                reasoning.append(item.model_dump(exclude_none=True))
        if len({call.id for call in calls}) != len(calls):
            raise ModelProtocolError('Duplicate tool call identity')
        yield ModelProviderState(self.provider, {'reasoning': reasoning})
        for call in calls:
            yield ModelToolCallCompleted(call)
        yield ModelResponseCompleted('tool_use' if calls else 'end_turn')
