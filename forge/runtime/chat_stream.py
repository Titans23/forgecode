"""One Chat Completions stream decoder for SDK and opt-in model adapters."""
import json
from contextlib import aclosing

from forge.runtime.state import (ModelProtocolError, ModelOutputTruncatedError, ModelTextDelta,
    ModelReasoningDelta, ModelUsageUpdate, ModelToolCallStarted, ModelToolCallArgumentsDelta,
    ModelToolCallCompleted, ModelResponseCompleted, ModelProviderState, ToolCall)


def field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


async def decode_chat(stream, *, provider, allowed_names=None):
    """Publish fragments promptly, but complete calls only after terminal validation."""
    pending, reasoning, finish = {}, [], None
    async for chunk in stream:
        usage = field(chunk, 'usage')
        choices = field(chunk, 'choices', ()) or ()
        if usage is not None:
            from forge.observability.usage_ledger import usage_dict, token_usage
            raw = usage_dict(usage)
            yield ModelUsageUpdate(token_usage(provider, raw), raw_usage=raw,
                provider_request_id=field(chunk, 'id'), returned_model=field(chunk, 'model'),
                usage_is_final=not choices or any(field(c, 'finish_reason') for c in choices))
        for choice in choices:
            if field(choice, 'index', 0) != 0:
                raise ModelProtocolError('Only one completion choice is supported')
            delta = field(choice, 'delta')
            if text := field(delta, 'reasoning_content'):
                reasoning.append(text)
                yield ModelReasoningDelta(text)
            if text := field(delta, 'content'):
                yield ModelTextDelta(text)
            for part in field(delta, 'tool_calls', ()) or ():
                index = field(part, 'index', 0)
                item = pending.setdefault(index, {'id': '', 'name': '', 'arguments': '', 'started': False, 'emitted': 0})
                if identity := field(part, 'id'):
                    item['id'] = identity
                function = field(part, 'function')
                item['name'] += field(function, 'name', '') or ''
                item['arguments'] += field(function, 'arguments', '') or ''
                if not item['started'] and item['id'] and item['name'] and (
                        allowed_names is None or item['name'] in allowed_names):
                    item['started'] = True
                    yield ModelToolCallStarted(index, item['id'], item['name'])
                if item['started'] and item['emitted'] < len(item['arguments']):
                    yield ModelToolCallArgumentsDelta(index, item['arguments'][item['emitted']:])
                    item['emitted'] = len(item['arguments'])
            finish = field(choice, 'finish_reason') or finish
    if finish in {'length', 'max_tokens'}:
        raise ModelOutputTruncatedError(tuple(item['name'] for item in pending.values()))
    if finish not in {'stop', 'tool_calls'} or (pending and finish != 'tool_calls'):
        raise ModelProtocolError('Missing successful stream terminal', reason='stream_termination_missing')
    calls = []
    for index, item in sorted(pending.items()):
        if not item['id'] or not item['name']:
            raise ModelProtocolError('Incomplete tool identity', reason='incomplete_tool_call')
        if allowed_names is not None and item['name'] not in allowed_names:
            raise ModelProtocolError('Model requested unavailable tool', reason='unavailable_tool', tool_name=item['name'])
        try:
            arguments = json.loads(item['arguments'] or '{}',
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if not isinstance(arguments, dict):
                raise ValueError('Arguments must be an object')
        except (TypeError, ValueError) as error:
            raise ModelProtocolError('Invalid tool arguments', reason='invalid_tool_arguments', tool_name=item['name']) from error
        calls.append(ToolCall(index, item['id'], item['name'], arguments))
    if len({call.id for call in calls}) != len(calls):
        raise ModelProtocolError('Duplicate tool call identity')
    yield ModelProviderState(provider, {'reasoning_content': ''.join(reasoning)})
    for call in calls:
        yield ModelToolCallCompleted(call)
    yield ModelResponseCompleted('tool_use' if calls else 'end_turn')


async def chat_response(client, request, *, provider, allowed_names=None):
    stream = await client.chat.completions.create(**request)
    try:
        async with aclosing(decode_chat(stream, provider=provider, allowed_names=allowed_names)) as events:
            async for event in events:
                yield event
    finally:
        close = getattr(stream, 'close', None)
        if close is not None:
            await close()


def chat_messages(messages, system=None, *, deepseek=True):
    output = [{'role': 'system', 'content': system}] if system else []
    for message in messages:
        content, calls, results, images = [], [], [], []
        parts = message.get('content', [])
        if isinstance(parts, str):
            parts = [{'type': 'text', 'text': parts}]
        for part in parts:
            kind = part.get('type')
            if kind == 'text':
                content.append(part.get('text', ''))
            elif kind == 'image' and not deepseek:
                source = part['source']
                images.append({'type': 'image_url', 'image_url': {'url':
                    'data:' + source['media_type'] + ';base64,' + source['data']}})
            elif kind == 'tool_use':
                calls.append({'id': part['id'], 'type': 'function', 'function': {
                    'name': part['name'], 'arguments': json.dumps(part['input'], ensure_ascii=False)}})
            elif kind == 'tool_result':
                if isinstance(part.get('content'), list) and any(p.get('type') == 'image' for p in part['content']):
                    if deepseek:
                        raise ModelProtocolError('This DeepSeek adapter does not support image input.')
                    for block in part['content']:
                        if block['type'] == 'image':
                            source = block['source']
                            images.append({'type': 'image_url', 'image_url': {'url':
                                'data:' + source['media_type'] + ';base64,' + source['data']}})
                    part = {**part, 'content': [b for b in part['content'] if b['type'] != 'image']}
                result = part.get('content', '')
                results.append({'role': 'tool', 'tool_call_id': part['tool_use_id'],
                                'content': result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)})
            else:
                raise ModelProtocolError(f'Unsupported message part for DeepSeek: {kind}')
        if content or calls or images and not results:
            item = {'role': message['role'], 'content': '\n'.join(content)}
            if images and not results:
                item['content'] = [{'type': 'text', 'text': '\n'.join(content)}, *images]
            if calls:
                item['tool_calls'] = calls
            if message['role'] == 'assistant' and deepseek:
                state = message.get('provider_state', {})
                item['reasoning_content'] = (state.get('data', {}).get('reasoning_content', '')
                                             if state.get('provider') == 'deepseek' else '')
            output.append(item)
        output.extend(results)
        if images and results:
            output.append({'role': 'user', 'content': images})
    return output
