"""Core compositions use the production loop without a workspace or native worker."""
import asyncio
import subprocess
import sys
import textwrap

import pytest

from forge.api import AgentDefinition, HarnessSession, SessionOptions, RunProfile
from forge.config import ForgeConfig
from forge.runtime.state import ModelTextDelta, ModelResponseCompleted


class Model:
    model = 'offline'
    provider = 'anthropic'
    closed = False

    async def stream(self, messages, tools=None, system=None):
        assert tools == []
        yield ModelTextDelta('answer')
        yield ModelResponseCompleted('end_turn')

    async def aclose(self):
        self.closed = True


def test_core_roundtrip_and_storage_scope(tmp_path):
    async def run():
        opts = SessionOptions(agent=AgentDefinition.core('notes'), data_root=tmp_path,
            config=ForgeConfig(api_key='fixture', model_id='offline'), profile=RunProfile(intent_routing=False))
        model = Model()
        async with await HarnessSession.open(opts, model_client_factory=lambda *a, **k: model) as session:
            assert session.backend is None
            assert session.conversation.registry.names == ()
            assert session.conversation.workspace_tracker is None
            assert (await session.run('hello')).text == 'answer'
            identity = session.session_id
        assert model.closed
        async with await HarnessSession.open(opts, model_client_factory=lambda *a, **k: Model(),
                                             resume_identifier=identity) as session:
            assert len(session.conversation.messages) == 2
            assert (await session.run('again')).model_calls == 1
        from dataclasses import replace
        from forge.sessions.store import SessionNotFoundError
        with pytest.raises(SessionNotFoundError):
            await HarnessSession.open(replace(opts, agent=AgentDefinition.core('other')),
                model_client_factory=lambda *a, **k: Model(), resume_identifier=identity)
    asyncio.run(run())


def test_core_import_and_turn_without_optional_modules(tmp_path):
    code = textwrap.dedent('''
        import sys, importlib.abc, asyncio
        from pathlib import Path
        class Reject(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, *args):
                if fullname.split('.')[0] in {'anthropic', 'openai', 'mcp', 'typer', 'rich', 'prompt_toolkit', 'win32api', 'httpx'}:
                    raise ImportError('Optional dependency loaded: ' + fullname)
                if fullname.startswith('forge.sandbox'):
                    raise ImportError('Native backend loaded: ' + fullname)
        sys.meta_path.insert(0, Reject())
        from forge.api import AgentDefinition, HarnessSession, SessionOptions
        from forge.config import ForgeConfig
        from forge.runtime.state import ModelTextDelta, ModelResponseCompleted
        class Model:
            model = 'fixture'
            async def stream(self, **kwargs):
                yield ModelTextDelta('ok')
                yield ModelResponseCompleted('end_turn')
        async def main():
            opts = SessionOptions(agent=AgentDefinition.core(), data_root=Path(sys.argv[1]),
                config=ForgeConfig(api_key='fixture', model_id='fixture'))
            async with await HarnessSession.open(opts, model_client_factory=lambda *a, **k: Model()) as session:
                assert (await session.run('hello')).text == 'ok'
        asyncio.run(main())
    ''')
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_core_cancellation_closes_client(tmp_path):
    class Waiting(Model):
        async def stream(self, **kwargs):
            await asyncio.Event().wait()
            yield ModelTextDelta('unreachable')
    async def run():
        client = Waiting()
        session = await HarnessSession.open(SessionOptions(agent=AgentDefinition.core(), data_root=tmp_path,
            config=ForgeConfig(api_key='fixture', model_id='fixture')), model_client_factory=lambda *a, **k: client)
        task = asyncio.create_task(session.run('wait'))
        await asyncio.sleep(0.05)
        await session.close()
        assert task.cancelled() and client.closed
        assert not session.running
    asyncio.run(run())


def test_tool_selection_applies_to_task_tools_too(tmp_path):
    from forge.runtime.factory import create_runtime
    from forge.runtime.dependencies import RuntimeBindings
    conversation, _, _ = create_runtime(tmp_path, agent=AgentDefinition(tools=('read_file',)),
        bindings=RuntimeBindings(config=ForgeConfig(api_key='fixture', model_id='fixture'),
            data_root=tmp_path / 'data', model_client_factory=lambda *a, **k: Model(),
            trusted_extensions=False, intent_routing=False))
    assert conversation.registry.names == ('read_file',)
    assert not conversation.finish_protocol
    asyncio.run(conversation.runtime_close())
