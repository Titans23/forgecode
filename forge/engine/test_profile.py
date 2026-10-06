"""Explicit offline test profile: only the model is scripted, tools remain real."""
import asyncio

from forge.application.models import ContractError, strict_loads
from forge.runtime.state import ModelTextDelta, ModelToolCallCompleted, ModelUsageUpdate, TokenUsage, ToolCall


class MemoryCredentials:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def resolve(self, connection_id):
        return self.values.get(connection_id)


class ScriptedModelClient:
    provider = 'anthropic'

    def __init__(self, config, responses, *, max_tokens=None):
        self.model = config.model_id
        self.max_tokens = max_tokens or config.max_tokens
        self.context_window = config.context_window
        self.responses = list(responses)

    async def stream(self, messages, tools=None, system=None):
        if not self.responses:
            raise RuntimeError('Scripted test response exhausted')
        response = self.responses.pop(0)
        delay = response.get('delay_seconds', 0)
        if type(delay) not in (int, float) or not 0 <= delay <= 60:
            raise ValueError('Invalid scripted test delay')
        await asyncio.sleep(delay)
        yield ModelUsageUpdate(TokenUsage(**response['usage']))
        for text in response.get('text_chunks', []):
            yield ModelTextDelta(text)
        for index, call in enumerate(response.get('tool_calls', [])):
            yield ModelToolCallCompleted(ToolCall(index, call['id'], call['name'], call['arguments']))

    async def aclose(self):
        pass


def load_scripted_profile(path):
    value = strict_loads(path.read_bytes())
    if not isinstance(value, dict) or set(value) != {'credentials', 'responses'} or not isinstance(value['credentials'], dict) or not isinstance(value['responses'], list):
        raise ContractError('Invalid scripted test fixture')
    def factory(config, *, max_tokens=None):
        return ScriptedModelClient(config, value['responses'], max_tokens=max_tokens)
    return MemoryCredentials(value['credentials']), factory
