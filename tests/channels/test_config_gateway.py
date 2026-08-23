'''Channel configuration, routing, deduplication, and approval tests.'''

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from forge.channels.base import ChannelAdapter
from forge.channels.config import ChannelConfig, load_channel_settings
from forge.channels.gateway import (
    ApprovalBroker,
    ChannelGateway,
    DurableEventDeduplicator,
)
from forge.channels.models import ApprovalAction, InboundMessage, OutboundMessage
from forge.permissions.policy import PermissionRequest
from forge.runtime.state import ModelTextDelta, TokenUsage, TurnCompleted, TurnResult
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
        )
        await gateway.handle_message(message(message_id='same'))
        await gateway.handle_message(message(message_id='same'))
        await gateway.handle_message(message(message_id='new'))

        assert calls == 1
        assert runtime.prompts == ['hello', 'hello']
        assert [item.text for item in adapter.sent] == ['done', 'done']
        await gateway.close()

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
