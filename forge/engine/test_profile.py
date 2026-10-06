"""Explicit offline test profile: only the model is scripted, tools remain real."""
import asyncio
from dataclasses import dataclass

from forge.application.models import ContractError, strict_loads
from forge.permissions.policy import ApprovalResponse
from forge.permissions.risk import classify_tool_call
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
        self.calls = 0

    async def stream(self, messages, tools=None, system=None):
        if not self.responses:
            raise RuntimeError('Scripted test response exhausted')
        response = self.responses.pop(0)
        self.calls += 1
        delay = response.get('delay_seconds', 0)
        if type(delay) not in (int, float) or not 0 <= delay <= 60:
            raise ValueError('Invalid scripted test delay')
        await asyncio.sleep(delay)
        if response.get('usage') is not None:
            yield ModelUsageUpdate(TokenUsage(**response['usage']))
        for text in response.get('text_chunks', []):
            yield ModelTextDelta(text)
        for index, call in enumerate(response.get('tool_calls', [])):
            yield ModelToolCallCompleted(ToolCall(index, call['id'], call['name'], call['arguments']))
        if response.get('error') == 'connection_lost':
            raise ConnectionError('Scripted test provider interruption')

    async def aclose(self):
        pass


SCHEMA = 'forge.scripted-model.v1'
TOOL_EFFECTS = {'read_file': 'read_only', 'apply_patch': 'workspace_write',
                'verify': 'process', 'run_command': 'process', 'finish_task': 'read_only'}


@dataclass(frozen=True)
class ScriptedProfile:
    credentials: MemoryCredentials
    model_client_factory: object
    approval_handler: object


def scripted_profile(value):
    """Validate the complete bounded script before constructing a model or tool."""
    fields = {'schema_version', 'origin', 'max_steps', 'allowed_tools', 'credentials', 'responses', 'approve_scripted_calls'}
    if not isinstance(value, dict) or set(value) != fields or value['schema_version'] != SCHEMA or value['origin'] != 'scripted':
        raise ContractError('Invalid versioned scripted test fixture')
    if type(value['max_steps']) is not int or not 1 <= value['max_steps'] <= 32:
        raise ContractError('Scripted max_steps must be between 1 and 32')
    allowed = value['allowed_tools']
    if not isinstance(allowed, list) or not all(isinstance(name, str) and name in TOOL_EFFECTS for name in allowed) or len(set(allowed)) != len(allowed):
        raise ContractError('Invalid scripted tool allowlist')
    if not isinstance(value['credentials'], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value['credentials'].items()) or type(value['approve_scripted_calls']) is not bool:
        raise ContractError('Invalid scripted credentials or approvals')
    responses = value['responses']
    if not isinstance(responses, list) or not 1 <= len(responses) <= value['max_steps']:
        raise ContractError('Scripted response limit exceeded')
    identifiers, requests = set(), []
    for response in responses:
        if not isinstance(response, dict) or set(response) - {'usage', 'text_chunks', 'tool_calls', 'delay_seconds', 'error'}:
            raise ContractError('Invalid scripted response fields')
        usage = response.get('usage')
        if usage is not None and (not isinstance(usage, dict) or set(usage) - {'input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'} or not {'input_tokens', 'output_tokens'} <= usage.keys() or any(type(n) is not int or n < 0 for n in usage.values())):
            raise ContractError('Invalid scripted usage')
        chunks, calls = response.get('text_chunks', []), response.get('tool_calls', [])
        delay = response.get('delay_seconds', 0)
        if not isinstance(chunks, list) or not all(isinstance(s, str) for s in chunks) or not isinstance(calls, list) or len(calls) > 4 or type(delay) not in (int, float) or not 0 <= delay <= 60 or response.get('error') not in (None, 'connection_lost'):
            raise ContractError('Invalid scripted response contents')
        for call in calls:
            if not isinstance(call, dict) or set(call) != {'id', 'name', 'arguments'} or not isinstance(call['id'], str) or not call['id'] or call['id'] in identifiers or call['name'] not in allowed or not isinstance(call['arguments'], dict):
                raise ContractError('Invalid, duplicate or disallowed scripted call')
            identifiers.add(call['id'])
            if len(identifiers) > 64:
                raise ContractError('Scripted tool limit exceeded')
            requests.append(classify_tool_call(ToolCall(0, call['id'], call['name'], call['arguments']), TOOL_EFFECTS[call['name']]))
    def factory(config, *, max_tokens=None):
        return ScriptedModelClient(config, responses, max_tokens=max_tokens)

    async def approve(request):
        accepted = value['approve_scripted_calls'] and not request.hard_deny and request in requests
        return ApprovalResponse('allow_once' if accepted else 'deny', 'Explicit scripted test call' if accepted else 'Call is outside scripted test approvals')

    return ScriptedProfile(MemoryCredentials(value['credentials']), factory, approve)


def load_scripted_profile(path):
    return scripted_profile(strict_loads(path.read_bytes()))
