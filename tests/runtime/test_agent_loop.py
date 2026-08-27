'''Tests for the minimal M1 streaming conversation runtime.'''

import asyncio
from collections.abc import AsyncIterator
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest
from pydantic import Field
from unittest.mock import AsyncMock

from forge.context.compactor import CompactionConfig
from forge.permissions.approval import StaticApprovalHandler
from forge.permissions.policy import (
    ApprovalResponse,
    PermissionManager,
    PermissionRequest,
)
from forge.permissions.risk import classify_tool_call
from forge.runtime.agent_loop import (
    Conversation,
    ModelResponseError,
    is_tool_protocol_failure,
    load_system_prompt,
)
from forge.runtime.completion import TaskPolicy
from forge.runtime.model_client import (
    ModelOutputTruncatedError,
    ModelProtocolError,
)
from forge.runtime.router import RouteResult, TurnDecision
from forge.runtime.state import (
    ConversationEvent,
    ModelCallCompleted,
    ModelCallStarted,
    ModelStreamEvent,
    ModelTextDelta,
    ModelToolCallArgumentsDelta,
    ModelToolCallCompleted,
    ModelToolCallStarted,
    ModelUsageUpdate,
    TokenUsage,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
    TurnResult,
    ToolCall,
)
from forge.tools import create_default_registry
from forge.tools.base import Tool, ToolInput, ToolRegistry, ToolResult
from forge.tools.finish import FinishTaskTool
from forge.tools.shell import RunCommandTool
from forge.runtime.workspace import WorkspaceTracker
from forge.tools.filesystem import ReadFileTool
from forge.tools.search import GrepTool
from forge.tasks.state import ActiveTask


class FakeModelClient:
    '''Record requests and emit deterministic model stream events.'''

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
            if isinstance(event, Exception):
                raise event
            yield event


class StaticIntentRouter:
    def __init__(self, *decisions: TurnDecision) -> None:
        self.decisions = list(decisions)
        self.calls: list[str] = []

    async def route(
        self,
        prompt: str,
        active_task: ActiveTask | None,
        recent_messages: list[dict[str, object]],
    ) -> RouteResult:
        self.calls.append(prompt)
        decision = (
            self.decisions.pop(0)
            if len(self.decisions) > 1
            else self.decisions[0]
        )
        return RouteResult(
            decision=decision,
            usage=TokenUsage(input_tokens=7, output_tokens=3),
        )


def routed(
    intent: str,
    *,
    relation: str = 'none',
    requires_change: bool = False,
) -> TurnDecision:
    return TurnDecision(
        intent=intent,
        task_relation=relation,
        requires_workspace_change=requires_change,
        confidence=0.99,
        reason='test decision',
    )


def streamed_response(
    *text_parts: str,
    input_tokens: int = 10,
    output_tokens: int = 2,
) -> list[ModelStreamEvent]:
    return [
        ModelUsageUpdate(
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=0,
            )
        ),
        *(ModelTextDelta(text=part) for part in text_parts),
        ModelUsageUpdate(
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        ),
    ]


def collect_turn(
    conversation: Conversation,
    prompt: str,
) -> list[ConversationEvent]:
    async def collect() -> list[ConversationEvent]:
        return [event async for event in conversation.stream(prompt)]

    return asyncio.run(collect())


class ReadFileInput(ToolInput):
    path: str = Field(min_length=1)


class RecordingReadFileTool(Tool[ReadFileInput]):
    name = 'read_file'
    description = 'Read a test file.'
    input_model = ReadFileInput

    def __init__(
        self,
        root: Path,
        result: ToolResult | None = None,
    ) -> None:
        super().__init__(root)
        self.calls: list[str] = []
        self.result = result or ToolResult.ok(
            'Read file.',
            content='file contents',
        )

    async def execute(self, arguments: ReadFileInput) -> ToolResult:
        self.calls.append(arguments.path)
        return self.result


class NoOpWriteTool(RecordingReadFileTool):
    name = 'no_op_write'
    description = 'Pretend to write without changing the workspace.'
    effect = 'workspace_write'


class NoOpProcessTool(RecordingReadFileTool):
    name = 'run_process'
    description = 'Pretend to execute a process.'
    effect = 'process'


class TinyWriteInput(ToolInput):
    path: str = Field(min_length=1)
    content: str = Field(max_length=3)


class TinyWriteTool(Tool[TinyWriteInput]):
    name = 'tiny_write'
    description = 'Test-only size-limited write tool.'
    input_model = TinyWriteInput
    effect = 'workspace_write'

    async def execute(self, arguments: TinyWriteInput) -> ToolResult:
        return ToolResult.ok('Wrote test content.')


class FailingWriteTool(NoOpWriteTool):
    name = 'failing_write'
    description = 'Reject a test write with an actionable diagnostic.'

    async def execute(self, arguments: ReadFileInput) -> ToolResult:
        self.calls.append(arguments.path)
        return ToolResult.fail(
            'patch_rejected',
            'Patch validation failed.',
            content='error: target context did not match',
        )


class NoChangeWorkspaceTracker:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.revision = 0
        self.changed_paths: tuple[str, ...] = ()

    async def begin_turn(self) -> None:
        self.revision = 0

    def watch_paths(self, paths: tuple[str, ...]) -> None:
        pass

    async def refresh(self):
        return None


def tool_response(
    *tool_calls: ToolCall,
    input_tokens: int = 15,
    output_tokens: int = 10,
) -> list[ModelStreamEvent]:
    events: list[ModelStreamEvent] = [
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=0)
        )
    ]
    for tool_call in tool_calls:
        events.extend(
            [
                ModelToolCallStarted(
                    index=tool_call.index,
                    id=tool_call.id,
                    name=tool_call.name,
                ),
                ModelToolCallArgumentsDelta(
                    index=tool_call.index,
                    partial_json=json.dumps(tool_call.arguments),
                ),
                ModelToolCallCompleted(tool_call=tool_call),
            ]
        )
    events.append(
        ModelUsageUpdate(
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
    )
    return events


def test_permission_modes_and_hard_denies(tmp_path: Path) -> None:
    write = PermissionRequest(
        'write_file',
        'file.write',
        'low',
        ('app.py',),
    )
    read = PermissionRequest(
        'read_file',
        'file.read',
        'low',
        ('app.py',),
    )
    secret = PermissionRequest(
        'read_file',
        'file.read',
        'critical',
        ('.env',),
        'Credential files are protected.',
        hard_deny=True,
    )
    manager = PermissionManager(tmp_path, mode='plan')

    assert asyncio.run(manager.authorize(read)).action == 'allow'
    assert asyncio.run(manager.authorize(write)).action == 'deny'
    assert asyncio.run(manager.authorize(secret)).source == 'hard_deny'


def test_absolute_and_git_paths_are_not_hard_denied() -> None:
    absolute = classify_tool_call(
        ToolCall(
            index=0,
            id='read-absolute',
            name='read_file',
            arguments={'path': 'D:/shared/example.txt'},
        ),
        'read_only',
    )
    git_path = classify_tool_call(
        ToolCall(
            index=0,
            id='read-git',
            name='read_file',
            arguments={'path': '.git/config'},
        ),
        'read_only',
    )
    env_path = classify_tool_call(
        ToolCall(
            index=0,
            id='read-env',
            name='read_file',
            arguments={'path': '.env'},
        ),
        'read_only',
    )

    assert absolute.hard_deny is False
    assert git_path.hard_deny is False
    assert env_path.hard_deny is True


def test_python_stdin_delete_requires_high_risk_approval() -> None:
    request = classify_tool_call(
        ToolCall(
            index=0,
            id='python-delete',
            name='run_command',
            arguments={
                'command': 'python -',
                'stdin': "import os\nos.remove('play/a.txt')",
            },
        ),
        'process',
    )

    assert request.capability == 'file.delete'
    assert request.risk == 'high'
    assert "os.remove('play/a.txt')" in request.preview


def test_delete_approval_is_never_persisted_as_wildcard(
    tmp_path: Path,
) -> None:
    manager = PermissionManager(
        tmp_path,
        mode='supervised',
        approval_handler=StaticApprovalHandler('allow_project'),
        user_path=tmp_path / 'user-permissions.json',
    )
    request = PermissionRequest(
        'apply_patch',
        'file.delete',
        'high',
        ('play/a.txt', 'play/b.txt'),
    )

    decision = asyncio.run(manager.authorize(request))

    assert decision.action == 'allow'
    assert manager.project_rules == []
    assert not manager.project_path.exists()


def _init_test_repository(root: Path) -> None:
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    subprocess.run(
        ['git', 'config', 'user.email', 'forge-tests@example.invalid'],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ['git', 'config', 'user.name', 'Forge Tests'],
        cwd=root,
        check=True,
    )
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(
        ['git', 'commit', '-qm', 'baseline'],
        cwd=root,
        check=True,
    )


class RecordingApprovalHandler:
    def __init__(self) -> None:
        self.requests: list[PermissionRequest] = []

    async def __call__(self, request: PermissionRequest) -> ApprovalResponse:
        self.requests.append(request)
        return ApprovalResponse('allow_once')


def test_identical_successful_delete_is_not_prompted_or_executed_twice(
    tmp_path: Path,
) -> None:
    play = tmp_path / 'play'
    play.mkdir()
    (play / '.touch').write_text('x', encoding='utf-8')
    (play / 'keep.txt').write_text('keep', encoding='utf-8')
    _init_test_repository(tmp_path)

    delete_patch = (
        '*** Begin Patch\n'
        '*** Delete File: play/.touch\n'
        '*** End Patch'
    )
    first = ToolCall(
        index=0,
        id='delete-first',
        name='apply_patch',
        arguments={'patch': delete_patch},
    )
    repeated = ToolCall(
        index=1,
        id='delete-repeat',
        name='apply_patch',
        arguments={'patch': delete_patch},
    )
    clear_remaining = ToolCall(
        index=0,
        id='clear-remaining',
        name='run_command',
        arguments={
            'command': 'python -',
            'stdin': "from pathlib import Path\nPath('play/keep.txt').unlink()\n",
        },
    )
    finish = ToolCall(
        index=0,
        id='finish-delete',
        name='finish_task',
        arguments={
            'task_kind': 'change',
            'status': 'completed',
            'summary': 'Deleted the requested marker.',
            'blocked_reasons': [],
        },
    )
    approvals = RecordingApprovalHandler()
    registry = create_default_registry(tmp_path)
    conversation = Conversation(
        client=FakeModelClient(
            tool_response(first, repeated),
            tool_response(clear_remaining),
            tool_response(finish),
        ),
        registry=registry,
        permission_manager=PermissionManager(
            tmp_path,
            approval_handler=approvals,
            user_path=tmp_path / 'user-permissions.json',
        ),
        intent_router=StaticIntentRouter(
            routed('change_task', relation='none', requires_change=True)
        ),
    )

    events = collect_turn(conversation, '删除 play/.touch')

    completed = [
        event for event in events if isinstance(event, ToolExecutionCompleted)
    ]
    assert len(approvals.requests) == 2
    assert [item.capability for item in approvals.requests] == [
        'file.delete',
        'file.delete',
    ]
    assert completed[0].result.success is True
    assert completed[1].result.success is True
    assert completed[1].result.metadata['status'] == 'already_completed'
    assert not (play / '.touch').exists()
    assert not (play / 'keep.txt').exists()
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'completed', [
        getattr(event, 'reasons')
        for event in events
        if getattr(event, 'reasons', None)
    ]


def test_agent_clears_empty_directory_tree_without_stagnation(
    tmp_path: Path,
) -> None:
    play = tmp_path / 'play'
    (play / '.keep').mkdir(parents=True)
    (play / '.tmp').mkdir()
    (play / 'src' / 'assets').mkdir(parents=True)
    (play / 'src' / 'modules').mkdir()
    (play / 'tests').mkdir()
    (tmp_path / 'README.md').write_text('baseline\n', encoding='utf-8')
    _init_test_repository(tmp_path)

    clear = ToolCall(
        index=0,
        id='clear-play-contents',
        name='remove_directory',
        arguments={
            'path': 'play',
            'recursive': True,
            'contents_only': True,
        },
    )
    finish = ToolCall(
        index=0,
        id='finish-clear-play',
        name='finish_task',
        arguments={
            'task_kind': 'change',
            'status': 'completed',
            'summary': 'Cleared the play directory contents.',
            'blocked_reasons': [],
        },
    )
    approvals = RecordingApprovalHandler()
    registry = create_default_registry(tmp_path)
    conversation = Conversation(
        client=FakeModelClient(
            tool_response(clear),
            tool_response(finish),
        ),
        registry=registry,
        permission_manager=PermissionManager(
            tmp_path,
            approval_handler=approvals,
            user_path=tmp_path / 'user-permissions.json',
        ),
        intent_router=StaticIntentRouter(
            routed('change_task', relation='none', requires_change=True)
        ),
    )

    events = collect_turn(conversation, '帮我清空 play 里面的内容')

    assert [item.capability for item in approvals.requests] == ['file.delete']
    assert play.is_dir()
    assert list(play.iterdir()) == []
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'completed'
    assert events[-1].result.changed_paths == ('play',)
    assert 'without a workspace change' not in events[-1].result.text


def test_process_workspace_change_clears_prior_edit_failure(
    tmp_path: Path,
) -> None:
    play = tmp_path / 'play'
    play.mkdir()
    (play / 'old.txt').write_text('old', encoding='utf-8')
    _init_test_repository(tmp_path)

    failed = ToolCall(
        index=0,
        id='failed-edit',
        name='failing_write',
        arguments={'path': 'play/missing.txt'},
    )
    process = ToolCall(
        index=0,
        id='process-delete',
        name='run_command',
        arguments={
            'command': 'python -',
            'stdin': "from pathlib import Path\nPath('play/old.txt').unlink()\n",
        },
    )
    finish = ToolCall(
        index=0,
        id='finish-process',
        name='finish_task',
        arguments={
            'task_kind': 'change',
            'status': 'completed',
            'summary': 'Cleared the remaining file.',
            'blocked_reasons': [],
        },
    )
    approvals = RecordingApprovalHandler()
    tracker = WorkspaceTracker(tmp_path)
    registry = ToolRegistry(
        [
            FailingWriteTool(tmp_path),
            RunCommandTool(tmp_path),
            FinishTaskTool(tmp_path),
        ],
        workspace_tracker=tracker,
    )
    conversation = Conversation(
        client=FakeModelClient(
            tool_response(failed),
            tool_response(process),
            tool_response(finish),
        ),
        registry=registry,
        permission_manager=PermissionManager(
            tmp_path,
            approval_handler=approvals,
            user_path=tmp_path / 'user-permissions.json',
        ),
        intent_router=StaticIntentRouter(
            routed('change_task', relation='none', requires_change=True)
        ),
    )

    events = collect_turn(conversation, '清空 play 目录')

    assert not (play / 'old.txt').exists()
    assert [item.capability for item in approvals.requests] == ['file.delete']
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'completed'


def test_single_target_delete_can_be_allowed_for_session_rule(
    tmp_path: Path,
) -> None:
    manager = PermissionManager(
        tmp_path,
        mode='supervised',
        approval_handler=StaticApprovalHandler('allow_session'),
        user_path=tmp_path / 'user-permissions.json',
    )
    request = PermissionRequest(
        'remove_directory',
        'file.delete',
        'high',
        ('play/src',),
    )

    first = asyncio.run(manager.authorize(request))
    manager.approval_handler = None
    second = asyncio.run(manager.authorize(request))

    assert first.action == 'allow'
    assert second.action == 'allow'
    assert second.source == 'session'
    assert manager.session_rules[0].target == 'play/src'


def test_supervised_permission_can_be_allowed_for_session(
    tmp_path: Path,
) -> None:
    manager = PermissionManager(
        tmp_path,
        mode='supervised',
        approval_handler=StaticApprovalHandler('allow_session'),
        user_path=tmp_path / 'user-permissions.json',
    )
    request = PermissionRequest(
        'run_command',
        'process.exec',
        'low',
        ('.',),
    )

    first = asyncio.run(manager.authorize(request))
    manager.approval_handler = None
    second = asyncio.run(manager.authorize(request))

    assert first.action == 'allow'
    assert second.action == 'allow'
    assert second.source == 'session'


def test_plan_mode_hides_effectful_tools_and_explains_mode(
    tmp_path: Path,
) -> None:
    read_tool = RecordingReadFileTool(tmp_path)
    write_tool = NoOpWriteTool(tmp_path)
    process_tool = NoOpProcessTool(tmp_path)
    registry = ToolRegistry([read_tool, write_tool, process_tool])
    client = FakeModelClient(streamed_response('只读分析'))
    conversation = Conversation(
        client=client,
        registry=registry,
        permission_manager=PermissionManager(tmp_path, mode='plan'),
    )

    events = collect_turn(conversation, '帮我修改文件')

    names = {item['name'] for item in client.calls[0]['tools']}
    assert 'read_file' in names
    assert 'no_op_write' not in names
    assert 'run_process' not in names
    assert 'Current mode: plan' in client.calls[0]['system']
    assert '/permission supervised' in client.calls[0]['system']
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'completed'
    assert conversation.task_manager.active is None


def test_permission_denial_stops_turn_without_model_recovery(
    tmp_path: Path,
) -> None:
    write_tool = NoOpWriteTool(tmp_path)
    registry = ToolRegistry([write_tool])
    denied_call = ToolCall(
        index=0,
        id='call-denied',
        name='no_op_write',
        arguments={'path': 'app.py'},
    )
    client = FakeModelClient(
        tool_response(denied_call),
        streamed_response('不应执行第二次模型调用'),
    )
    conversation = Conversation(
        client=client,
        registry=registry,
        permission_manager=PermissionManager(tmp_path, mode='plan'),
    )

    events = collect_turn(conversation, '修改 app.py')

    completed = [
        event for event in events if isinstance(event, ToolExecutionCompleted)
    ]
    assert len(client.calls) == 1
    assert write_tool.calls == []
    assert completed[0].result.error is not None
    assert completed[0].result.error.code == 'permission_denied'
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'blocked'
    assert '/permission supervised' in events[-1].result.text


def test_orphan_continuation_is_answered_by_the_model() -> None:
    client = FakeModelClient(streamed_response('请告诉我需要继续哪项工作。'))
    conversation = Conversation(client=client)

    events = collect_turn(conversation, '继续')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.text == '请告诉我需要继续哪项工作。'
    assert len(client.calls) == 1
    assert conversation.task_manager.active is None


def test_task_status_query_is_model_answered_and_read_only(
    tmp_path: Path,
) -> None:
    task = ActiveTask(
        id='task-resumed-status',
        goal='修复 play 目录中的复杂游戏',
        status='stuck',
        requires_change=True,
        scope_hints=('play/**',),
        blocked_reasons=('Patch validation failed.',),
    )
    write_tool = NoOpWriteTool(tmp_path)
    tracker = NoChangeWorkspaceTracker(tmp_path)
    client = FakeModelClient(streamed_response('当前任务是修复 play 游戏，状态为卡住。'))
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([write_tool], workspace_tracker=tracker),
        active_task=task,
        intent_router=StaticIntentRouter(
            routed('task_query', relation='active')
        ),
    )

    events = collect_turn(conversation, '当前任务是什么')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.text == '当前任务是修复 play 游戏，状态为卡住。'
    assert {item['name'] for item in client.calls[0]['tools']} == {'task_get'}
    assert write_tool.calls == []
    assert tracker.revision == 0
    assert conversation.task_manager.active == task


def test_task_status_query_without_active_task_uses_model() -> None:
    client = FakeModelClient(streamed_response('当前没有活动任务。'))
    conversation = Conversation(
        client=client,
        intent_router=StaticIntentRouter(routed('task_query')),
    )

    events = collect_turn(conversation, '当前任务是什么')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.text == '当前没有活动任务。'
    assert len(client.calls) == 1
    assert client.calls[0]['tools'] is None
    assert conversation.task_manager.active is None


def test_read_only_continuation_does_not_inherit_change_contract(
    tmp_path: Path,
) -> None:
    task = ActiveTask(
        id='task-resumed-explain',
        goal='修复 play 目录中的复杂游戏',
        status='stuck',
        requires_change=True,
        scope_hints=('play/**',),
        blocked_reasons=('Patch validation failed.',),
    )
    tracker = NoChangeWorkspaceTracker(tmp_path)
    write_tool = NoOpWriteTool(tmp_path)
    registry = ToolRegistry(
        [RecordingReadFileTool(tmp_path), write_tool],
        workspace_tracker=tracker,
    )
    client = FakeModelClient(streamed_response('这是之前失败的原因。'))
    conversation = Conversation(
        client=client,
        registry=registry,
        active_task=task,
        intent_router=StaticIntentRouter(
            routed('read_only', relation='active')
        ),
    )

    events = collect_turn(conversation, '继续解释之前失败的原因')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.model_calls == 2
    assert completed.result.usage == TokenUsage(17, 5)
    assert completed.result.text == '这是之前失败的原因。'
    assert '[ForgeCode Turn Change Contract]' not in client.calls[0]['system']
    assert {
        definition['name'] for definition in client.calls[0]['tools']
    } == {'read_file', 'task_get'}
    assert write_tool.calls == []
    assert conversation.task_manager.active == task


def test_mixed_status_and_change_prompt_reaches_normal_agent_loop(
    tmp_path: Path,
) -> None:
    task = ActiveTask(
        id='task-resumed-mixed',
        goal='修复 play 目录中的复杂游戏',
        status='stuck',
        requires_change=True,
        scope_hints=('play/**',),
    )
    client = FakeModelClient(streamed_response('准备继续修复。'))
    conversation = Conversation(
        client=client,
        active_task=task,
        permission_manager=PermissionManager(tmp_path, mode='plan'),
        intent_router=StaticIntentRouter(
            routed(
                'change_task',
                relation='active',
                requires_change=True,
            )
        ),
    )

    events = collect_turn(conversation, '当前任务是什么，然后继续修复')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.model_calls == 2
    assert len(client.calls) == 1


def test_ambiguous_route_preserves_task_and_asks_model(
    tmp_path: Path,
) -> None:
    task = ActiveTask(
        id='task-ambiguous-safe',
        goal='保护当前任务',
        status='blocked',
        requires_change=True,
        blocked_reasons=('Need clarification.',),
    )
    client = FakeModelClient(streamed_response('你希望我修改哪一部分？'))
    conversation = Conversation(
        client=client,
        active_task=task,
        intent_router=StaticIntentRouter(routed('ambiguous')),
    )

    events = collect_turn(conversation, '弄一下这个')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.text == '你希望我修改哪一部分？'
    assert conversation.task_manager.active == task
    assert len(client.calls) == 1
    assert client.calls[0]['tools'] is None


def test_model_routed_new_change_task_replaces_previous_task(
    tmp_path: Path,
) -> None:
    previous = ActiveTask(
        id='task-previous-read',
        goal='查看旧任务',
        status='completed',
    )
    client = FakeModelClient(streamed_response('已进入新任务。'))
    conversation = Conversation(
        client=client,
        active_task=previous,
        permission_manager=PermissionManager(tmp_path, mode='plan'),
        intent_router=StaticIntentRouter(
            routed(
                'change_task',
                relation='new',
                requires_change=True,
            )
        ),
    )

    events = collect_turn(conversation, '把 README 更新一下')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.model_calls == 2
    assert conversation.task_manager.active is not None
    assert conversation.task_manager.active.id != previous.id
    assert conversation.task_manager.active.goal == '把 README 更新一下'


def test_model_routed_continue_keeps_original_task_identity(
    tmp_path: Path,
) -> None:
    task = ActiveTask(
        id='task-explicit-continue',
        goal='完成 play 游戏',
        status='stuck',
        requires_change=True,
        blocked_reasons=('Previous edit failed.',),
    )
    client = FakeModelClient(streamed_response('继续处理。'))
    conversation = Conversation(
        client=client,
        active_task=task,
        permission_manager=PermissionManager(tmp_path, mode='plan'),
        intent_router=StaticIntentRouter(
            routed(
                'continue_task',
                relation='active',
                requires_change=True,
            )
        ),
    )

    events = collect_turn(conversation, '接着完成刚才没做完的工作')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.model_calls == 2
    assert conversation.task_manager.active is not None
    assert conversation.task_manager.active.id == task.id
    assert conversation.task_manager.active.goal == task.goal


def test_read_only_route_without_active_task_does_not_create_task(
    tmp_path: Path,
) -> None:
    client = FakeModelClient(streamed_response('这是只读分析。'))
    conversation = Conversation(
        client=client,
        permission_manager=PermissionManager(tmp_path, mode='plan'),
        intent_router=StaticIntentRouter(routed('read_only')),
    )

    events = collect_turn(conversation, '解释这个项目的架构')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.model_calls == 2
    assert conversation.task_manager.active is None


def test_conversation_forwards_stream_and_returns_final_result() -> None:
    client = FakeModelClient(streamed_response('RE', 'ADY'))
    conversation = Conversation(client=client)

    events = collect_turn(conversation, 'Only reply READY')

    assert events == [
        ModelCallStarted(iteration=1),
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=10, output_tokens=0),
            request_usage=TokenUsage(input_tokens=10, output_tokens=0),
        ),
        ModelTextDelta(text='RE'),
        ModelTextDelta(text='ADY'),
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=10, output_tokens=2),
            request_usage=TokenUsage(input_tokens=10, output_tokens=2),
        ),
        ModelCallCompleted(iteration=1),
        TurnCompleted(
            result=TurnResult(
                text='READY',
                usage=TokenUsage(input_tokens=10, output_tokens=2),
                last_request_usage=TokenUsage(
                    input_tokens=10,
                    output_tokens=2,
                ),
            )
        ),
    ]
    assert client.calls[0]['messages'] == [
        {'role': 'user', 'content': 'Only reply READY'}
    ]
    assert client.calls[0]['tools'] is None
    assert client.calls[0]['system'].startswith(load_system_prompt())


def test_system_prompt_defines_forgecode_identity() -> None:
    prompt = load_system_prompt()

    assert 'Your product identity is ForgeCode.' in prompt
    assert 'Do not claim to be Anthropic' in prompt
    assert 'tools included in the current model request are available' in prompt
    assert '`finish_task` is\n   optional structured completion' in prompt
    assert 'Tool\n   schema errors, repeated reads' in prompt
    assert 'Do not run destructive commands' in prompt
    assert 'call `verify`' in prompt
    assert 'Run dependent verification commands one at a' in prompt
    assert 'prefer the smallest relevant verification' in prompt


def test_conversation_accepts_an_explicit_system_prompt() -> None:
    client = FakeModelClient(streamed_response('READY'))
    conversation = Conversation(
        client=client,
        system_prompt='test system',
    )

    collect_turn(conversation, 'hello')

    assert client.calls[0]['system'].startswith('test system')


def test_task_policy_requires_workspace_tracking(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match='WorkspaceTracker'):
        Conversation(
            client=FakeModelClient(streamed_response('done')),
            registry=ToolRegistry([RecordingReadFileTool(tmp_path)]),
            task_policy=TaskPolicy(require_changes=True),
        )


def test_conversation_executes_tool_and_continues_until_final_text(
    tmp_path: Path,
) -> None:
    tool_call = ToolCall(
        index=0,
        id='toolu_read',
        name='read_file',
        arguments={'path': 'README.md'},
    )
    client = FakeModelClient(
        tool_response(tool_call),
        streamed_response(
            'Finished',
            input_tokens=30,
            output_tokens=4,
        ),
    )
    tool = RecordingReadFileTool(tmp_path)
    registry = ToolRegistry([tool])
    conversation = Conversation(client=client, registry=registry)

    events = collect_turn(conversation, 'Read the README')

    assert ToolExecutionStarted(tool_call=tool_call) in events
    assert ToolExecutionCompleted(
        tool_call=tool_call,
        result=tool.result,
    ) in events
    assert events[-1] == TurnCompleted(
        result=TurnResult(
            text='Finished',
            usage=TokenUsage(input_tokens=45, output_tokens=14),
            last_request_usage=TokenUsage(
                input_tokens=30,
                output_tokens=4,
            ),
            model_calls=2,
            tool_calls=(tool_call,),
        )
    )
    assert tool.calls == ['README.md']
    assert client.calls[0]['tools'] == registry.definitions
    second_request = client.calls[1]
    assert second_request['messages'][:2] == [
        {'role': 'user', 'content': 'Read the README'},
        {
            'role': 'assistant',
            'content': [
                {
                    'type': 'tool_use',
                    'id': 'toolu_read',
                    'name': 'read_file',
                    'input': {'path': 'README.md'},
                }
            ],
        },
    ]
    tool_result_message = second_request['messages'][2]
    assert tool_result_message['role'] == 'user'
    assert len(tool_result_message['content']) == 1
    result_block = tool_result_message['content'][0]
    assert result_block['tool_use_id'] == 'toolu_read'
    assert result_block['is_error'] is False
    payload = json.loads(result_block['content'])
    assert payload == {
        'success': True,
        'summary': 'Read file.',
        'content': 'file contents',
        'error': None,
        'metadata': {},
    }
    assert conversation.messages == [
        {'role': 'user', 'content': 'Read the README'},
        {
            'role': 'assistant',
            'content': [
                {
                    'type': 'tool_use',
                    'id': 'toolu_read',
                    'name': 'read_file',
                    'input': {'path': 'README.md'},
                }
            ],
        },
        tool_result_message,
        {'role': 'assistant', 'content': 'Finished'},
    ]


def test_conversation_executes_multiple_tool_calls_in_order(
    tmp_path: Path,
) -> None:
    first = ToolCall(
        index=0,
        id='toolu_first',
        name='read_file',
        arguments={'path': 'a.py'},
    )
    second = ToolCall(
        index=1,
        id='toolu_second',
        name='read_file',
        arguments={'path': 'b.py'},
    )
    client = FakeModelClient(
        tool_response(first, second),
        streamed_response('Done', input_tokens=25, output_tokens=3),
    )
    tool = RecordingReadFileTool(tmp_path)
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([tool]),
    )

    events = collect_turn(conversation, 'Read both files')

    assert tool.calls == ['a.py', 'b.py']
    result_blocks = client.calls[1]['messages'][2]['content']
    assert [
        block['tool_use_id']
        for block in result_blocks
    ] == [
        'toolu_first',
        'toolu_second',
    ]
    assert events[-1].result.tool_calls == (first, second)


def test_failed_tool_result_is_returned_to_model(
    tmp_path: Path,
) -> None:
    tool_call = ToolCall(
        index=0,
        id='toolu_missing',
        name='missing_tool',
        arguments={},
    )
    client = FakeModelClient(
        tool_response(tool_call),
        streamed_response('Could not run that tool.'),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([RecordingReadFileTool(tmp_path)]),
        context_root=tmp_path,
    )

    collect_turn(conversation, 'Use a missing tool')

    blocks = client.calls[1]['messages'][2]['content']
    block = blocks[0]
    payload = json.loads(block['content'])
    assert block['is_error'] is True
    assert payload['success'] is False
    assert payload['error']['code'] == 'unknown_tool'


def test_agent_loop_stops_at_model_call_limit(tmp_path: Path) -> None:
    first = ToolCall(
        index=0,
        id='toolu_1',
        name='read_file',
        arguments={'path': 'a.py'},
    )
    second = ToolCall(
        index=0,
        id='toolu_2',
        name='read_file',
        arguments={'path': 'b.py'},
    )
    client = FakeModelClient(tool_response(first), tool_response(second))
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([RecordingReadFileTool(tmp_path)]),
        max_iterations=2,
    )

    events = collect_turn(conversation, 'Never finish')

    assert len(client.calls) == 2
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'stuck'
    assert 'limit of 2 model calls' in events[-1].result.text


def test_agent_loop_stops_before_exceeding_tool_call_limit(
    tmp_path: Path,
) -> None:
    tool = RecordingReadFileTool(tmp_path)
    calls = tuple(
        ToolCall(
            index=index,
            id=f'toolu_{index}',
            name='read_file',
            arguments={'path': f'{index}.py'},
        )
        for index in range(3)
    )
    conversation = Conversation(
        client=FakeModelClient(tool_response(*calls)),
        registry=ToolRegistry([tool]),
        max_tool_calls=2,
    )

    events = collect_turn(conversation, 'Inspect many files')

    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'stuck'
    assert 'more than 2 tool calls' in events[-1].result.text
    assert tool.calls == []


def test_agent_loop_has_bounded_calls_but_no_input_token_limit_by_default() -> None:
    conversation = Conversation(client=FakeModelClient())

    assert conversation.max_iterations == 80
    assert conversation.max_tool_calls == 120
    assert conversation.max_turn_input_tokens is None


def test_invalid_tool_json_is_retried_without_executing_partial_calls(
    tmp_path: Path,
) -> None:
    partial_call = ToolCall(
        index=0,
        id='toolu_partial',
        name='read_file',
        arguments={'path': 'should-not-run.py'},
    )
    tool = RecordingReadFileTool(tmp_path)
    client = FakeModelClient(
        [
            ModelUsageUpdate(usage=TokenUsage(10, 4)),
            ModelToolCallCompleted(tool_call=partial_call),
            ModelProtocolError(
                'Invalid JSON arguments for tool write_file.',
                reason='invalid_tool_arguments',
                tool_name='write_file',
            ),
        ],
        streamed_response('Recovered safely.', input_tokens=12),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([tool]),
    )

    events = collect_turn(conversation, 'Build a page')

    assert tool.calls == []
    assert events[-1].result.text == 'Recovered safely.'
    assert events[-1].result.usage.input_tokens == 22
    feedback = client.calls[1]['messages'][-1]['content']
    assert 'No tool was executed' in feedback
    assert 'Available tools: read_file' in feedback
    assert 'write_file with at most 4000 characters' in feedback
    assert 'Recovery attempt 1 of 2' in feedback


def test_max_tokens_truncation_retries_with_small_patch_feedback() -> None:
    client = FakeModelClient(
        [
            ModelUsageUpdate(usage=TokenUsage(10, 4096)),
            ModelOutputTruncatedError(('apply_patch',)),
        ],
        streamed_response('Retried in smaller steps.', input_tokens=15),
    )
    conversation = Conversation(client=client)

    events = collect_turn(conversation, 'Build a game')

    assert events[-1].result.text == 'Retried in smaller steps.'
    feedback = client.calls[1]['messages'][-1]['content']
    assert 'reached the max_tokens limit' in feedback
    assert 'apply_patch with at most 4000 characters' in feedback
    assert 'Modify only one function or one file section' in feedback


def test_plain_text_truncation_preserves_and_continues_response() -> None:
    client = FakeModelClient(
        [
            ModelUsageUpdate(usage=TokenUsage(10, 0)),
            ModelTextDelta(text='First half, '),
            ModelUsageUpdate(usage=TokenUsage(10, 8192)),
            ModelOutputTruncatedError(),
        ],
        streamed_response(
            'second half.',
            input_tokens=15,
            output_tokens=3,
        ),
    )
    conversation = Conversation(client=client)

    events = collect_turn(conversation, 'Explain the result')

    result = events[-1].result
    assert result.text == 'First half, second half.'
    assert result.usage == TokenUsage(25, 8195)
    continuation_messages = client.calls[1]['messages'][-2:]
    assert continuation_messages[0] == {
        'role': 'assistant',
        'content': 'First half, ',
    }
    assert 'already generated has been preserved' in (
        continuation_messages[1]['content']
    )
    assert 'without repeating earlier content' in (
        continuation_messages[1]['content']
    )
    assert 'Continuation attempt 1 of 2' in (
        continuation_messages[1]['content']
    )
    assert conversation.messages[-1] == {
        'role': 'assistant',
        'content': 'second half.',
    }


def test_plain_text_continuation_stops_after_configured_limit() -> None:
    truncated = [
        ModelUsageUpdate(usage=TokenUsage(10, 0)),
        ModelTextDelta(text='partial'),
        ModelUsageUpdate(usage=TokenUsage(10, 8192)),
        ModelOutputTruncatedError(),
    ]
    client = FakeModelClient(truncated, truncated)
    conversation = Conversation(
        client=client,
        max_output_continuations=1,
    )

    with pytest.raises(
        ModelOutputTruncatedError,
        match='max_tokens limit',
    ):
        collect_turn(conversation, 'Explain at length')

    assert len(client.calls) == 2


def test_protocol_recovery_stops_after_configured_limit() -> None:
    error = ModelProtocolError(
        'invalid arguments',
        reason='invalid_tool_arguments',
        tool_name='write_file',
    )
    client = FakeModelClient([error], [error])
    conversation = Conversation(
        client=client,
        max_protocol_recoveries=1,
    )

    with pytest.raises(ModelProtocolError, match='invalid arguments'):
        collect_turn(conversation, 'Build a page')

    assert len(client.calls) == 2


def test_empty_model_response_is_retried_as_protocol_recovery() -> None:
    error = ModelProtocolError(
        'Provider returned no text or tool calls (stop_reason=end_turn).',
        reason='empty_model_response',
    )
    client = FakeModelClient(
        [ModelUsageUpdate(usage=TokenUsage(0, 0)), error],
        streamed_response('Recovered after the empty response.'),
    )
    conversation = Conversation(client=client)

    events = collect_turn(conversation, 'Handle the task')

    assert events[-1].result.status == 'completed'
    assert events[-1].result.text == 'Recovered after the empty response.'
    assert len(client.calls) == 2
    feedback = str(client.calls[1]['messages'][-1]['content'])
    assert 'stop_reason=end_turn' in feedback


def test_second_protocol_recovery_requests_minimal_skeleton() -> None:
    error = ModelOutputTruncatedError(('apply_patch',))
    client = FakeModelClient(
        [error],
        [error],
        streamed_response('Recovered with a skeleton.'),
    )
    conversation = Conversation(client=client)

    events = collect_turn(conversation, 'Build a game')

    assert events[-1].result.text == 'Recovered with a skeleton.'
    feedback = client.calls[2]['messages'][-1]['content']
    assert 'at most 2000 characters' in feedback
    assert 'Create only a minimal skeleton' in feedback
    assert 'HTML, CSS, and JavaScript in separate tool calls' in feedback


def test_conversation_sends_previous_turns_as_context() -> None:
    client = FakeModelClient(
        streamed_response('Hello'),
        streamed_response('Your name is Ada', input_tokens=20),
    )
    conversation = Conversation(client=client)

    collect_turn(conversation, 'Hello')
    collect_turn(conversation, 'What is my name?')

    assert client.calls[1]['messages'] == [
        {'role': 'user', 'content': 'Hello'},
        {'role': 'assistant', 'content': 'Hello'},
        {'role': 'user', 'content': 'What is my name?'},
    ]
    assert client.calls[1]['system'].startswith(load_system_prompt())
    assert conversation.messages == [
        {'role': 'user', 'content': 'Hello'},
        {'role': 'assistant', 'content': 'Hello'},
        {'role': 'user', 'content': 'What is my name?'},
        {'role': 'assistant', 'content': 'Your name is Ada'},
    ]


def test_current_goal_survives_many_tool_calls_and_message_snipping(
    tmp_path: Path,
) -> None:
    (tmp_path / 'sample.txt').write_text('content\n', encoding='utf-8')
    responses = [
        tool_response(
            ToolCall(
                index=0,
                id=f'toolu_{index}',
                name='read_file',
                arguments={'path': 'sample.txt'},
            )
        )
        for index in range(30)
    ]
    client = FakeModelClient(
        *responses,
        streamed_response('Finished the original task.'),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([RecordingReadFileTool(tmp_path)]),
        context_config=CompactionConfig(
            message_limit=10,
            keep_first_messages=2,
            keep_recent_messages=8,
        ),
        intent_router=StaticIntentRouter(
            routed('new_task', relation='new')
        ),
    )

    collect_turn(conversation, 'Keep this exact active goal')

    assert len(client.calls) <= 9
    assert all(
        'Goal:\nKeep this exact active goal' in call['system']
        for call in client.calls
    )


def test_exact_tool_repeat_is_skipped_after_limit(tmp_path: Path) -> None:
    call = lambda index: ToolCall(
        index=0,
        id=f'toolu_{index}',
        name='read_file',
        arguments={'path': 'sample.txt'},
    )
    tool = RecordingReadFileTool(tmp_path)
    client = FakeModelClient(
        tool_response(call(1)),
        tool_response(call(2)),
        tool_response(call(3)),
        streamed_response('Used the existing result.'),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([tool]),
    )

    events = collect_turn(conversation, 'Read the sample once')

    assert tool.calls == ['sample.txt']
    completed = [
        event for event in events
        if isinstance(event, ToolExecutionCompleted)
    ]
    assert completed[1].result.success is True
    assert completed[1].result.metadata['cache_hit'] is True
    assert completed[1].result.content.startswith(
        '[Identical cached tool result omitted.'
    )
    assert completed[2].result.success is False
    assert completed[2].result.error is not None
    assert completed[2].result.error.code == 'repeated_tool_call'


def test_edit_recovery_stops_noop_writes_without_total_call_limit(
    tmp_path: Path,
) -> None:
    tool = NoOpWriteTool(tmp_path)
    tracker = NoChangeWorkspaceTracker(tmp_path)
    responses = [
        tool_response(
            ToolCall(
                index=0,
                id=f'toolu_{index}',
                name='no_op_write',
                arguments={'path': f'file-{index}.txt'},
            )
        )
        for index in range(1, 7)
    ]
    conversation = Conversation(
        client=FakeModelClient(*responses),
        registry=ToolRegistry([tool], workspace_tracker=tracker),
        stagnation_warning=2,
        stagnation_limit=3,
        mutation_recovery_limit=3,
    )

    events = collect_turn(conversation, 'Make a real code change')

    result = next(
        event.result for event in events if isinstance(event, TurnCompleted)
    )
    assert result.status == 'stuck'
    assert '6 workspace-write attempt(s)' in result.text
    assert result.model_calls == 6
    assert len(tool.calls) == 6
    assert 'model calls without new workspace' not in result.text
    assert conversation.task_manager.active is not None
    assert conversation.task_manager.active.status == 'stuck'


def test_turn_stops_at_cumulative_input_token_limit(
    tmp_path: Path,
) -> None:
    tool = RecordingReadFileTool(tmp_path)
    client = FakeModelClient(
        tool_response(
            ToolCall(0, 'read-one', 'read_file', {'path': 'one.js'}),
            input_tokens=60,
        ),
        tool_response(
            ToolCall(0, 'read-two', 'read_file', {'path': 'two.js'}),
            input_tokens=60,
        ),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([tool]),
        max_turn_input_tokens=100,
        stagnation_warning=10,
        stagnation_limit=20,
    )

    events = collect_turn(conversation, 'Inspect two files')

    result = next(
        event.result for event in events if isinstance(event, TurnCompleted)
    )
    assert result.status == 'stuck'
    assert result.model_calls == 2
    assert result.usage.input_tokens == 120
    assert 'cumulative input-token limit of 100' in result.text
    assert len(client.calls) == 2


def test_failed_mutation_without_tracker_rejects_text_completion(
    tmp_path: Path,
) -> None:
    write = FailingWriteTool(tmp_path)
    client = FakeModelClient(
        tool_response(
            ToolCall(
                index=0,
                id='failed-write-without-tracker',
                name='failing_write',
                arguments={'path': 'world.js'},
            )
        ),
        streamed_response('Done despite the failed write.'),
        streamed_response('Still done despite the failed write.'),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([write]),
        mutation_recovery_limit=2,
    )

    events = collect_turn(conversation, 'Fix the rendering bug')

    result = next(
        event.result for event in events if isinstance(event, TurnCompleted)
    )
    assert conversation.workspace_tracker is None
    assert result.status == 'stuck'
    assert result.model_calls == 3
    assert 'workspace-write attempt(s)' in result.text
    assert 'Done despite the failed write.' not in result.text
    assert '[Failed Mutation Recovery]' in client.calls[1]['system']


def test_repeated_invalid_tool_arguments_end_as_stuck() -> None:
    calls = [
        ToolCall(
            index=0,
            id=f'toolu_invalid_{index}',
            name='read_file',
            arguments={'path': 'sample.txt', 'unexpected': index},
        )
        for index in range(1, 4)
    ]
    client = FakeModelClient(*(tool_response(call) for call in calls))
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([RecordingReadFileTool(Path.cwd())]),
        max_tool_protocol_recoveries=3,
    )

    events = collect_turn(conversation, 'Read sample.txt')

    result = next(
        event.result for event in events if isinstance(event, TurnCompleted)
    )
    assert result.status == 'stuck'
    assert 'schema-invalid tool requests' in result.text
    first_recovery = client.calls[1]['messages'][-1]
    assert first_recovery['role'] == 'user'
    assert 'Exact rejection(s):' in first_recovery['content']
    assert '`unexpected` is not an allowed argument' in (
        first_recovery['content']
    )
    assert 'Do not repeat the rejected payload.' in (
        first_recovery['content']
    )


def test_invalid_write_arguments_do_not_enter_mutation_recovery(
    tmp_path: Path,
) -> None:
    calls = [
        ToolCall(
            index=0,
            id=f'toolu_oversized_{index}',
            name='tiny_write',
            arguments={'path': f'file-{index}.txt', 'content': 'too long'},
        )
        for index in range(1, 4)
    ]
    client = FakeModelClient(*(tool_response(call) for call in calls))
    tracker = NoChangeWorkspaceTracker(tmp_path)
    conversation = Conversation(
        client=client,
        registry=ToolRegistry(
            [TinyWriteTool(tmp_path)],
            workspace_tracker=tracker,
        ),
        max_tool_protocol_recoveries=3,
    )

    events = collect_turn(conversation, 'Write a generated file')

    result = next(
        event.result for event in events if isinstance(event, TurnCompleted)
    )
    assert result.status == 'stuck'
    assert 'schema-invalid tool requests' in result.text
    assert 'workspace-write attempt(s)' not in result.text
    assert all(
        '[Failed Mutation Recovery]' not in call['system']
        for call in client.calls
    )


def test_unsupported_shell_syntax_is_a_protocol_recovery_failure() -> None:
    result = ToolResult.fail(
        'unsupported_shell_syntax',
        'Use stdin instead of a POSIX heredoc on Windows.',
    )

    assert is_tool_protocol_failure(result) is True


def test_copied_patch_line_numbers_are_a_protocol_recovery_failure() -> None:
    result = ToolResult.fail(
        'patch_contains_read_line_numbers',
        'Remove read_file line-number prefixes.',
    )

    assert is_tool_protocol_failure(result) is True


def test_changing_grep_patterns_cannot_extend_a_completed_file_read(
    tmp_path: Path,
) -> None:
    (tmp_path / 'player.js').write_text(
        'function update() {}\nfunction draw() {}\n',
        encoding='utf-8',
    )
    calls = [
        ToolCall(
            index=0,
            id='read',
            name='read_file',
            arguments={'path': 'player.js'},
        ),
        ToolCall(
            index=0,
            id='grep-update',
            name='grep',
            arguments={'path': 'player.js', 'pattern': 'update'},
        ),
        ToolCall(
            index=0,
            id='grep-draw',
            name='grep',
            arguments={'path': 'player.js', 'pattern': 'draw'},
        ),
    ]
    client = FakeModelClient(
        *(tool_response(call) for call in calls),
        streamed_response('player.js defines update and draw functions.'),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry(
            [ReadFileTool(tmp_path), GrepTool(tmp_path)]
        ),
        stagnation_warning=2,
        stagnation_limit=4,
    )

    events = collect_turn(conversation, 'Explain player.js')

    tool_events = [
        event for event in events
        if isinstance(event, ToolExecutionCompleted)
    ]
    assert tool_events[0].result.success is True
    assert all(event.result.success for event in tool_events)
    assert len(client.calls) == 4
    assert client.calls[-1]['tools'] is not None


def test_compaction_is_checked_before_every_model_call(
    tmp_path: Path,
) -> None:
    client = FakeModelClient(
        tool_response(
            ToolCall(
                index=0,
                id='toolu_read',
                name='read_file',
                arguments={'path': 'sample.txt'},
            )
        ),
        streamed_response('Finished.'),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([RecordingReadFileTool(tmp_path)]),
    )
    compact = AsyncMock(return_value=None)
    conversation.context.compact_history = compact

    collect_turn(conversation, 'Read sample.txt')

    assert compact.await_count == 2


def test_conversation_does_not_commit_stream_without_text() -> None:
    client = FakeModelClient(
        [
            ModelUsageUpdate(
                usage=TokenUsage(input_tokens=10, output_tokens=0)
            )
        ]
    )
    conversation = Conversation(client=client)

    with pytest.raises(
        ModelResponseError,
        match='did not contain any text',
    ):
        collect_turn(conversation, 'Hello')

    assert conversation.messages == []


def test_relevant_repository_memory_is_injected_only_for_current_query(
    tmp_path: Path,
) -> None:
    client = FakeModelClient(
        streamed_response('Use pytest'),
        streamed_response('Use the formatter'),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([RecordingReadFileTool(tmp_path)]),
        context_root=tmp_path,
    )
    conversation.context.remember('testing', 'Calculator tests use pytest.')

    collect_turn(conversation, 'How do calculator tests run?')
    collect_turn(conversation, 'How should formatting work?')

    assert 'Calculator tests use pytest.' in client.calls[0]['system']
    assert 'Calculator tests use pytest.' not in client.calls[1]['system']


def test_conversation_does_not_commit_stream_without_usage() -> None:
    client = FakeModelClient([ModelTextDelta(text='Hello')])
    conversation = Conversation(client=client)

    with pytest.raises(
        ModelResponseError,
        match='did not contain token usage',
    ):
        collect_turn(conversation, 'Hello')

    assert conversation.messages == []


def test_conversation_rejects_empty_prompt() -> None:
    conversation = Conversation(client=FakeModelClient())

    with pytest.raises(ValueError, match='prompt must not be empty'):
        collect_turn(conversation, '   ')


def test_conversation_context_stats_include_request_layers() -> None:
    client = FakeModelClient()
    client.max_tokens = 100
    client.context_window = 1_000
    tools = [
        {
            'name': 'read_file',
            'description': 'Read a file.',
            'input_schema': {'type': 'object'},
        }
    ]
    conversation = Conversation(
        client=client,
        system_prompt='system rules',
        tools=tools,
    )
    conversation.messages.append({'role': 'user', 'content': 'history'})

    stats = conversation.context_stats

    assert stats.message_count == 1
    assert stats.system_characters > len('system rules')
    assert 'Runtime Tool Availability' in (
        conversation._system_prompt_with_task()
    )
    assert stats.tool_schema_characters > 0
    assert stats.context_window_tokens == 1_000
    assert stats.reserved_output_tokens == 100
    assert stats.remaining_tokens is not None
