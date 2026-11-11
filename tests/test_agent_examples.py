import asyncio
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

import pytest

from forge.agents import AgentDefinition, PluginSpec
from forge.api import HarnessSession, SessionOptions, RunProfile
from forge.config import ForgeConfig
from forge.delivery import load_definition
from forge.runtime.state import ModelToolCallCompleted, ModelTextDelta, ModelResponseCompleted, ToolCall
from forge.validation import check_agent

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples/agents'


@pytest.mark.parametrize('name', ['notes', 'expenses', 'coordinator'])
def test_public_example_acceptance_without_repository(tmp_path, name):
    path = EXAMPLES / name
    agent = load_definition(path / 'agent.json', trust=True)
    result = asyncio.run(check_agent(agent, json.loads((path / 'acceptance.json').read_text()), work_dir=tmp_path))
    assert result['status'] == 'passed', result
    assert result['model_evaluation'] == {'status': 'unverified', 'api_calls': 0}


def test_coordinator_uses_worker_role_and_shared_usage(tmp_path):
    agent = load_definition(EXAMPLES / 'coordinator/agent.json', trust=True)
    models = []
    class Model:
        def __init__(self, config):
            self.model = config.model_id
            self.calls = 0
            models.append(self)
        async def stream(self, messages, **kwargs):
            self.calls += 1
            if self.model == 'main' and self.calls == 1:
                yield ModelToolCallCompleted(ToolCall(0, 'delegate', 'coordinate', {'events': ['Meeting', 'Lunch']}))
                yield ModelResponseCompleted('tool_use')
            else:
                yield ModelTextDelta('worker summary' if self.model == 'worker' else 'all events summarized')
                yield ModelResponseCompleted('end_turn')
        async def aclose(self):
            pass
    async def run():
        options = SessionOptions(agent=agent, data_root=tmp_path, approval='auto',
            config=ForgeConfig(api_key='unused', model_id='main'),
            models={'local-worker': {'provider': 'anthropic', 'model': 'worker'}},
            profile=RunProfile(intent_routing=False))
        async with await HarnessSession.open(options, model_client_factory=lambda c, **k: Model(c)) as session:
            result = await session.run('Summarize local events')
            assert result.status == 'completed' and result.model_calls == 4
            assert 'worker summary' in str(session.conversation.messages)
        assert [m.model for m in models] == ['main', 'worker', 'worker']
    asyncio.run(run())


def test_channel_plugin_uses_public_session_and_owned_service(tmp_path):
    plugin = tmp_path / 'channel'
    plugin.mkdir()
    (plugin / 'forge-plugin.json').write_text(json.dumps({'id': 'channel', 'api_version': 1}))
    (plugin / 'plugin.py').write_text('''def activate(ctx, config):
 class Channel:
  closed = False
  async def receive(self, session, text):
   return (await session.run(text)).text
  def close(self): self.closed = True
 channel = Channel()
 ctx.register_service('channel', channel, close=channel.close)
''')
    class Model:
        model = 'channel-fixture'
        async def stream(self, **kwargs):
            yield ModelTextDelta('local response')
            yield ModelResponseCompleted('end_turn')
    async def run():
        options = SessionOptions(agent=replace(AgentDefinition.core(), plugins=(PluginSpec(str(plugin), trusted=True),)),
            data_root=tmp_path / 'data', config=ForgeConfig(api_key='unused', model_id='channel-fixture'))
        async with await HarnessSession.open(options, model_client_factory=lambda *a, **k: Model()) as session:
            channel = session.service('channel')
            assert await channel.receive(session, 'local input') == 'local response'
        assert channel.closed
        with pytest.raises(ValueError):
            session.service('channel')
    asyncio.run(run())


def test_internal_http_auth_errors_timeout_with_controlled_service(tmp_path, monkeypatch):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get('Authorization'))
            if self.path == '/slow':
                time.sleep(0.15)
            self.send_response(404 if self.path == '/missing' else 200)
            self.end_headers()
            try:
                self.wfile.write(b'{"id":"public-fixture"}')
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('FORGE_TEST_HTTP_TOKEN', 'synthetic-token')
    spec = PluginSpec(str(EXAMPLES / 'internal_http'), trusted=True,
        config={'endpoint': f'http://127.0.0.1:{server.server_port}', 'credential_env': 'FORGE_TEST_HTTP_TOKEN', 'timeout': 0.05})
    agent = replace(AgentDefinition.core('internal-fixture'), plugins=(spec,))
    class Model:
        model = 'http-probe'
        calls = 0
        async def stream(self, **kwargs):
            if self.calls < 3:
                name = ['found', 'missing', 'slow'][self.calls]
                self.calls += 1
                yield ModelToolCallCompleted(ToolCall(0, name, 'internal_record', {'id': name}))
                yield ModelResponseCompleted('tool_use')
            else:
                yield ModelTextDelta('done')
                yield ModelResponseCompleted('end_turn')
        async def aclose(self):
            pass
    async def run():
        options = SessionOptions(agent=agent, data_root=tmp_path, config=ForgeConfig(api_key='unused', model_id='probe'))
        async with await HarnessSession.open(options, model_client_factory=lambda *a, **k: Model()) as session:
            from forge.runtime.state import ToolExecutionCompleted
            results = [e.result async for e in session.stream('query fixture') if isinstance(e, ToolExecutionCompleted)]
            assert json.loads(results[0].content) == {'id': 'public-fixture'}
            assert [r.error.code for r in results[1:]] == ['http_error', 'http_unavailable']
        assert session.cleanup['state'] == 'clean'
    try:
        asyncio.run(run())
        assert seen == ['Bearer synthetic-token'] * 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
