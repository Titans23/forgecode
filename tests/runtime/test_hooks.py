'''Integration tests for Hook boundaries in the Agent Loop.'''

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock

from pydantic import Field

from forge.hooks.manager import HookManager
from forge.hooks.models import HookEvent, HookOutcome, HookSettings
from forge.runtime.agent_loop import Conversation
from forge.runtime.state import (
    ConversationEvent,
    ModelStreamEvent,
    ModelTextDelta,
    ModelToolCallCompleted,
    ModelUsageUpdate,
    TokenUsage,
    ToolCall,
    ToolExecutionCompleted,
    TurnCompleted,
)
from forge.sessions.store import SessionStore
from forge.tools.base import Tool, ToolInput, ToolRegistry, ToolResult


class FakeClient:
    provider = 'fake'

    def __init__(self, *responses: list[ModelStreamEvent]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.calls.append(
            {'messages': messages, 'tools': tools, 'system': system}
        )
        for event in self.responses.pop(0):
            yield event


def tool_response(call: ToolCall) -> list[ModelStreamEvent]:
    return [
        ModelUsageUpdate(usage=TokenUsage(10, 0)),
        ModelToolCallCompleted(tool_call=call),
        ModelUsageUpdate(usage=TokenUsage(10, 3)),
    ]


def text_response(text: str) -> list[ModelStreamEvent]:
    return [
        ModelUsageUpdate(usage=TokenUsage(10, 0)),
        ModelTextDelta(text=text),
        ModelUsageUpdate(usage=TokenUsage(10, 2)),
    ]


def collect(conversation: Conversation) -> list[ConversationEvent]:
    async def run() -> list[ConversationEvent]:
        return [event async for event in conversation.stream('do it')]

    return asyncio.run(run())


class WriteInput(ToolInput):
    path: str = Field(min_length=1)
    content: str


class RecordingWriteTool(Tool[WriteInput]):
    name = 'test_write'
    description = 'Write a file for Hook integration tests.'
    input_model = WriteInput
    effect = 'workspace_write'

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.calls: list[WriteInput] = []

    async def execute(self, arguments: WriteInput) -> ToolResult:
        self.calls.append(arguments)
        (self.root / arguments.path).write_text(
            arguments.content, encoding='utf-8'
        )
        return ToolResult.ok('Wrote file.')


class VerifyInput(ToolInput):
    command: str


class RecordingVerifyTool(Tool[VerifyInput]):
    name = 'verify'
    description = 'Return deterministic verification evidence.'
    input_model = VerifyInput

    async def execute(self, arguments: VerifyInput) -> ToolResult:
        return ToolResult.ok(
            'Verified.',
            metadata={
                'verification': True,
                'command': arguments.command,
                'cwd': '.',
                'exit_code': 0,
                'duration_seconds': 0.1,
                'timed_out': False,
                'workspace_revision': 0,
            },
        )


class RecordingHooks:
    def __init__(self) -> None:
        self.events: list[HookEvent] = []

    async def emit(self, event: HookEvent) -> HookOutcome:
        self.events.append(event)
        if event.name == 'PreToolUse' and event.tool_name == 'test_write':
            return HookOutcome(
                arguments={
                    'path': 'changed.py',
                    'content': 'changed by hook',
                }
            )
        if event.name == 'PostToolUse':
            return HookOutcome(
                arguments=event.arguments,
                additional_context=('tool policy context',),
            )
        return HookOutcome(arguments=event.arguments)


def test_tool_and_file_hooks_wrap_actual_mutation(tmp_path: Path) -> None:
    call = ToolCall(
        index=0,
        id='tool-write',
        name='test_write',
        arguments={'path': 'original.py', 'content': 'original'},
    )
    client = FakeClient(tool_response(call), text_response('done'))
    tool = RecordingWriteTool(tmp_path)
    hooks = RecordingHooks()
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([tool]),
        hook_manager=hooks,  # type: ignore[arg-type]
    )

    events = collect(conversation)

    assert not (tmp_path / 'original.py').exists()
    assert (tmp_path / 'changed.py').read_text(
        encoding='utf-8'
    ) == 'changed by hook'
    names = [event.name for event in hooks.events]
    assert names == [
        'SessionStart',
        'BeforeModelCall',
        'PreToolUse',
        'BeforeFileEdit',
        'AfterFileEdit',
        'PostToolUse',
        'BeforeModelCall',
    ]
    assert 'tool policy context' in str(client.calls[1]['system'])
    completed = [
        event
        for event in events
        if isinstance(event, ToolExecutionCompleted)
    ]
    assert completed[0].tool_call.arguments['path'] == 'changed.py'


def test_before_model_call_can_stop_request() -> None:
    class DenyModelHook:
        async def emit(self, event: HookEvent) -> HookOutcome:
            if event.name == 'BeforeModelCall':
                return HookOutcome(
                    allowed=False,
                    reason='maintenance window',
                )
            return HookOutcome(arguments=event.arguments)

    client = FakeClient(text_response('must not run'))
    conversation = Conversation(
        client=client,
        hook_manager=DenyModelHook(),  # type: ignore[arg-type]
    )

    events = collect(conversation)

    assert client.calls == []
    result = next(
        event.result
        for event in events
        if isinstance(event, TurnCompleted)
    )
    assert result.status == 'failed'
    assert 'maintenance window' in result.text


def test_after_verification_receives_structured_evidence(
    tmp_path: Path,
) -> None:
    call = ToolCall(
        index=0,
        id='tool-verify',
        name='verify',
        arguments={'command': 'pytest -q'},
    )
    client = FakeClient(tool_response(call), text_response('verified'))
    hooks = RecordingHooks()
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([RecordingVerifyTool(tmp_path)]),
        hook_manager=hooks,  # type: ignore[arg-type]
    )

    collect(conversation)

    event = next(
        item for item in hooks.events if item.name == 'AfterVerification'
    )
    assert event.payload['success'] is True
    assert event.payload['command'] == 'pytest -q'


def test_before_compact_can_skip_automatic_compaction() -> None:
    class DenyCompactHook:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def emit(self, event: HookEvent) -> HookOutcome:
            self.events.append(event.name)
            if event.name == 'BeforeCompact':
                return HookOutcome(
                    allowed=False,
                    reason='keep transcript intact',
                )
            return HookOutcome(arguments=event.arguments)

    hooks = DenyCompactHook()
    client = FakeClient(text_response('done'))
    conversation = Conversation(
        client=client,
        hook_manager=hooks,  # type: ignore[arg-type]
    )
    conversation.context.compaction_required = (
        lambda *_args, **_kwargs: True
    )
    compact = AsyncMock(return_value=None)
    conversation.context.compact_history = compact

    collect(conversation)

    assert 'BeforeCompact' in hooks.events
    compact.assert_not_awaited()
    assert len(client.calls) == 1


def test_session_hooks_are_persisted_as_sanitized_audit_events(
    tmp_path: Path,
) -> None:
    settings = HookSettings.model_validate(
        {
            'hooks': {
                'SessionStart': [
                    {
                        'id': 'start-audit',
                        'command': [sys.executable, '-c', 'pass'],
                    }
                ],
                'SessionEnd': [
                    {
                        'id': 'end-audit',
                        'command': [sys.executable, '-c', 'pass'],
                    }
                ],
            }
        }
    )
    store = SessionStore(tmp_path, data_root=tmp_path / 'data')
    journal = store.create(model='fake')
    conversation = Conversation(
        client=FakeClient(text_response('unused')),
        session_journal=journal,
        session_store=store,
        hook_manager=HookManager(tmp_path, settings),
    )

    async def lifecycle() -> None:
        await conversation.session_start(source='test')
        await conversation.session_end(reason='test')

    asyncio.run(lifecycle())
    records = [
        json.loads(line)
        for line in journal.path.read_text(encoding='utf-8').splitlines()
    ]
    audits = [
        item for item in records if item['type'] == 'hook_execution'
    ]

    assert [
        item['payload']['execution']['hook_id'] for item in audits
    ] == ['start-audit', 'end-audit']
    assert all(
        item['payload']['execution']['decision'] == 'allow'
        for item in audits
    )
