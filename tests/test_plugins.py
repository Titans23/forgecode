import asyncio
from dataclasses import replace
import json

import pytest
from typer.testing import CliRunner

from forge.agents import AgentDefinition, PluginSpec
from forge.api import HarnessSession, RunProfile, SessionOptions
from forge.config import ForgeConfig
from forge.plugins import PluginError, PluginHost
from forge.runtime.state import ModelToolCallCompleted, ModelResponseCompleted, ModelTextDelta, ToolCall


def plugin(tmp_path, name='hello', body=None, requires=()):
    path = tmp_path / name
    path.mkdir()
    (path / 'forge-plugin.json').write_text(json.dumps({'id': name, 'api_version': 1,
        'entry_point': 'plugin.py:activate', 'requires': list(requires)}))
    (path / 'plugin.py').write_text(body or '''
count = 0
def activate(ctx, config):
    def greet(args):
        global count
        count += 1
        return {"greeting": "Hello, " + args["name"], "count": count}
    ctx.register_tool("greet", "Greet", {"type":"object", "properties":{"name":{"type":"string"}},
        "required":["name"], "additionalProperties":False}, greet)
''', encoding='utf-8')
    return PluginSpec(str(path), trusted=True)


def test_dependency_validation_happens_before_import(tmp_path):
    spec = plugin(tmp_path, body='raise RuntimeError("must not import")', requires=('missing',))
    async def run():
        with pytest.raises(PluginError, match='Missing'):
            await PluginHost(data_root=tmp_path).load([spec])
        with pytest.raises(PluginError, match='trusted'):
            await PluginHost(data_root=tmp_path).load([replace(spec, trusted=False)])
        a = plugin(tmp_path, 'a', requires=('b',))
        b = plugin(tmp_path, 'b', requires=('a',))
        with pytest.raises(PluginError, match='cycle'):
            await PluginHost(data_root=tmp_path).load([a, b])
        with pytest.raises(PluginError, match='Duplicate'):
            await PluginHost(data_root=tmp_path).load([a, a])
    asyncio.run(run())


def test_failed_activation_releases_effects_and_registrations(tmp_path):
    spec = plugin(tmp_path, body='''
def activate(ctx, config):
    marker = ctx.data_dir / "released"
    ctx.register_service("connection", object())
    ctx.effect(lambda: marker.write_text("closed"))
    raise RuntimeError("activation failed")
''')
    async def run():
        host = PluginHost(data_root=tmp_path / 'data')
        with pytest.raises(RuntimeError) as result:
            await host.load([spec])
        assert result.value.plugin_cleanup['state'] == 'clean'
        assert not host.services
        assert (tmp_path / 'data/plugins/hello/released').read_text() == 'closed'
        assert (await host.close())['state'] == 'clean'
    asyncio.run(run())


def test_effects_unwind_reverse_order_and_preserve_cleanup_failure(tmp_path):
    spec = plugin(tmp_path, body='''
def activate(ctx, config):
    log = ctx.data_dir / "order"
    ctx.effect(lambda: log.write_text(log.read_text() + "1"))
    def fail():
        raise ValueError("cannot close")
    ctx.effect(fail)
    ctx.effect(lambda: log.write_text("2"))
''')
    async def run():
        host = await PluginHost(data_root=tmp_path / 'data').load([spec])
        assert (await host.close())['state'] == 'unknown'
        assert (tmp_path / 'data/plugins/hello/order').read_text() == '21'
    asyncio.run(run())


class ToolModel:
    model = 'fixture'
    provider = 'anthropic'
    def __init__(self):
        self.calls = 0
    async def stream(self, messages, tools=None, system=None):
        self.calls += 1
        if self.calls == 1:
            assert [t['name'] for t in tools] == ['greet']
            yield ModelToolCallCompleted(ToolCall(0, 'one', 'greet', {'name': 'Alice'}))
            yield ModelResponseCompleted('tool_use')
        else:
            assert 'Alice' in str(messages[-1])
            yield ModelTextDelta('done')
            yield ModelResponseCompleted('end_turn')


def test_plugin_tools_use_existing_loop_and_are_session_local(tmp_path):
    spec = plugin(tmp_path)
    definition = replace(AgentDefinition.core(), plugins=(spec,))
    async def run():
        opts = SessionOptions(agent=definition, data_root=tmp_path / 'data',
            config=ForgeConfig(api_key='fixture', model_id='fixture'), profile=RunProfile(intent_routing=False))
        for _ in range(2):
            async with await HarnessSession.open(opts, model_client_factory=lambda *a, **k: ToolModel()) as session:
                result = await session.run('greet Alice')
                assert result.status == 'completed' and result.model_calls == 2
                payload = json.loads(session.conversation.messages[-2]['content'][0]['content'])
                assert json.loads(payload['content'])['count'] == 1
                host = session.conversation.plugin_host
                tool = host.tools['greet']
                assert not (await tool.run({'name': 42})).success
                assert not tool.provenance['sandbox_covered']
            assert session.cleanup['state'] == 'clean'
            assert not host.tools and not session.conversation.registry.names
    asyncio.run(run())


def test_cancel_plugin_tool_releases_resource(tmp_path):
    spec = plugin(tmp_path, body='''
import asyncio
def activate(ctx, config):
    event = asyncio.Event()
    ctx.register_service("started", event)
    ctx.effect(lambda: (ctx.data_dir / "closed").write_text("yes"))
    async def wait(args):
        event.set()
        await asyncio.Event().wait()
    ctx.register_tool("greet", "Wait", {"type":"object"}, wait)
''')
    async def run():
        opts = SessionOptions(agent=replace(AgentDefinition.core(), plugins=(spec,)), data_root=tmp_path / 'data',
                              config=ForgeConfig(api_key='fixture', model_id='fixture'))
        session = await HarnessSession.open(opts, model_client_factory=lambda *a, **k: ToolModel())
        host = session.conversation.plugin_host
        task = asyncio.create_task(session.run('wait'))
        await asyncio.wait_for(host.services['started'].wait(), timeout=5)
        await session.close()
        assert task.cancelled()
        assert (tmp_path / 'data/plugins/hello/closed').read_text() == 'yes'
    asyncio.run(run())


def test_cli_enable_is_explicit_and_preserves_unrelated_settings(tmp_path, monkeypatch):
    from forge.plugin_cli import app
    from forge.compositions import read_settings, resolve_agent
    monkeypatch.setenv('FORGE_DATA_DIR', str(tmp_path / 'home'))
    path = tmp_path / 'home/settings.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'hooks': {}, 'agents': {'notes': {'base': 'core', 'plugins': ['hello']}}}))
    spec = plugin(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ['enable', spec.source]).exit_code != 0
    assert runner.invoke(app, ['enable', spec.source, '--trust']).exit_code == 0
    assert read_settings()['hooks'] == {}
    assert resolve_agent('notes').plugins[0].trusted
    assert runner.invoke(app, ['disable', 'hello']).exit_code == 0
    with pytest.raises(ValueError, match='enabled'):
        resolve_agent('notes')


def test_closed_scope_refuses_late_registration_and_native_label(tmp_path):
    from forge.plugins import PluginContext
    from forge.tools.base import ToolRegistry
    from forge.sandbox.tool_backend import FileToolBackend
    async def run():
        host = await PluginHost(data_root=tmp_path).load([plugin(tmp_path)])
        registry = ToolRegistry()
        host.bind_registry(registry)
        backend = object.__new__(FileToolBackend)
        backend.mode = 'workspace-write'
        result = await backend.execute(ToolCall(0, 'one', 'greet', {'name': 'Bob'}), registry)
        assert result.success and result.metadata['sandbox_covered'] is False
        assert result.metadata['execution_boundary'] == 'trusted-plugin-host'
        backend.mode = 'strict'
        assert not (await backend.execute(ToolCall(0, 'two', 'greet', {'name': 'Bob'}), registry)).success
        await host.close()
        with pytest.raises(PluginError, match='closed'):
            PluginContext(host, 'hello').register_service('late', object())
        assert not host.services
    asyncio.run(run())


def test_sandbox_provider_selection_is_explicit_and_serializable(tmp_path):
    assert AgentDefinition().sandbox_provider == 'native'
    assert AgentDefinition.core().sandbox_provider is None
    host = AgentDefinition(name='host', sandbox_provider=None)
    assert AgentDefinition.from_dict(host.as_dict()) == host
    assert SessionOptions(tmp_path, agent=host).sandbox == 'full-access'
    assert SessionOptions(tmp_path).sandbox == 'workspace-write'
    with pytest.raises(ValueError, match='requires a sandbox provider'):
        SessionOptions(tmp_path, agent=host, sandbox='read-only')


@pytest.mark.parametrize('failure', ['raise', 'cancel', 'wrong-mode'])
def test_provider_startup_failure_closes_plugin_scope_without_model_or_fallback(tmp_path, failure):
    body = '''
import asyncio
from dataclasses import replace
def activate(ctx, config):
    ctx.effect(lambda: (ctx.data_dir / "closed").write_text("closed"))
    async def open_execution(request):
        assert not hasattr(request, "config")
        (ctx.data_dir / "selected").write_text(request.mode)
        if config["failure"] == "raise":
            raise RuntimeError("provider failed")
        if config["failure"] == "cancel":
            raise asyncio.CancelledError()
        from forge.sandbox.provider import open_host_execution
        return await open_host_execution(replace(request, mode="full-access"))
    ctx.register_sandbox_provider("custom", open_execution)
'''
    spec = replace(plugin(tmp_path, body=body), config={'failure': failure})
    root = tmp_path / 'project'
    root.mkdir()
    definition = AgentDefinition(plugins=(spec,), sandbox_provider='custom')
    def no_model(*args, **kwargs):
        pytest.fail('Failed provider must not construct a model')
    async def run():
        from forge.protocol import ContractError
        expected = {'raise': RuntimeError, 'cancel': asyncio.CancelledError, 'wrong-mode': ContractError}[failure]
        with pytest.raises(expected) as result:
            await HarnessSession.open(SessionOptions(root, agent=definition, data_root=tmp_path / 'data',
                config=ForgeConfig(api_key='fixture', model_id='fixture')), model_client_factory=no_model)
        assert result.value.plugin_cleanup['state'] == 'clean'
        assert (tmp_path / 'data/plugins/hello/selected').read_text() == 'workspace-write'
        assert (tmp_path / 'data/plugins/hello/closed').read_text() == 'closed'
        if failure == 'wrong-mode':
            assert result.value.cleanup_report['state'] == 'clean'
    asyncio.run(run())


def test_sandbox_registration_cannot_replace_defaults_or_outlive_scope(tmp_path):
    from forge.plugins import PluginContext
    async def factory(request):
        raise AssertionError('Registration does not execute a provider')
    async def run():
        first, second = PluginHost(data_root=tmp_path), PluginHost(data_root=tmp_path)
        ctx = PluginContext(first, 'fixture')
        for name in ('native', 'auto'):
            with pytest.raises(PluginError, match='reserved'):
                ctx.register_sandbox_provider(name, factory)
        ctx.register_sandbox_provider('custom', factory)
        assert second.sandbox_providers == {}
        with pytest.raises(PluginError, match='Duplicate'):
            ctx.register_sandbox_provider('custom', factory)
        await first.close()
        assert first.sandbox_providers == {}
        with pytest.raises(PluginError, match='closed'):
            ctx.register_sandbox_provider('late', factory)
    asyncio.run(run())


def test_managed_coding_child_cannot_replace_parent_provider(tmp_path):
    spec = plugin(tmp_path, body='''
from forge.agents import AgentDefinition
def activate(ctx, config):
    async def delegate(args):
        try:
            await ctx.run_child("child", AgentDefinition(name="child", sandbox_provider=None))
        except ValueError as error:
            return str(error)
        raise AssertionError("Child changed provider")
    ctx.register_tool("delegate", "Delegate", {"type":"object"}, delegate)
''')
    class Model:
        model = 'fixture'
        calls = 0
        async def stream(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield ModelToolCallCompleted(ToolCall(0, 'child', 'delegate', {}))
                yield ModelResponseCompleted('tool_use')
            else:
                assert 'Child sandbox provider must be inherited' in str(kwargs['messages'][-1])
                yield ModelTextDelta('blocked')
                yield ModelResponseCompleted('end_turn')
    async def run():
        root = tmp_path / 'project'
        root.mkdir()
        made = []
        def create(*args, **kwargs):
            made.append(Model())
            return made[-1]
        opts = SessionOptions(root, agent=AgentDefinition(plugins=(spec,)), sandbox='full-access',
            data_root=tmp_path / 'data', config=ForgeConfig(api_key='fixture', model_id='fixture'),
            profile=RunProfile(intent_routing=False))
        async with await HarnessSession.open(opts, model_client_factory=create) as session:
            assert (await session.run('delegate')).text == 'blocked'
            assert len(made) == 1
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())
