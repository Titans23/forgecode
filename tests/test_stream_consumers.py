"""CLI and channel consumers leave durable lifecycle ownership with the Harness."""
import asyncio
from collections import Counter
from contextlib import aclosing
import json
from types import SimpleNamespace

import pytest

from forge.channels.config import ChannelConfig
from forge.channels.gateway import ChannelGateway
from forge.channels.models import InboundMessage
from forge.cli import render_streamed_turn
from forge.permissions.policy import PermissionManager
from forge.runtime.agent_loop import Conversation
from forge.runtime.state import (
    ModelResponseCompleted, ModelTextDelta, ModelToolCallCompleted, ModelUsageUpdate,
    TokenUsage, ToolCall, ToolExecutionCompleted,
)
from forge.sessions.store import SessionStore
from forge.tools.base import ToolRegistry
from forge.tools.filesystem import ReadFileTool


class ScriptedModel:
    model = 'offline-consumer-fixture'
    max_tokens = 128
    context_window = None

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0
        self.waiting = asyncio.Event()

    async def stream(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield ModelToolCallCompleted(ToolCall(0, 'read-once', 'read_file', {'path': 'note.txt'}))
        else:
            self.waiting.set()
            if self.outcome == 'cancel':
                await asyncio.Event().wait()
            if self.outcome == 'error':
                raise RuntimeError('scripted provider failure')
            yield ModelTextDelta('Read the note.')
        yield ModelUsageUpdate(TokenUsage(10, 2))
        yield ModelResponseCompleted('tool_use' if self.calls == 1 else 'end_turn')


class MemoryAdapter:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)

    async def stop(self):
        pass


def recorded_types(journal):
    return Counter(json.loads(line)['type'] for line in journal.path.read_text(encoding='utf-8').splitlines())


def conversation_fixture(tmp_path, outcome):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'note.txt').write_text('Saved evidence.', encoding='utf-8')
    store = SessionStore(root, data_root=tmp_path / 'data')
    model = ScriptedModel(outcome)
    journal = store.create(model=model.model)
    conversation = Conversation(client=model, registry=ToolRegistry([ReadFileTool(root)]),
        context_root=root, session_store=store, session_journal=journal, include_task_tools=False,
        permission_manager=PermissionManager(root, journal=journal, load_stored_rules=False))
    return conversation, journal, store, model


@pytest.mark.parametrize('consumer', ['direct', 'cli', 'channel'])
@pytest.mark.parametrize('outcome', ['completed', 'error', 'cancel'])
def test_consumers_preserve_one_durable_lifecycle(tmp_path, consumer, outcome):
    async def run():
        conversation, journal, store, model = conversation_fixture(tmp_path, outcome)
        adapter = MemoryAdapter()
        gateway = ChannelGateway(adapter=adapter,
            config=ChannelConfig(platform='feishu', tenantId='tenant', allowedUsers=('user',)),
            runtime_factory=lambda _identifier: (conversation, journal, None),
            state_directory=tmp_path / 'channel')

        def ignore(*args, **kwargs):
            pass

        def tool_completed(*args):
            assert recorded_types(journal)['tool_completed'] == 1

        def turn_completed(*args):
            assert recorded_types(journal)['turn_completed'] == 1

        view = SimpleNamespace(append_text=ignore, update_usage=ignore, start_tool=ignore,
            complete_tool=tool_completed, complete=turn_completed, block_completion=ignore)

        async def consume():
            if consumer == 'cli':
                await render_streamed_turn(conversation, 'Read note.txt', view)
            elif consumer == 'channel':
                await gateway.handle_message(InboundMessage(platform='feishu', tenant_id='tenant',
                    message_id='message-1', sender_id='user', chat_id='chat', chat_type='p2p', text='Read note.txt'))
            else:
                async with aclosing(conversation.stream('Read note.txt')) as events:
                    async for event in events:
                        if isinstance(event, ToolExecutionCompleted):
                            tool_completed()

        task = asyncio.create_task(consume())
        try:
            if outcome == 'cancel':
                await asyncio.wait_for(model.waiting.wait(), timeout=10)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif outcome == 'error' and consumer != 'channel':
                with pytest.raises(RuntimeError, match='scripted provider failure'):
                    await task
            else:
                await task
            counts = recorded_types(journal)
            assert counts['tool_started'] == counts['tool_completed'] == 1
            assert counts['turn_completed'] == (outcome == 'completed')
            assert counts['turn_error'] == (outcome == 'error')
            assert counts['turn_cancelled'] == (outcome == 'cancel')
            assert not conversation._running
            restored = store.load(journal.session_id)
            assert not restored.indeterminate_tools
            assert any(message['role'] == 'user' and isinstance(message['content'], list)
                and any(block.get('tool_use_id') == 'read-once' for block in message['content'])
                for message in restored.messages)
            if consumer == 'channel' and outcome != 'cancel':
                assert len(adapter.sent) == 1
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await gateway.close()
            await conversation.runtime_close()
    asyncio.run(run())


def test_closing_consumer_after_tool_result_keeps_committed_facts(tmp_path):
    async def run():
        conversation, journal, store, model = conversation_fixture(tmp_path, 'completed')
        try:
            async with aclosing(conversation.stream('Read note.txt')) as events:
                async for event in events:
                    if isinstance(event, ToolExecutionCompleted):
                        assert recorded_types(journal)['tool_completed'] == 1
                        break
            counts = recorded_types(journal)
            assert counts['tool_started'] == counts['tool_completed'] == counts['turn_cancelled'] == 1
            assert counts['turn_completed'] == 0
            assert not store.load(journal.session_id).indeterminate_tools
            assert model.calls == 1
            assert not conversation._running
        finally:
            await conversation.runtime_close()
    asyncio.run(run())


def test_deepseek_text_reaches_terminal_before_stream_finishes(tmp_path):
    """Gate an actual SDK SSE stream after its first delta, with no network calls."""
    from io import StringIO
    import httpx
    from openai import AsyncOpenAI
    from rich.console import Console
    from forge.config import ForgeConfig
    from forge.runtime.providers import NativeModelClient
    from forge.terminal import TerminalUI
    async def run():
        first_consumed, release = asyncio.Event(), asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                def chunk(delta=None, finish=None, usage=None):
                    return ('data: ' + json.dumps({'id': 'offline', 'object': 'chat.completion.chunk',
                        'created': 1, 'model': 'offline', 'choices': [] if usage else [
                            {'index': 0, 'delta': delta or {}, 'finish_reason': finish}],
                        **({'usage': usage} if usage else {})}) + '\n\n').encode()
                yield chunk({'content': '第一段已到达'})
                first_consumed.set()
                await release.wait()
                yield chunk({'content': '，第二段随后到达。'})
                yield chunk(finish='stop')
                yield chunk(usage={'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12})
                yield b'data: [DONE]\n\n'
        async with AsyncOpenAI(api_key='offline', base_url='https://offline.invalid',
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Stream())))) as sdk:
            model = NativeModelClient(ForgeConfig(api_key='offline', model_id='offline', provider='deepseek'), client=sdk)
            conversation = Conversation(client=model, registry=ToolRegistry([]), context_root=tmp_path,
                                        include_task_tools=False)
            output = StringIO()
            terminal = TerminalUI(Console(file=output, force_terminal=False, width=100))
            try:
                with terminal.stream_response() as view:
                    task = asyncio.create_task(render_streamed_turn(conversation, 'hello', view))
                    try:
                        await asyncio.wait_for(first_consumed.wait(), 3)
                        assert not task.done()
                        assert '第一段已到达' in output.getvalue()
                        assert '第二段随后到达' not in output.getvalue()
                        release.set()
                        await asyncio.wait_for(task, 3)
                        assert output.getvalue().count('第一段已到达') == 1
                        assert output.getvalue().count('第二段随后到达') == 1
                    finally:
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
            finally:
                await conversation.runtime_close()
    asyncio.run(run())
