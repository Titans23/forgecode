'''Channel configuration, routing, deduplication, and approval tests.'''

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from forge.channels import feishu as feishu_module
from forge.channels.base import ChannelAdapter
from forge.channels.config import ChannelConfig, load_channel_settings
from forge.channels.gateway import (
    ApprovalBroker,
    ChannelGateway,
    DurableEventDeduplicator,
)
from forge.channels.models import ApprovalAction, InboundMessage, OutboundMessage
from forge.permissions.policy import PermissionRequest
from forge.runtime.state import (
    ModelCallCompleted,
    ModelCallStarted,
    ModelTextDelta,
    TokenUsage,
    ToolCall,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
    TurnResult,
)
from forge.tools.base import ToolResult
from forge.channels.feishu import FeishuChannelAdapter


class FakeAdapter(ChannelAdapter):
    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []
        self.approvals: list[dict[str, object]] = []

    async def start(self, on_message, on_approval) -> None:
        self.on_message = on_message
        self.on_approval = on_approval

    async def stop(self) -> None:
        return None

    async def send(self, message: OutboundMessage) -> None:
        self.sent.append(message)

    async def request_approval(self, **kwargs) -> None:
        self.approvals.append(kwargs)


class FakePermissionManager:
    approval_handler = None


class FakeRuntime:
    def __init__(self) -> None:
        self.permission_manager = FakePermissionManager()
        self.prompts: list[str] = []
        self.events: list[object] = []

    async def session_start(self, *, source: str) -> None:
        self.source = source

    async def stream(self, prompt: str):
        self.prompts.append(prompt)
        tool_call = ToolCall(
            0,
            'call-1',
            'read_file',
            {'path': 'README.md', 'api_key': 'secret-value'},
        )
        yield ModelCallStarted(iteration=1)
        yield ToolExecutionStarted(tool_call)
        yield ToolExecutionCompleted(
            tool_call,
            ToolResult.ok('read', content='README contents'),
        )
        yield ModelCallCompleted(iteration=1)
        yield ModelTextDelta(text='ok')
        yield TurnCompleted(
            result=TurnResult(
                text='done',
                usage=TokenUsage(input_tokens=1, output_tokens=1),
            )
        )

    def record_session_event(self, event) -> None:
        self.events.append(event)

    async def runtime_close(self, *, reason: str) -> None:
        self.closed = reason


def message(*, message_id: str = 'm1', mentioned: bool = True) -> InboundMessage:
    return InboundMessage(
        platform='feishu',
        tenant_id='tenant',
        message_id=message_id,
        sender_id='user-1',
        chat_id='chat-1',
        chat_type='group',
        text='hello',
        mentioned_bot=mentioned,
    )


def test_topics_are_isolated_in_private_and_group_chats() -> None:
    private_root = InboundMessage(
        platform='feishu',
        tenant_id='tenant',
        message_id='m1',
        sender_id='user-1',
        chat_id='chat-1',
        chat_type='p2p',
        text='root',
    )
    private_topic = InboundMessage(
        platform='feishu',
        tenant_id='tenant',
        message_id='m2',
        sender_id='user-1',
        chat_id='chat-1',
        chat_type='p2p',
        thread_id='topic-1',
        text='topic',
    )
    private_topic_reply = InboundMessage(
        platform='feishu',
        tenant_id='tenant',
        message_id='m3',
        sender_id='user-1',
        chat_id='chat-1',
        chat_type='p2p',
        thread_id='topic-1',
        text='follow-up',
    )
    private_other_topic = InboundMessage(
        platform='feishu',
        tenant_id='tenant',
        message_id='m4',
        sender_id='user-1',
        chat_id='chat-1',
        chat_type='p2p',
        thread_id='topic-2',
        text='other topic',
    )
    group_topic = InboundMessage(
        platform='feishu',
        tenant_id='tenant',
        message_id='m5',
        sender_id='user-1',
        chat_id='chat-1',
        chat_type='group',
        thread_id='topic-1',
        text='group topic',
    )

    assert private_root.session_key != private_topic.session_key
    assert private_topic.session_key == private_topic_reply.session_key
    assert private_topic.session_key != private_other_topic.session_key
    assert private_topic.session_key != group_topic.session_key


def test_channel_configuration_merges_and_uses_environment_names(
    tmp_path: Path,
    monkeypatch,
) -> None:
    user = tmp_path / 'user.json'
    project_dir = tmp_path / '.forge'
    project_dir.mkdir()
    user.write_text(
        json.dumps({'channels': {'main': {'platform': 'qq'}}}),
        encoding='utf-8',
    )
    (project_dir / 'channels.json').write_text(
        json.dumps(
            {
                'channels': {
                    'main': {
                        'platform': 'feishu',
                        'tenantId': 'tenant',
                        'appIdEnv': 'FEISHU_ID',
                        'appSecretEnv': 'FEISHU_SECRET',
                        'allowedUsers': ['user-1'],
                    }
                }
            }
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('FEISHU_ID', 'id')
    monkeypatch.setenv('FEISHU_SECRET', 'secret')

    config = load_channel_settings(tmp_path, user_path=user).channels['main']

    assert config.platform == 'feishu'
    assert config.credential_status() == (True, ())


def test_channel_credentials_load_from_project_dotenv(tmp_path: Path) -> None:
    dotenv_path = tmp_path / '.env'
    dotenv_path.write_text(
        'FEISHU_DOTENV_ID=dotenv-app\n'
        'FEISHU_DOTENV_SECRET=dotenv-secret\n',
        encoding='utf-8',
    )
    previous = {
        name: os.environ.get(name)
        for name in ('FEISHU_DOTENV_ID', 'FEISHU_DOTENV_SECRET')
    }
    os.environ.pop('FEISHU_DOTENV_ID', None)
    os.environ.pop('FEISHU_DOTENV_SECRET', None)
    try:
        config_path = tmp_path / '.forge'
        config_path.mkdir()
        (config_path / 'channels.json').write_text(
            json.dumps(
                {
                    'channels': {
                        'dotenv': {
                            'platform': 'feishu',
                            'appIdEnv': 'FEISHU_DOTENV_ID',
                            'appSecretEnv': 'FEISHU_DOTENV_SECRET',
                            'allowedUsers': ['user-1'],
                        }
                    }
                }
            ),
            encoding='utf-8',
        )

        config = load_channel_settings(tmp_path).channels['dotenv']

        assert config.credential_status() == (True, ())
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_feishu_channel_can_be_configured_entirely_from_project_dotenv(
    tmp_path: Path,
) -> None:
    dotenv_path = tmp_path / '.env'
    dotenv_path.write_text(
        'FEISHU_APP_ID=dotenv-app\n'
        'FEISHU_APP_SECRET=dotenv-secret\n'
        'FEISHU_TENANT_ID=tenant-from-dotenv\n'
        'FEISHU_ALLOWED_USERS=user-1,user-2\n'
        'FEISHU_ALLOWED_CHATS=chat-1\n'
        'FEISHU_REQUIRE_MENTION=false\n'
        'FEISHU_APPROVAL_TIMEOUT_SECONDS=300\n',
        encoding='utf-8',
    )
    names = (
        'FEISHU_APP_ID',
        'FEISHU_APP_SECRET',
        'FEISHU_TENANT_ID',
        'FEISHU_ALLOWED_USERS',
        'FEISHU_ALLOWED_CHATS',
        'FEISHU_REQUIRE_MENTION',
        'FEISHU_APPROVAL_TIMEOUT_SECONDS',
    )
    previous = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ.pop(name, None)
    try:
        config = load_channel_settings(tmp_path).channels['feishu-main']

        assert config.platform == 'feishu'
        assert config.tenant_id == 'tenant-from-dotenv'
        assert config.allowed_users == ('user-1', 'user-2')
        assert config.allowed_chats == ('chat-1',)
        assert config.require_mention is False
        assert config.approval_timeout_seconds == 300
        assert config.credential_status() == (True, ())
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_allowlist_and_group_mention_are_enforced() -> None:
    config = ChannelConfig(
        platform='feishu',
        tenantId='tenant',
        allowedUsers=('user-1',),
        allowedChats=('chat-1',),
    )

    assert config.accepts(message())
    assert not config.accepts(message(mentioned=False))


def test_enabled_channel_rejects_an_empty_allowlist() -> None:
    with pytest.raises(ValueError, match='allowedUsers or allowedChats'):
        ChannelConfig(platform='feishu', tenantId='tenant')


def test_gateway_deduplicates_and_reuses_one_runtime(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = FakeAdapter()
        runtime = FakeRuntime()
        progress: list[str] = []
        calls = 0

        def factory(identifier):
            nonlocal calls
            calls += 1
            return runtime, SimpleNamespace(session_id='session-1'), None

        gateway = ChannelGateway(
            adapter=adapter,
            config=ChannelConfig(
                platform='feishu',
                tenantId='tenant',
                allowedUsers=('user-1',),
            ),
            runtime_factory=factory,
            state_directory=tmp_path,
            progress=progress.append,
        )
        await gateway.handle_message(message(message_id='same'))
        await gateway.handle_message(message(message_id='same'))
        await gateway.handle_message(message(message_id='new'))

        assert calls == 1
        assert runtime.prompts == ['hello', 'hello']
        assert [item.text for item in adapter.sent] == ['done', 'done']
        assert any(line.startswith('[Feishu] received') for line in progress)
        assert any(line.startswith('[Session] created') for line in progress)
        assert '[Model] call started iteration=1' in progress
        logs = '\n'.join(progress)
        assert 'text=hello' in logs
        assert '[Tool] started name=read_file id=call-1' in logs
        assert 'arguments={"path": "README.md", "api_key": "***"}' in logs
        assert 'summary=read content=README contents' in logs
        assert '[Model] call completed iteration=1' in logs
        assert '[Turn] completed status=completed model_calls=1 tokens=2' in logs
        assert 'secret-value' not in logs
        assert any('ignored duplicate' in line for line in progress)
        assert any('text=done' in line for line in progress)
        await gateway.close()
        assert progress[-1] == '[Gateway] stopped'

    asyncio.run(run())


def test_gateway_deduplication_survives_restart(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = FakeAdapter()
        runtimes: list[FakeRuntime] = []

        def factory(identifier):
            runtime = FakeRuntime()
            runtimes.append(runtime)
            return runtime, SimpleNamespace(session_id='session-1'), None

        config = ChannelConfig(
            platform='feishu',
            tenantId='tenant',
            allowedUsers=('user-1',),
        )
        first = ChannelGateway(
            adapter=adapter,
            config=config,
            runtime_factory=factory,
            state_directory=tmp_path,
        )
        await first.handle_message(message(message_id='persistent'))
        await first.close()
        second = ChannelGateway(
            adapter=adapter,
            config=config,
            runtime_factory=factory,
            state_directory=tmp_path,
        )
        await second.handle_message(message(message_id='persistent'))

        assert len(runtimes) == 1

    asyncio.run(run())


def test_dedup_compaction_retains_the_most_recent_events(tmp_path: Path) -> None:
    path = tmp_path / 'events.log'
    dedup = DurableEventDeduplicator(path, maximum_entries=2)

    assert dedup.reserve(message(message_id='old'))
    assert dedup.reserve(message(message_id='recent-1'))
    assert dedup.reserve(message(message_id='recent-2'))
    restarted = DurableEventDeduplicator(path, maximum_entries=2)

    assert not restarted.reserve(message(message_id='recent-1'))
    assert not restarted.reserve(message(message_id='recent-2'))
    assert restarted.reserve(message(message_id='old'))


def test_approval_is_single_user_exact_hash_and_single_use() -> None:
    async def run() -> None:
        broker = ApprovalBroker()
        adapter = FakeAdapter()
        request = PermissionRequest(
            'send',
            'mcp.write',
            'high',
            ('chat-1',),
            preview='hello',
            arguments_hash='a' * 64,
        )
        task = asyncio.create_task(
            broker.request(adapter, message(), request, timeout_seconds=1)
        )
        await asyncio.sleep(0)
        approval = adapter.approvals[0]
        approval_id = str(approval['approval_id'])

        assert not await broker.resolve(
            ApprovalAction(approval_id, 'other-user', 'approve', 'a' * 64)
        )
        assert not await broker.resolve(
            ApprovalAction(approval_id, 'user-1', 'approve', 'b' * 64)
        )
        assert await broker.resolve(
            ApprovalAction(approval_id, 'user-1', 'approve', 'a' * 64)
        )
        assert (await task).choice == 'allow_once'
        assert not await broker.resolve(
            ApprovalAction(approval_id, 'user-1', 'approve', 'a' * 64)
        )

    asyncio.run(run())


def test_approval_times_out_without_execution() -> None:
    async def run() -> None:
        broker = ApprovalBroker()
        response = await broker.request(
            FakeAdapter(),
            message(),
            PermissionRequest(
                'send',
                'mcp.write',
                'high',
                arguments_hash='a' * 64,
            ),
            timeout_seconds=0.01,
        )

        assert response.choice == 'deny'
        assert response.reason == 'Chat approval timed out.'

    asyncio.run(run())


def test_feishu_adapter_normalizes_message_and_card_action() -> None:
    adapter = FeishuChannelAdapter(
        ChannelConfig(
            platform='feishu',
            tenantId='tenant',
            allowedUsers=('u1',),
        ),
        channel=object(),
    )
    normalized = adapter._normalize_message(
        SimpleNamespace(
            message_id='m1',
            sender_id='u1',
            chat_id='c1',
            chat_type='group',
            content_text='hello',
            body_text='hello',
            conversation=SimpleNamespace(thread_id='thread-1'),
            mentioned_bot=True,
            resources=(),
            raw={},
        )
    )
    action = adapter._normalize_approval(
        SimpleNamespace(
            operator_id='u1',
            raw={
                'action': {
                    'value': {
                        'forge_approval_id': 'approval',
                        'forge_arguments_hash': 'hash',
                        'forge_decision': 'approve',
                    }
                }
            },
        )
    )

    assert normalized.text == 'hello'
    assert normalized.mentioned_bot
    assert normalized.thread_id == 'thread-1'
    assert action == ApprovalAction('approval', 'u1', 'approve', 'hash')


def test_feishu_adapter_uses_root_message_as_topic_context() -> None:
    adapter = FeishuChannelAdapter(
        ChannelConfig(
            platform='feishu',
            tenantId='tenant',
            allowedUsers=('u1',),
        ),
        channel=object(),
    )

    def normalize(message_id: str, raw: dict[str, object]) -> InboundMessage:
        return adapter._normalize_message(
            SimpleNamespace(
                message_id=message_id,
                sender_id='u1',
                chat_id='c1',
                chat_type='p2p',
                content_text='hello',
                body_text='hello',
                conversation=SimpleNamespace(thread_id=None),
                mentioned_bot=False,
                resources=(),
                raw=raw,
            )
        )

    root = normalize('root-message', {})
    reply = normalize('reply-message', {'root_id': 'root-message'})
    other_root = normalize('other-root', {})

    assert root.thread_id == 'root-message'
    assert reply.thread_id == root.thread_id
    assert other_root.thread_id == 'other-root'
    assert root.session_key == reply.session_key
    assert root.session_key != other_root.session_key


def test_feishu_sdk_isolates_its_loop_from_running_gateway_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('APP_ID', 'test-app')
    monkeypatch.setenv('APP_SECRET', 'test-secret')

    async def run() -> None:
        from lark_channel.ws import client as ws_client

        original_loop = ws_client.loop
        ws_client.loop = asyncio.get_running_loop()
        replacement_loop = None
        try:
            adapter = FeishuChannelAdapter(
                ChannelConfig(
                    platform='feishu',
                    tenantId='tenant',
                    allowedUsers=('u1',),
                )
            )
            adapter._build_channel()
            replacement_loop = ws_client.loop

            assert replacement_loop is not asyncio.get_running_loop()
            assert replacement_loop is not original_loop
        finally:
            ws_client.loop = original_loop
            if (
                replacement_loop is not None
                and replacement_loop is not original_loop
                and not replacement_loop.is_closed()
            ):
                replacement_loop.close()

    asyncio.run(run())


def test_feishu_sdk_receives_the_same_allowlist_policy(monkeypatch) -> None:
    monkeypatch.setenv('APP_ID', 'test-app')
    monkeypatch.setenv('APP_SECRET', 'test-secret')
    adapter = FeishuChannelAdapter(
        ChannelConfig(
            platform='feishu',
            tenantId='tenant',
            allowedUsers=('u1',),
            allowedChats=('c1',),
            requireMention=True,
        )
    )

    channel = adapter._build_channel()
    policy = channel._config.policy

    assert policy.dm_policy == 'allowlist'
    assert policy.group_policy == 'allowlist'
    assert policy.allow_from == ['u1']
    assert policy.group_allowlist == ['c1']
    assert channel._config.security.is_strict
    assert channel._config.security.strict_content_text
    assert channel._config.log_level.name == 'WARNING'


def test_feishu_adapter_daemon_lifecycle_stops_cleanly() -> None:
    class FakeChannel:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.stopped = False
            self.handlers: dict[str, object] = {}

        def on(self, name: str, handler) -> None:
            self.handlers[name] = handler

        def start(self) -> None:
            self.started.set()
            self.release.wait(timeout=1)
            raise RuntimeError('event loop stopped during intentional shutdown')

        def stop(self, *, join_timeout: float) -> None:
            assert join_timeout == 0.5
            assert threading.current_thread().daemon
            assert threading.current_thread().name == 'forge-feishu-stop'
            self.stopped = True
            self.release.set()

    async def run() -> None:
        channel = FakeChannel()
        adapter = FeishuChannelAdapter(
            ChannelConfig(
                platform='feishu',
                tenantId='tenant',
                allowedUsers=('u1',),
            ),
            channel=channel,
        )

        task = asyncio.create_task(
            adapter.start(lambda _value: None, lambda _value: None)
        )
        assert await asyncio.to_thread(channel.started.wait, 1)

        await adapter.stop()
        await asyncio.wait_for(task, timeout=1)
        assert channel.stopped
        assert adapter._worker is None

    asyncio.run(run())


def test_feishu_adapter_stop_is_bounded_when_sdk_stop_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeChannel:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.stop_started = threading.Event()
            self.release = threading.Event()

        def on(self, _name: str, _handler) -> None:
            return None

        def start(self) -> None:
            self.started.set()
            self.release.wait(timeout=1)

        def stop(self, *, join_timeout: float) -> None:
            assert join_timeout == 0.5
            self.stop_started.set()
            self.release.wait(timeout=1)

    async def run() -> None:
        monkeypatch.setattr(feishu_module, '_SDK_STOP_TIMEOUT_SECONDS', 0.05)
        channel = FakeChannel()
        adapter = FeishuChannelAdapter(
            ChannelConfig(
                platform='feishu',
                tenantId='tenant',
                allowedUsers=('u1',),
            ),
            channel=channel,
        )
        task = asyncio.create_task(
            adapter.start(lambda _value: None, lambda _value: None)
        )
        assert await asyncio.to_thread(channel.started.wait, 1)

        await asyncio.wait_for(adapter.stop(), timeout=0.5)
        assert channel.stop_started.is_set()
        channel.release.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(run())
