"""Gemini generateContent REST streaming adapter (no automatic tool execution).

Protocol: https://ai.google.dev/api/generate-content
"""
from copy import deepcopy
import asyncio
import json
from urllib.parse import quote
from uuid import uuid4

import httpx

from forge.runtime.capabilities import ModelCapabilities
from forge.runtime.state import (ModelCallError, ModelProtocolError, ModelOutputTruncatedError,
    ModelTextDelta, ModelReasoningDelta, ModelToolCallStarted, ModelToolCallCompleted, ModelUsageUpdate,
    ModelProviderState, ModelResponseCompleted, ModelRetryScheduled, ToolCall)


class GeminiModelClient:
    provider = 'gemini'
    observes_request_budget = True

    def __init__(self, config, *, max_tokens=None, max_attempts=3, client=None):
        if max_attempts < 1:
            raise ValueError('max_attempts must be positive')
        self.model = config.model_id
        self.context_window = config.context_window
        self.input_modalities = config.input_modalities
        self.max_tokens = max_tokens or config.max_tokens
        self.base_url = config.base_url.rstrip('/')
        self._key = config.api_key
        self.max_attempts = max_attempts
        from forge.runtime.model_budget import observe_wire_request
        self._client = client or httpx.AsyncClient(timeout=config.request_timeout_seconds,
            follow_redirects=False, event_hooks={'request': [observe_wire_request]})

    @property
    def capabilities(self):
        return ModelCapabilities(image_transport=True, model_input_modalities=self.input_modalities, context_window=self.context_window,
                                 max_output_tokens=self.max_tokens)

    def request_arguments(self, messages, tools=None, system=None):
        from forge.context.manager import context_stats, request_output_tokens
        maximum = request_output_tokens(context_stats(messages, system_prompt=system or '', tools=tools,
            context_window_tokens=self.context_window, reserved_output_tokens=self.max_tokens))
        if maximum <= 0:
            raise ModelCallError('context_overflow', 'No room for model output', retryable=False)
        contents, names = [], {}
        for message in messages:
            content = message.get('content', '')
            blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else content
            parts = []
            for block in blocks:
                kind = block['type']
                if kind == 'text':
                    if block['text']:
                        parts.append({'text': block['text']})
                elif kind == 'image':
                    source = block['source']
                    parts.append({'inlineData': {'mimeType': source['media_type'], 'data': source['data']}})
                elif kind == 'tool_use':
                    names[block['id']] = block['name']
                    parts.append({'functionCall': {'name': block['name'], 'args': block['input']}})
                elif kind == 'tool_result':
                    if block['tool_use_id'] not in names:
                        raise ModelProtocolError('Tool response has no matching function name')
                    value = block.get('content', '')
                    response = {'result': value} if not isinstance(value, list) else {
                        'result': [part for part in value if part['type'] != 'image']}
                    parts.append({'functionResponse': {'name': names[block['tool_use_id']], 'response': response}})
                    if isinstance(value, list):
                        for part in value:
                            if part['type'] == 'image':
                                source = part['source']
                                parts.append({'inlineData': {'mimeType': source['media_type'], 'data': source['data']}})
                else:
                    raise ModelProtocolError(f'Unsupported Gemini input: {kind}')
            state = message.get('provider_state', {})
            if message['role'] == 'assistant' and state.get('provider') == 'gemini':
                parts = deepcopy(state['data']['parts'])
            if parts:
                contents.append({'role': 'model' if message['role'] == 'assistant' else 'user', 'parts': parts})
        value = {'contents': contents, 'generationConfig': {'maxOutputTokens': maximum, 'candidateCount': 1}}
        if system:
            value['systemInstruction'] = {'parts': [{'text': system}]}
        if tools:
            value['tools'] = [{'functionDeclarations': [{'name': t['name'], 'description': t.get('description', ''),
                'parametersJsonSchema': t['input_schema']} for t in tools]}]
        return value

    async def stream(self, messages, tools=None, system=None):
        from forge.runtime.model_budget import request_observer
        arguments = self.request_arguments(messages, tools, system)
        for attempt in range(1, self.max_attempts + 1):
            observer = request_observer.get()
            if observer:
                observer()
            emitted = False
            try:
                async for event in self._stream(arguments):
                    emitted = emitted or isinstance(event, (ModelTextDelta, ModelReasoningDelta, ModelToolCallStarted))
                    yield event
                return
            except (httpx.HTTPError, httpx.StreamError) as error:
                status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
                retryable = status is None or status in {408, 429} or status >= 500
                reason = 'rate_limit' if status == 429 else 'request_rejected' if status else 'connection_error'
                if retryable and not emitted and attempt < self.max_attempts:
                    delay = min(8, 2 ** (attempt - 1))
                    yield ModelRetryScheduled(attempt, reason, delay)
                    await asyncio.sleep(delay)
                    continue
                raise ModelCallError(reason, f'Gemini transport failed ({status or reason})', retryable=retryable) from error

    async def _stream(self, arguments):
        from forge.observability.usage_ledger import token_usage
        parts, calls, final_usage = [], [], None
        finish = None
        url = self.base_url + '/models/' + quote(self.model.removeprefix('models/'), safe='-._') + ':streamGenerateContent'
        async with self._client.stream('POST', url, params={'alt': 'sse'},
                headers={'x-goog-api-key': self._key}, json=arguments) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith('data:'):
                    continue
                if line[5:].strip() == '[DONE]':
                    break
                try:
                    chunk = json.loads(line[5:].strip(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                except ValueError as error:
                    raise ModelProtocolError('Invalid Gemini stream JSON') from error
                if 'error' in chunk:
                    raise ModelCallError('response_failed', 'Gemini returned an error event', retryable=False)
                raw = chunk.get('usageMetadata')
                if raw and 'promptTokenCount' in raw and 'candidatesTokenCount' in raw:
                    final_usage = raw
                    yield ModelUsageUpdate(token_usage('gemini', raw), raw_usage=raw, usage_is_final=False)
                for candidate in chunk.get('candidates', ()):
                    if candidate.get('index', 0) != 0:
                        continue
                    finish = candidate.get('finishReason') or finish
                    for part in candidate.get('content', {}).get('parts', ()):
                        parts.append(part)
                        if 'text' in part:
                            yield (ModelReasoningDelta(part['text']) if part.get('thought') else ModelTextDelta(part['text']))
                        if 'functionCall' in part:
                            function = part['functionCall']
                            if 'partialArgs' in function or not isinstance(function.get('args', {}), dict):
                                raise ModelProtocolError('Incomplete Gemini function arguments')
                            try:
                                call = ToolCall(len(calls), function.get('id') or 'gemini-' + uuid4().hex,
                                                function['name'], function.get('args', {}))
                            except (ValueError, KeyError, TypeError) as error:
                                raise ModelProtocolError('Invalid Gemini function call') from error
                            calls.append(call)
                            yield ModelToolCallStarted(call.index, call.id, call.name)
        if finish == 'MAX_TOKENS':
            raise ModelOutputTruncatedError(tuple(call.name for call in calls))
        if finish != 'STOP':
            raise ModelProtocolError('Gemini stream has no successful terminal', reason='stream_termination_missing')
        if len({c.id for c in calls}) != len(calls):
            raise ModelProtocolError('Duplicate Gemini tool call identity')
        if final_usage is not None:
            yield ModelUsageUpdate(token_usage('gemini', final_usage), raw_usage=final_usage, usage_is_final=True)
        yield ModelProviderState('gemini', {'parts': parts})
        for call in calls:
            yield ModelToolCallCompleted(call)
        yield ModelResponseCompleted('tool_use' if calls else 'end_turn')

    async def aclose(self):
        await self._client.aclose()
