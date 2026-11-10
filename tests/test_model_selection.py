import asyncio
from dataclasses import replace
import json

import httpx
import pytest

from forge.api import AgentDefinition, HarnessSession, RunProfile, SessionOptions
from forge.agents import PluginSpec
from forge.config import ForgeConfig
from forge.runtime.capabilities import ModelCapabilities
from forge.runtime.model_registry import ProviderRegistry, portable_history
from forge.runtime.state import (ModelTextDelta, ModelResponseCompleted, ModelProviderState,
    ModelToolCallCompleted, ModelUsageUpdate, ModelProtocolError, ToolCall)


class Model:
    def __init__(self, config):
        self.model, self.provider = config.model_id, config.provider
        self.context_window, self.max_tokens = config.context_window, config.max_tokens
        self.closed = False
        self.capabilities = ModelCapabilities(image_transport=config.provider != 'deepseek')

    async def stream(self, **kwargs):
        yield ModelTextDelta(self.model)
        yield ModelProviderState(self.provider, {'opaque': 'private-state'})
        yield ModelResponseCompleted('end_turn')

    async def aclose(self):
        self.closed = True


def options(tmp_path):
    return SessionOptions(agent=AgentDefinition.core(), data_root=tmp_path / 'data',
        config=ForgeConfig(api_key='initial-fixture', model_id='alpha'),
        models={'second': {'provider': 'openai_chat', 'model': 'beta', 'api_key_env': 'SECOND_KEY'},
                'small': {'provider': 'openai_chat', 'model': 'small', 'api_key_env': 'SECOND_KEY',
                          'context_window': 4096, 'max_tokens': 1024}})


def test_switch_is_durable_nonexecuting_and_preserves_original_journal(tmp_path, monkeypatch):
    monkeypatch.setenv('SECOND_KEY', 'second-secret-fixture')
    clients = []
    def create(config, **kwargs):
        client = Model(config)
        clients.append(client)
        return client
    async def run():
        opts = options(tmp_path)
        async with await HarnessSession.open(opts, model_client_factory=create) as session:
            await session.run('hello')
            turn = session.conversation.turn_state
            before = session.journal.path.read_bytes()
            selected = await session.set_model('second')
            assert selected['model'] == 'beta'
            assert session.conversation.turn_state is turn and turn.model_calls == 1
            assert all('provider_state' not in m for m in session.conversation.messages)
            assert session.journal.path.read_bytes().startswith(before)
            assert b'second-secret-fixture' not in session.journal.path.read_bytes()
            identity = session.session_id
            assert (await session.run('again')).text == 'beta'
        assert all(c.closed for c in clients)
        async with await HarnessSession.open(opts, model_client_factory=create, resume_identifier=identity) as resumed:
            assert resumed.conversation.client.model == 'beta'
            assert resumed.conversation.model_alias == 'second'
            assert (await resumed.run('restored')).text == 'beta'
    asyncio.run(run())


def test_rejected_switch_keeps_client_history_and_journal(tmp_path, monkeypatch):
    monkeypatch.setenv('SECOND_KEY', 'fixture')
    clients = []
    def create(config, **kwargs):
        result = Model(config)
        clients.append(result)
        return result
    async def run():
        async with await HarnessSession.open(options(tmp_path), model_client_factory=create) as session:
            original = session.conversation.client
            before = session.journal.path.read_bytes()
            with pytest.raises(ValueError):
                await session.set_model('small')
            assert session.conversation.client is original
            assert session.journal.path.read_bytes() == before
            assert clients[-1].closed
            session.conversation._running = True
            with pytest.raises(RuntimeError):
                await session.set_model('second')
            session.conversation._running = False
    asyncio.run(run())


def test_portable_history_keeps_images_and_tool_results():
    image = {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'AA=='}}
    original = [{'role': 'assistant', 'content': [{'type': 'tool_use', 'id': '1', 'name': 'f', 'input': {'x': 1}}],
                 'provider_state': {'provider': 'gemini', 'data': {'private': 'secret'}}},
                {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': '1', 'content': [image]}]}]
    projected = portable_history(original)
    assert projected[1]['content'][-1] == image
    assert 'provider_state' not in projected[0] and 'provider_state' in original[0]
    assert 'Earlier tool call' in projected[0]['content'][0]['text']


def test_plugin_child_uses_role_model_and_shared_budget(tmp_path, monkeypatch):
    monkeypatch.setenv('SECOND_KEY', 'fixture')
    path = tmp_path / 'delegate'
    path.mkdir()
    (path / 'forge-plugin.json').write_text(json.dumps({'id': 'delegate', 'api_version': 1}))
    (path / 'plugin.py').write_text('''from forge.agents import AgentDefinition
def activate(ctx, config):
    async def delegate(args):
        result = await ctx.run_child("child task", AgentDefinition.core("child"))
        return result.text
    ctx.register_tool("delegate", "Delegate", {"type":"object"}, delegate)
''')
    class Parent(Model):
        calls = 0
        async def stream(self, **kwargs):
            self.calls += 1
            if self.model == 'alpha' and self.calls == 1:
                yield ModelToolCallCompleted(ToolCall(0, 'one', 'delegate', {}))
                yield ModelResponseCompleted('tool_use')
            else:
                yield ModelTextDelta(self.model)
                yield ModelResponseCompleted('end_turn')
    async def run(limit):
        opts = options(tmp_path)
        opts = replace(opts, agent=replace(opts.agent, plugins=(PluginSpec(str(path), trusted=True),),
                                          role_models={'worker': 'second'}),
                       profile=RunProfile(max_model_calls=limit, intent_routing=False))
        made = []
        def create(config, **kwargs):
            made.append(Parent(config))
            return made[-1]
        async with await HarnessSession.open(opts, model_client_factory=create) as session:
            result = await session.run('delegate')
            assert result.model_calls == limit
            assert any(c.model == 'beta' and c.closed for c in made)
            return result.status
    assert asyncio.run(run(3)) == 'completed'
    assert asyncio.run(run(2)) == 'failed'


@pytest.mark.parametrize('terminal', [True, False])
def test_gemini_stream_requires_terminal_before_tool_execution(terminal):
    from forge.runtime.gemini import GeminiModelClient
    async def run():
        requests = []
        chunks = [{'candidates': [{'content': {'parts': [{'functionCall': {'name': 'lookup', 'args': {'x': 1}},
                                                             'thoughtSignature': 'opaque-signature'}]}}]}]
        chunks.insert(0, {'candidates': [{'content': {'parts': [{'text': 'Readable thought', 'thought': True}]}}]})
        if terminal:
            chunks.append({'candidates': [{'finishReason': 'STOP'}],
                           'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 2,
                                             'cachedContentTokenCount': 3, 'thoughtsTokenCount': 4}})
        def handler(request):
            requests.append(request)
            return httpx.Response(200, text=''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks))
        client = GeminiModelClient(ForgeConfig(api_key='fixture', model_id='test', provider='gemini'),
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), max_attempts=1)
        events = []
        try:
            async for event in client.stream([{'role': 'user', 'content': 'test'}]):
                events.append(event)
        except ModelProtocolError:
            assert not terminal
        finally:
            await client.aclose()
        calls = [e for e in events if isinstance(e, ModelToolCallCompleted)]
        assert bool(calls) == terminal
        assert requests[0].headers['x-goog-api-key'] == 'fixture'
        assert 'fixture' not in str(requests[0].url)
        if terminal:
            usage = [e for e in events if isinstance(e, ModelUsageUpdate)][-1]
            assert usage.usage.total_input_tokens == 10 and usage.usage.output_tokens == 6
            state = next(e for e in events if isinstance(e, ModelProviderState))
            assert state.data['parts'][1]['thoughtSignature'] == 'opaque-signature'
        from forge.runtime.state import ModelReasoningDelta
        assert [e.text for e in events if isinstance(e, ModelReasoningDelta)] == ['Readable thought']
    asyncio.run(run())


def test_registry_custom_provider_and_duplicate():
    registry = ProviderRegistry()
    registry.register('company', lambda config, **kwargs: Model(config))
    result = registry.create(ForgeConfig(api_key='fixture', model_id='internal', provider='company',
                                        base_url='https://offline.invalid'))
    assert result.provider == 'company'
    with pytest.raises(ValueError):
        registry.register('anthropic', Model)


def test_openai_chat_stream_and_token_field():
    from openai import AsyncOpenAI
    from forge.runtime.providers import NativeModelClient
    async def run():
        captured = []
        chunks = [dict(id='r', object='chat.completion.chunk', created=1, model='test',
            choices=[{'index': 0, 'delta': {'content': 'hello'}, 'finish_reason': None}]),
            dict(id='r', object='chat.completion.chunk', created=1, model='test',
                 choices=[{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]),
            dict(id='r', object='chat.completion.chunk', created=1, model='test', choices=[],
                 usage={'prompt_tokens': 12, 'completion_tokens': 4, 'total_tokens': 16})]
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, text=''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n')
        sdk = AsyncOpenAI(api_key='fixture', http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        adapter = NativeModelClient(ForgeConfig(provider='openai_chat', api_key='fixture', model_id='test'), client=sdk)
        try:
            events = [e async for e in adapter.stream([{'role': 'user', 'content': 'test'}])]
            assert captured[0]['max_completion_tokens'] == 65536
            assert 'reasoning_content' not in str(captured[0])
            assert next(e for e in events if isinstance(e, ModelUsageUpdate)).usage.total_input_tokens == 12
        finally:
            await adapter.aclose()
    asyncio.run(run())


def test_close_drains_all_clients_even_when_one_close_fails(tmp_path, monkeypatch):
    monkeypatch.setenv('SECOND_KEY', 'fixture')
    made = []
    class FailingClose(Model):
        async def aclose(self):
            self.closed = True
            if self.model == 'alpha':
                raise OSError('fixture close error')
    def create(config, **kwargs):
        made.append(FailingClose(config))
        return made[-1]
    async def run():
        session = await HarnessSession.open(options(tmp_path), model_client_factory=create)
        await session.set_model('second')
        cleanup = await session.close()
        assert cleanup['state'] == 'unknown' and cleanup['model_cleanup_error'] == 'OSError'
        assert all(c.closed for c in made)
    asyncio.run(run())


def test_image_switch_requires_declared_modality(tmp_path, monkeypatch):
    monkeypatch.setenv('SECOND_KEY', 'fixture')
    async def run():
        async with await HarnessSession.open(options(tmp_path), model_client_factory=lambda c, **k: Model(c)) as session:
            image = {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'AA=='}}
            session.conversation.messages.append({'role': 'user', 'content': [image]})
            with pytest.raises(ValueError, match='image'):
                await session.set_model('second')
            assert session.conversation.client.model == 'alpha'
            session.conversation.model_aliases['second']['input_modalities'] = ['text', 'image']
            await session.set_model('second')
            assert session.conversation.messages[0]['content'] == [image]
    asyncio.run(run())


def test_registered_provider_loads_and_closes_before_its_service(tmp_path):
    path = tmp_path / 'provider'
    path.mkdir()
    (path / 'forge-plugin.json').write_text(json.dumps({'id': 'company', 'api_version': 1}))
    (path / 'plugin.py').write_text('''from forge.runtime.state import ModelTextDelta, ModelResponseCompleted
def activate(ctx, config):
    state = {"closed": False, "client_closed": False}
    ctx.register_service("state", state, close=lambda: state.update(closed=True))
    class Client:
        provider = "company"
        model = "internal"
        async def stream(self, **kwargs):
            yield ModelTextDelta("internal answer")
            yield ModelResponseCompleted("end_turn")
        async def aclose(self):
            assert not state["closed"]
            state["client_closed"] = True
    ctx.register_provider("company", lambda config, **kwargs: Client())
''')
    async def run():
        definition = replace(AgentDefinition.core(), plugins=(PluginSpec(str(path), trusted=True),))
        opts = SessionOptions(agent=definition, data_root=tmp_path / 'data',
            config=ForgeConfig(provider='company', model_id='internal', api_key='fixture', base_url='https://offline.invalid'))
        async with await HarnessSession.open(opts) as session:
            state = session.conversation.plugin_host.services['state']
            assert (await session.run('hello')).text == 'internal answer'
        assert state == {'closed': True, 'client_closed': True}
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_managed_child_inherits_parent_session_denials(tmp_path):
    from forge.permissions.policy import PermissionRule
    path = tmp_path / 'child-plugin'
    path.mkdir()
    (path / 'forge-plugin.json').write_text(json.dumps({'id': 'child', 'api_version': 1}))
    (path / 'plugin.py').write_text('''from pathlib import Path
from forge.agents import AgentDefinition, PluginSpec
def activate(ctx, config):
 async def delegate(args):
  child = AgentDefinition(name='child', base='core', plugins=(PluginSpec(str(Path(__file__).parent), trusted=True),), tools=('mutate',))
  result = await ctx.run_child('child', child)
  return result.status
 ctx.register_tool('delegate', 'delegate', {'type':'object'}, delegate)
 ctx.register_tool('mutate', 'mutate', {'type':'object'}, lambda a: (ctx.data_dir / 'must-not-exist').write_text('mutated'), effect='workspace_write')
''')
    class Client(Model):
        calls = 0
        async def stream(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                name = 'delegate' if self.model == 'main' else 'mutate'
                yield ModelToolCallCompleted(ToolCall(0, name, name, {}))
                yield ModelResponseCompleted('tool_use')
            else:
                yield ModelTextDelta('done')
                yield ModelResponseCompleted('end_turn')
    async def run():
        definition = AgentDefinition(name='parent', base='core', plugins=(PluginSpec(str(path), trusted=True),), role_models={'worker': 'child'})
        opts = SessionOptions(agent=definition, data_root=tmp_path / 'data', approval='auto',
            config=ForgeConfig(api_key='unused', model_id='main'), models={'child': {'provider':'anthropic','model':'child'}})
        async with await HarnessSession.open(opts, model_client_factory=lambda c, **k: Client(c)) as session:
            session.conversation.permission_manager.session_rules.append(PermissionRule('deny', 'plugin.child.write'))
            await session.run('delegate')
        assert not list((tmp_path / 'data').rglob('must-not-exist'))
        journals = '\n'.join(p.read_text(encoding='utf-8') for p in (tmp_path / 'data').rglob('*.jsonl'))
        assert 'A matching deny rule applies' in journals
    asyncio.run(run())
