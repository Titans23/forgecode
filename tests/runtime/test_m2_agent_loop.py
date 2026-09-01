'''Integration tests for the M2 model-tool-verification loop.'''

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
import subprocess
from typing import Any
from unittest.mock import AsyncMock

from forge.permissions.approval import StaticApprovalHandler
from forge.permissions.policy import PermissionManager
from forge.runtime.agent_loop import Conversation
from forge.runtime.completion import TaskPolicy
from forge.runtime.router import RouteResult, TurnDecision
from forge.runtime.state import (
    CompletionBlocked,
    ConversationEvent,
    ModelStreamEvent,
    ModelTextDelta,
    ModelToolCallCompleted,
    ModelUsageUpdate,
    TokenUsage,
    ToolCall,
    ToolExecutionCompleted,
    TurnCompleted,
    VerificationCompleted,
    WorkspaceChanged,
)
from forge.sessions.checkpoint import CheckpointStore
from forge.tools import create_default_registry
from forge.tools.base import Tool, ToolInput, ToolResult
from forge.tasks.state import ActiveTask


def initialize_git_repository(root: Path) -> None:
    subprocess.run(['git', 'init', '--quiet'], cwd=root, check=True)
    subprocess.run(
        ['git', 'config', 'user.email', 'forge@example.test'],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ['git', 'config', 'user.name', 'ForgeCode Tests'],
        cwd=root,
        check=True,
    )
    (root / 'sample.txt').write_text('old\n', encoding='utf-8')
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'baseline'],
        cwd=root,
        check=True,
    )


def add_tracked_smoke_test(root: Path) -> None:
    (root / 'test_smoke.py').write_text(
        'def test_ok():\n    assert True\n',
        encoding='utf-8',
    )
    (root / '.gitignore').write_text(
        '__pycache__/\n.pytest_cache/\n',
        encoding='utf-8',
    )
    subprocess.run(
        ['git', 'add', 'test_smoke.py', '.gitignore'],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'add smoke test'],
        cwd=root,
        check=True,
    )



class FakeModelClient:
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


class EmptyProcessInput(ToolInput):
    pass


class StubExploreTool(Tool[EmptyProcessInput]):
    name = 'explore_repository'
    description = 'Return a compact test exploration report.'
    input_model = EmptyProcessInput

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.calls = 0

    async def execute(self, arguments: EmptyProcessInput) -> ToolResult:
        del arguments
        self.calls += 1
        return ToolResult.ok(
            'Explore Agent completed a read-only repository investigation.',
            content='{"summary":"Edit sample.txt","suggested_edit_points":[]}',
        )


class ProcessModifyTool(Tool[EmptyProcessInput]):
    name = 'process_modify'
    description = 'Modify sample.txt from a process-like test tool.'
    input_model = EmptyProcessInput
    effect = 'process'

    async def execute(self, arguments: EmptyProcessInput) -> ToolResult:
        del arguments
        (self.root / 'sample.txt').write_text('temporary\n', encoding='utf-8')
        return ToolResult.ok('Temporarily changed sample.txt.')


class ProcessRevertTool(Tool[EmptyProcessInput]):
    name = 'process_revert'
    description = 'Revert sample.txt from a process-like test tool.'
    input_model = EmptyProcessInput
    effect = 'process'

    async def execute(self, arguments: EmptyProcessInput) -> ToolResult:
        del arguments
        (self.root / 'sample.txt').write_text('old\n', encoding='utf-8')
        return ToolResult.ok('Reverted sample.txt to the turn baseline.')


def response_with_tool(call: ToolCall) -> list[ModelStreamEvent]:
    return [
        ModelUsageUpdate(usage=TokenUsage(10, 0)),
        ModelToolCallCompleted(tool_call=call),
        ModelUsageUpdate(usage=TokenUsage(10, 2)),
    ]


def response_with_tools(*calls: ToolCall) -> list[ModelStreamEvent]:
    return [
        ModelUsageUpdate(usage=TokenUsage(10, 0)),
        *(ModelToolCallCompleted(tool_call=call) for call in calls),
        ModelUsageUpdate(usage=TokenUsage(10, 2)),
    ]


def text_response(text: str) -> list[ModelStreamEvent]:
    return [
        ModelUsageUpdate(usage=TokenUsage(10, 0)),
        ModelTextDelta(text=text),
        ModelUsageUpdate(usage=TokenUsage(10, 2)),
    ]


def finish_response(
    call_id: str,
    *,
    task_kind: str,
    status: str = 'completed',
    summary: str = 'Finished.',
    blocked_reasons: list[str] | None = None,
) -> list[ModelStreamEvent]:
    return response_with_tool(
        ToolCall(
            0,
            call_id,
            'finish_task',
            {
                'task_kind': task_kind,
                'status': status,
                'summary': summary,
                'blocked_reasons': blocked_reasons or [],
            },
        )
    )


def collect_turn(
    conversation: Conversation,
    prompt: str,
) -> list[ConversationEvent]:
    async def collect() -> list[ConversationEvent]:
        return [event async for event in conversation.stream(prompt)]

    return asyncio.run(collect())


def read_only_stagnation_calls(prefix: str) -> list[ToolCall]:
    '''Build one evidence read followed by eight read-only no-progress calls.'''
    specifications = [
        ('read_file', {'path': 'sample.txt'}),
        ('grep', {'path': 'sample.txt', 'pattern': 'old'}),
        ('run_command', {'command': 'git status --short'}),
        ('read_file', {'path': 'sample.txt'}),
        ('grep', {'path': 'sample.txt', 'pattern': '^old$'}),
        ('run_command', {'command': 'git diff --check'}),
        ('read_file', {'path': 'sample.txt'}),
        ('grep', {'path': 'sample.txt', 'pattern': 'o.d'}),
        ('run_command', {'command': 'git status --porcelain=v1'}),
    ]
    return [
        ToolCall(0, f'{prefix}-{index}', name, arguments)
        for index, (name, arguments) in enumerate(specifications, start=1)
    ]


def test_resumed_task_can_finish_from_persisted_workspace_change(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    (tmp_path / 'sample.txt').write_text(
        'earlier task edit\n',
        encoding='utf-8',
    )
    diff = ToolCall(
        0,
        'resume-diff',
        'git_diff',
        {'path': 'sample.txt'},
    )
    verify = ToolCall(
        0,
        'resume-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    client = FakeModelClient(
        response_with_tool(diff),
        response_with_tool(verify),
        finish_response(
            'resume-finish',
            task_kind='change',
            summary='Completed the persisted task change.',
        ),
    )
    task = ActiveTask(
        id='task-resume000001',
        goal='Change and verify sample.txt',
        status='stuck',
        requires_change=True,
        scope_hints=('sample.txt',),
        scope_source='explicit',
        workspace_paths=('sample.txt',),
    )
    router = AsyncMock()
    router.route.return_value = RouteResult(
        decision=TurnDecision(
            intent='continue_task',
            task_relation='active',
            requires_workspace_change=True,
            requires_verification=True,
            confidence=0.99,
            reason='Resume the active task.',
        ),
        usage=TokenUsage(7, 3),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(
            require_changes=True,
            require_verification=True,
        ),
        active_task=task,
        intent_router=router,
    )

    events = collect_turn(conversation, '\ufeffcontinue')
    completed = events[-1]

    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.changed_paths == ('sample.txt',)
    assert completed.result.verification is not None
    assert '[ForgeCode Resumed Change Evidence]' in str(
        client.calls[0]['system']
    )
    assert 'Do not create an unrelated edit' in str(client.calls[0]['system'])
    first_tool_names = {
        str(definition['name'])
        for definition in client.calls[0]['tools'] or ()
    }
    assert {'finish_task', 'git_diff', 'verify', 'apply_patch'} <= first_tool_names
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == (
        'earlier task edit\n'
    )



def test_completed_change_task_can_resume_as_inspection(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    task = ActiveTask(
        id='task-completed001',
        goal='Change and verify sample.txt',
        status='completed',
        requires_change=True,
        workspace_paths=('sample.txt',),
    )
    router = AsyncMock()
    router.route.return_value = RouteResult(
        decision=TurnDecision(
            intent='continue_task',
            task_relation='active',
            requires_workspace_change=False,
            requires_verification=True,
            confidence=0.99,
            reason='Inspect the already completed task.',
        ),
        usage=TokenUsage(7, 3),
    )
    client = FakeModelClient(
        response_with_tool(
            ToolCall(0, 'inspect-read', 'read_file', {'path': 'sample.txt'})
        ),
        response_with_tool(
            ToolCall(
                0,
                'inspect-verify',
                'verify',
                {'command': 'git diff --check'},
            )
        ),
        finish_response(
            'inspect-wrong-kind',
            task_kind='change',
            summary='Inspected the completed task; no edit was needed.',
        ),
        finish_response(
            'inspect-finish',
            task_kind='inspection',
            summary='Inspected the completed task and verification passed.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        active_task=task,
        intent_router=router,
    )

    events = collect_turn(conversation, 'Continue and inspect the completed task.')
    completed = events[-1]

    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed', (
        completed.result,
        len(client.calls),
        conversation.working_state.evidence_paths,
    )
    assert completed.result.changed_paths == ()
    assert completed.result.verification is not None
    assert conversation.task_manager.active is not None
    assert conversation.task_manager.active.status == 'completed'


def test_completion_validation_rejects_unverified_change_once(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    edit = ToolCall(
        0,
        'toolu_edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0, 'toolu_verify', 'verify', {'command': 'git diff --check'}
    )
    client = FakeModelClient(
        response_with_tool(edit),
        finish_response('finish_early', task_kind='change'),
        response_with_tool(verify),
        finish_response(
            'finish_verified',
            task_kind='change',
            summary='Implemented and verified.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_verification=True),
    )
    events = collect_turn(conversation, 'Change and verify sample.txt')
    completed = events[-1]

    assert any(isinstance(item, WorkspaceChanged) for item in events)
    assert any(isinstance(item, CompletionBlocked) for item in events)
    assert not any(isinstance(item, VerificationCompleted) for item in events)
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert completed.result.changed_paths == ('sample.txt',)
    assert completed.result.verification is None
    assert len(client.calls) == 2
    assert any(
        'has not been verified' in reason
        for reason in completed.result.completion_reasons
    )


def test_verify_side_effect_binds_evidence_to_post_command_revision(
    tmp_path: Path,
) -> None:
    (tmp_path / 'verify_side_effect.py').write_text(
        "from pathlib import Path\nPath('generated.txt').write_text('ok\\n')\n",
        encoding='utf-8',
    )
    initialize_git_repository(tmp_path)
    edit = ToolCall(
        0,
        'side-effect-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'side-effect-verify',
        'verify',
        {'command': 'python verify_side_effect.py'},
    )
    client = FakeModelClient(
        response_with_tool(edit),
        response_with_tool(verify),
        finish_response(
            'side-effect-finish',
            task_kind='change',
            summary='Changed and verified the final workspace.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(
            require_changes=True,
            require_verification=True,
        ),
    )

    events = collect_turn(
        conversation,
        'Change sample.txt and verify the final workspace.',
    )

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.changed_paths == ('generated.txt', 'sample.txt')
    assert completed.result.verification is not None
    assert completed.result.verification.workspace_revision == 2
    revisions = [
        event.revision
        for event in events
        if isinstance(event, WorkspaceChanged)
    ]
    assert revisions == [1, 2]


def test_default_policy_can_finish_a_valid_diff_without_verify(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    edit = ToolCall(
        0,
        'toolu_default_edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(edit),
        finish_response(
            'finish_without_verify',
            task_kind='change',
            summary='Implemented the requested change.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, 'Change sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.changed_paths == ('sample.txt',)
    assert completed.result.verification is None
    assert not any(isinstance(item, CompletionBlocked) for item in events)


def test_replayed_game_evidence_can_progress_to_edit_and_verification(
    tmp_path: Path,
) -> None:
    game_files = {
        'play/js/world.js': 'const faceMode = buggy;\n',
        'play/js/game.js': 'export const game = true;\n',
        'play/js/player.js': 'export const player = true;\n',
        'play/js/constants.js': 'export const BLOCK = 1;\n',
        'play/index.html': '<main>game</main>\n',
    }
    for path, content in game_files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    initialize_git_repository(tmp_path)

    initial_reads = tuple(
        ToolCall(0, f'initial-{index}', 'read_file', {'path': path})
        for index, path in enumerate(game_files)
    )
    replay_reads = tuple(
        ToolCall(
            0,
            f'replay-{index}',
            'read_file',
            {'path': path, 'start_line': 1, 'end_line': 500},
        )
        for index, path in enumerate(game_files)
    )
    edit = ToolCall(
        0,
        'edit-world',
        'replace_text',
        {
            'path': 'play/js/world.js',
            'old_text': 'const faceMode = buggy;\n',
            'new_text': 'const faceMode = six-sided;\n',
        },
    )
    verify = ToolCall(
        0,
        'verify-game',
        'verify',
        {'command': 'git diff --check'},
    )
    client = FakeModelClient(
        response_with_tools(*initial_reads),
        response_with_tools(*replay_reads),
        response_with_tool(edit),
        response_with_tool(verify),
        finish_response(
            'finish-game',
            task_kind='change',
            summary='Fixed and verified the block rendering code.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(
            require_changes=True,
            require_verification=True,
        ),
    )

    events = collect_turn(conversation, '修复方块材质渲染')

    replay_results = [
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id.startswith('replay-')
    ]
    assert len(replay_results) == len(game_files)
    assert all(result.metadata['evidence_replayed'] for result in replay_results)
    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.verification is not None
    assert completed.result.verification.success is True
    assert 'six-sided' in (
        tmp_path / 'play/js/world.js'
    ).read_text(encoding='utf-8')


def test_failed_patch_recovers_to_valid_begin_patch_and_completion(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    invalid_patch = (
        '*** Begin ' 'Patch\n'
        '*** Update File: sample.txt\n'
        '@@\n'
        '-not-current\n'
        '+new\n'
        '*** End ' 'Patch'
    )
    valid_patch = (
        '*** Begin ' 'Patch\n'
        '*** Update File: sample.txt\n'
        '@@\n'
        '-old\n'
        '+new\n'
        '*** End ' 'Patch'
    )
    client = FakeModelClient(
        response_with_tool(
            ToolCall(
                0,
                'patch-failed',
                'apply_patch',
                {'patch': invalid_patch},
            )
        ),
        response_with_tool(
            ToolCall(
                0,
                'read-target',
                'read_file',
                {'path': 'sample.txt'},
            )
        ),
        response_with_tool(
            ToolCall(
                0,
                'patch-retried',
                'apply_patch',
                {'patch': valid_patch},
            )
        ),
        response_with_tool(
            ToolCall(
                0,
                'verify-recovery',
                'verify',
                {'command': 'git diff --check'},
            )
        ),
        finish_response(
            'finish-recovery',
            task_kind='change',
            summary='Recovered, changed, and verified sample.txt.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(
            require_changes=True,
            require_verification=True,
        ),
    )

    events = collect_turn(conversation, 'Fix and verify sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.model_calls == 5
    assert completed.result.changed_paths == ('sample.txt',)
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'
    assert '[Failed Mutation Recovery]' in client.calls[1]['system']
    assert 'patch_context_not_found' in client.calls[1]['system']
    assert '[Failed Mutation Recovery]' not in client.calls[3]['system']


def test_edit_recovery_counts_failures_per_target(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    (tmp_path / 'tmp').write_text('existing\n', encoding='utf-8')
    unrelated_failure = ToolCall(
        0,
        'unrelated-create-failure',
        'write_file',
        {'path': 'tmp', 'content': 'noop'},
    )
    invalid_patch = ToolCall(
        0,
        'target-first-failure',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Update File: sample.txt\n'
                '@@\n'
                '-missing\n'
                '+new\n'
                '*** End Patch\n'
            )
        },
    )
    valid_patch = ToolCall(
        0,
        'target-corrected-edit',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Update File: sample.txt\n'
                '@@\n'
                '-old\n'
                '+new\n'
                '*** End Patch\n'
            )
        },
    )
    verify = ToolCall(
        0,
        'target-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    client = FakeModelClient(
        response_with_tool(unrelated_failure),
        response_with_tool(invalid_patch),
        response_with_tool(valid_patch),
        response_with_tool(verify),
        finish_response(
            'target-finish',
            task_kind='change',
            summary='Changed and verified sample.txt.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(
            require_changes=True,
            require_verification=True,
        ),
        mutation_recovery_limit=2,
    )

    events = collect_turn(conversation, 'Change and verify sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.changed_paths == ('sample.txt',)
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'


def test_tmp_content_is_rejected_as_placeholder_write(tmp_path: Path) -> None:
    initialize_git_repository(tmp_path)
    client = FakeModelClient(
        response_with_tool(
            ToolCall(
                0,
                'tmp-placeholder',
                'write_file',
                {'path': 'src/index.ts', 'content': 'tmp'},
            )
        ),
        text_response('Unable to continue.'),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, 'Fix the TypeScript implementation.')

    rejected = next(
        event
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'tmp-placeholder'
    )
    assert rejected.result.success is False
    assert rejected.result.error is not None
    assert rejected.result.error.code == 'placeholder_write_denied'
    assert not (tmp_path / 'src' / 'index.ts').exists()


def test_final_targeted_read_gets_one_corrected_edit_opportunity(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    failed_patch = ToolCall(
        0,
        'late-read-patch-1',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Update File: sample.txt\n'
                '@@\n'
                '-missing-one\n'
                '+new\n'
                '*** End Patch\n'
            )
        },
    )
    failed_replace = ToolCall(
        0,
        'late-read-replace',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'missing-two\n',
            'new_text': 'new\n',
        },
    )
    second_patch = ToolCall(
        0,
        'late-read-patch-2',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Update File: sample.txt\n'
                '@@\n'
                '-missing-three\n'
                '+new\n'
                '*** End Patch\n'
            )
        },
    )
    targeted_read = ToolCall(
        0,
        'late-targeted-read',
        'read_file',
        {'path': 'sample.txt', 'start_line': 1, 'end_line': 5},
    )
    corrected_edit = ToolCall(
        0,
        'late-corrected-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(failed_patch),
        response_with_tool(failed_replace),
        response_with_tool(second_patch),
        response_with_tool(targeted_read),
        response_with_tool(corrected_edit),
        finish_response(
            'late-read-finish',
            task_kind='change',
            summary='Corrected the edit from exact file evidence.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
        mutation_recovery_limit=4,
    )

    events = collect_turn(conversation, 'Update sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.model_calls == 6
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'


def test_targetless_patch_failure_clears_on_real_code_edit(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    empty_patch = ToolCall(
        0,
        'targetless-empty-patch',
        'apply_patch',
        {'patch': '*** Begin Patch\n*** End Patch\n'},
    )
    corrected_edit = ToolCall(
        0,
        'targetless-corrected-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(empty_patch),
        response_with_tool(corrected_edit),
        finish_response(
            'targetless-finish',
            task_kind='change',
            summary='Completed a real code edit after malformed patch recovery.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
    )

    events = collect_turn(conversation, 'Update sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.changed_paths == ('sample.txt',)
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'


def test_existing_file_strategy_error_clears_on_task_relevant_sibling_edit(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    (tmp_path / 'other.py').write_text('OLD = True\n', encoding='utf-8')
    existing_write = ToolCall(
        0,
        'sibling-existing-file-write',
        'write_file',
        {'path': 'sample.txt', 'content': 'dummy'},
    )
    failed_same_target_edit = ToolCall(
        0,
        'sibling-failed-same-target-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'missing\n',
            'new_text': 'new\n',
        },
    )
    sibling_edit = ToolCall(
        0,
        'sibling-corrected-edit',
        'replace_text',
        {
            'path': 'other.py',
            'old_text': 'OLD = True\n',
            'new_text': 'READY = True\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(existing_write),
        response_with_tool(failed_same_target_edit),
        response_with_tool(sibling_edit),
        finish_response(
            'sibling-edit-finish',
            task_kind='change',
            summary='Completed the task through a relevant existing-file edit.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
    )

    events = collect_turn(conversation, 'Improve the project implementation')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.changed_paths == ('other.py',)
    assert (tmp_path / 'other.py').read_text(encoding='utf-8') == (
        'READY = True\n'
    )


def test_existing_directory_noop_does_not_create_recovery_debt(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    (tmp_path / 'play' / 'src' / 'assets').mkdir(parents=True)
    create_existing = ToolCall(
        0,
        'existing-directory-create',
        'create_directory',
        {'path': 'play/src/assets'},
    )
    concrete_write = ToolCall(
        0,
        'existing-directory-write',
        'write_file',
        {'path': 'play/src/main.js', 'content': 'export {};\n'},
    )
    client = FakeModelClient(
        response_with_tool(create_existing),
        response_with_tool(concrete_write),
        finish_response(
            'existing-directory-finish',
            task_kind='change',
            summary='Created the requested implementation file.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
    )

    events = collect_turn(conversation, 'Implement the game in play/src.')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.changed_paths == ('play/src/main.js',)
    assert not (
        tmp_path / 'play' / 'src' / 'assets' / '.gitkeep'
    ).exists()
    recovery_names = {
        definition['name'] for definition in client.calls[1]['tools'] or ()
    }
    assert 'create_directory' in recovery_names
    assert 'write_file' in recovery_names
    assert 'apply_patch' in recovery_names


def test_required_change_prose_gets_one_bounded_edit_retry(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    edit = ToolCall(
        0,
        'prose-only-corrected-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    client = FakeModelClient(
        text_response('I only inspected the project and made no file changes.'),
        response_with_tool(edit),
        finish_response(
            'prose-only-finish',
            task_kind='change',
            summary='Implemented the requested workspace change.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
    )

    events = collect_turn(
        conversation,
        'Change sample.txt',
    )

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.model_calls == 3
    assert 'Do not ask for confirmation' in str(client.calls[1]['messages'])
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'


def test_write_then_revert_to_baseline_enters_edit_recovery(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    (tmp_path / 'sample.txt').write_bytes(b'old\n')
    client = FakeModelClient(
        response_with_tools(
            ToolCall(
                0,
                'write-new',
                'replace_text',
                {
                    'path': 'sample.txt',
                    'old_text': 'old\n',
                    'new_text': 'new\n',
                },
            ),
            ToolCall(
                1,
                'restore-old',
                'replace_text',
                {
                    'path': 'sample.txt',
                    'old_text': 'new\n',
                    'new_text': 'old\n',
                },
            ),
        ),
        text_response('Done.'),
        text_response('Still done without a corrected edit.'),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        mutation_recovery_limit=2,
    )

    events = collect_turn(conversation, 'Change sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert completed.result.changed_paths == ()
    assert completed.result.model_calls == 3
    assert '[Failed Mutation Recovery]' in client.calls[1]['system']
    assert 'no_workspace_change' in client.calls[1]['system']
    assert 'Edit Recovery rejected the prose response' in str(
        client.calls[2]['messages']
    )
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'old\n'


def test_later_write_failure_in_same_response_remains_in_recovery(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    successful_edit = ToolCall(
        0,
        'successful-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    failed_edit = ToolCall(
        1,
        'later-failed-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'missing\n',
            'new_text': 'extra\n',
        },
    )
    client = FakeModelClient(
        response_with_tools(successful_edit, failed_edit),
        text_response('Done after only the first edit.'),
        text_response('Still done without the second edit.'),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        mutation_recovery_limit=2,
    )

    events = collect_turn(conversation, 'Apply both required edits')

    failed_result = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'later-failed-edit'
    )
    assert failed_result.error is not None
    assert failed_result.error.code == 'text_not_found'
    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert completed.result.model_calls == 3
    assert completed.result.changed_paths == ('sample.txt',)
    assert '[Failed Mutation Recovery]' in client.calls[1]['system']
    assert 'text_not_found' in client.calls[1]['system']
    assert 'Edit Recovery rejected the prose response' in str(
        client.calls[2]['messages']
    )
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'


def test_one_premature_recovery_summary_may_resume_corrected_edit(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    failed_edit = ToolCall(
        0,
        'prose-recovery-failed-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'missing\n',
            'new_text': 'new\n',
        },
    )
    corrected_edit = ToolCall(
        0,
        'prose-recovery-corrected-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(failed_edit),
        text_response('I cannot continue because verification is unavailable.'),
        response_with_tool(corrected_edit),
        finish_response(
            'prose-recovery-finish',
            task_kind='change',
            summary='Corrected sample.txt.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
    )

    events = collect_turn(conversation, 'Change sample.txt')

    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'completed'
    assert 'Edit Recovery rejected the prose response' in str(
        client.calls[2]['messages']
    )
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'


def test_cli_fix_intent_can_edit_after_bounded_novel_reads(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    targets = [
        tmp_path / 'play' / 'js' / f'file-{index}.js'
        for index in range(1, 4)
    ]
    for index, target in enumerate(targets, start=1):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f'old-{index}\n', encoding='utf-8')
    subprocess.run(['git', 'add', '.'], cwd=tmp_path, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'game baseline'],
        cwd=tmp_path,
        check=True,
    )
    reads = [
        ToolCall(
            0,
            f'novel-read-{index}',
            'read_file',
            {'path': f'play/js/file-{index}.js'},
        )
        for index in range(1, 4)
    ]
    edit = ToolCall(
        0,
        'novel-read-edit',
        'replace_text',
        {
            'path': 'play/js/file-1.js',
            'old_text': 'old-1\n',
            'new_text': 'fixed-1\n',
        },
    )
    verify = ToolCall(
        0,
        'novel-read-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    client = FakeModelClient(
        *(response_with_tool(call) for call in reads),
        response_with_tool(edit),
        response_with_tool(verify),
        finish_response(
            'novel-read-finish',
            task_kind='change',
            summary='Fixed the rendering code after bounded discovery.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        stagnation_warning=20,
        stagnation_limit=30,
    )

    events = collect_turn(
        conversation,
        '当前游戏很多方块只有一两面材质，帮我修复一下',
    )

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.model_calls == 6
    assert completed.result.changed_paths == ('play/js/file-1.js',)
    assert all(
        any(
            isinstance(event, ToolExecutionCompleted)
            and event.tool_call.id == read.id
            and event.result.success
            for event in events
        )
        for read in reads
    )
    assert '[ForgeCode Action Recovery]' not in (
        client.calls[3]['system'] or ''
    )
    available_names = {
        str(definition['name'])
        for definition in client.calls[3]['tools'] or ()
    }
    assert 'apply_patch' in available_names
    assert 'replace_text' in available_names
    assert 'write_file_chunk' not in available_names
    assert 'find_files' in available_names
    assert 'list_directory' in available_names


def test_reverted_process_batch_does_not_hide_later_real_edit(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    registry = create_default_registry(tmp_path)
    registry.register(ProcessModifyTool(tmp_path))
    registry.register(ProcessRevertTool(tmp_path))
    transient_batch = response_with_tools(
        ToolCall(0, 'process-modify', 'process_modify', {}),
        ToolCall(1, 'process-revert', 'process_revert', {}),
    )
    edit = ToolCall(
        0,
        'process-revert-real-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'process-revert-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    client = FakeModelClient(
        transient_batch,
        response_with_tool(edit),
        response_with_tool(verify),
        finish_response(
            'process-revert-finish',
            task_kind='change',
            summary='Created a persistent change after the reverted batch.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=registry,
    )

    events = collect_turn(conversation, 'Fix sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.changed_paths == ('sample.txt',)
    revisions = [
        event.revision
        for event in events
        if isinstance(event, WorkspaceChanged)
    ]
    assert revisions[:2] == [1, 2]
    assert '[ForgeCode Action Recovery]' not in (
        client.calls[1]['system'] or ''
    )


def test_normal_discovery_batch_does_not_enter_a_forced_edit_phase(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    initial = ToolCall(
        0,
        'read-limit-initial',
        'read_file',
        {'path': 'sample.txt'},
    )
    first_recovery_read = ToolCall(
        0,
        'read-limit-first',
        'read_file',
        {'path': 'sample.txt', 'start_line': 1, 'end_line': 1},
    )
    second_recovery_read = ToolCall(
        1,
        'read-limit-second',
        'grep',
        {'path': 'sample.txt', 'pattern': 'old'},
    )
    edit = ToolCall(
        0,
        'read-limit-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'read-limit-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    client = FakeModelClient(
        response_with_tool(initial),
        response_with_tools(first_recovery_read, second_recovery_read),
        response_with_tool(edit),
        response_with_tool(verify),
        finish_response(
            'read-limit-finish',
            task_kind='change',
            summary='Edited after one bounded recovery read.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, 'Fix sample.txt')

    second_result = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == second_recovery_read.id
    )
    assert second_result.success is True
    post_read_names = {
        str(definition['name'])
        for definition in client.calls[2]['tools'] or ()
    }
    assert 'read_file' in post_read_names
    assert 'grep' in post_read_names
    assert 'apply_patch' in post_read_names
    assert 'replace_text' in post_read_names
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'completed'


def test_preexisting_untracked_file_does_not_satisfy_turn_change(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    world = tmp_path / 'play' / 'js' / 'world.js'
    world.parent.mkdir(parents=True)
    world.write_text('const faceMode = buggy;\n', encoding='utf-8')
    inspect = ToolCall(
        0,
        'untracked-inspect',
        'git_diff',
        {'path': 'play/js/world.js'},
    )
    edit = ToolCall(
        0,
        'untracked-edit',
        'replace_text',
        {
            'path': 'play/js/world.js',
            'old_text': 'const faceMode = buggy;\n',
            'new_text': 'const faceMode = sixSided;\n',
        },
    )
    verify = ToolCall(
        0,
        'untracked-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    summary = 'Fixed and verified the preexisting untracked game file.'
    client = FakeModelClient(
        response_with_tool(inspect),
        finish_response('untracked-early-finish', task_kind='change'),
        response_with_tool(edit),
        response_with_tool(verify),
        finish_response(
            'untracked-finish',
            task_kind='change',
            summary=summary,
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(
        conversation,
        '当前游戏很多方块只有一两面材质，帮我修复一下',
    )

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert completed.result.changed_paths == ()
    assert completed.result.verification is None
    assert len(client.calls) == 2
    assert world.read_text(encoding='utf-8') == (
        'const faceMode = buggy;\n'
    )
    inspect_event = next(
        event
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == inspect.id
    )
    assert inspect_event.result.metadata['synthetic_diff'] is True
    early_finish = next(
        event
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'untracked-early-finish'
    )
    assert early_finish.result.success is False
    assert early_finish.result.error is not None
    assert early_finish.result.error.code == 'finish_rejected'
    assert all(
        '[ForgeCode Action Recovery]' not in (call['system'] or '')
        for call in client.calls
    )


def test_inspection_stagnation_stops_without_action_recovery(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    investigation = read_only_stagnation_calls('inspection')
    summary = 'sample.txt contains the old baseline value.'
    client = FakeModelClient(
        *(response_with_tool(call) for call in investigation[:8]),
        text_response(summary),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, 'Inspect and explain sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.model_calls == 9
    assert completed.result.text == summary
    assert completed.result.changed_paths == ()
    assert client.responses == []
    assert client.calls[-1]['tools'] is None
    assert 'read-only synthesis checkpoint' in str(
        client.calls[-1]['messages']
    )
    assert all(
        '[ForgeCode Action Recovery]' not in (
            (call['system'] or '') + str(call['messages'])
        )
        for call in client.calls
    )


def test_explicit_verification_request_does_not_interrupt_implementation(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    edit = ToolCall(
        0,
        'verification-recovery-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'intermediate\n',
        },
    )
    quick_verify = ToolCall(
        0,
        'verification-quick-check',
        'verify',
        {'command': 'python -c "print(1)"'},
    )
    second_edit = ToolCall(
        0,
        'verification-second-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'intermediate\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'verification-recovery-verify',
        'verify',
        {'command': 'python -m pytest --version'},
    )
    client = FakeModelClient(
        response_with_tool(edit),
        response_with_tool(quick_verify),
        response_with_tool(second_edit),
        response_with_tool(verify),
        finish_response(
            'verification-recovery-finish',
            task_kind='change',
            summary='Changed and verified sample.txt.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
    )

    events = collect_turn(
        conversation,
        'Change sample.txt and run focused tests.',
    )

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.verification is not None
    assert completed.result.verification.success is True
    available_names = {
        str(definition['name'])
        for definition in client.calls[1]['tools'] or ()
    }
    assert {'read_file', 'apply_patch', 'verify'} <= available_names
    assert '[ForgeCode Verification Recovery]' not in (
        client.calls[1]['system'] or ''
    )
    after_quick_check_names = {
        str(definition['name'])
        for definition in client.calls[2]['tools'] or ()
    }
    assert {'read_file', 'apply_patch', 'verify'} <= after_quick_check_names
    assert '[ForgeCode Verification Recovery]' not in (
        client.calls[2]['system'] or ''
    )


def test_failed_verification_enters_bounded_edit_recovery(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    first_edit = ToolCall(
        0,
        'failed-verify-first-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'broken\n',
        },
    )
    failed_verify = ToolCall(
        0,
        'failed-verify-run',
        'verify',
        {'command': 'python -c "raise SystemExit(1)"'},
    )
    targeted_read = ToolCall(
        0,
        'failed-verify-read',
        'read_file',
        {'path': 'sample.txt', 'start_line': 1, 'end_line': 1},
    )
    redundant_read = ToolCall(
        1,
        'failed-verify-redundant-read',
        'grep',
        {'path': 'sample.txt', 'pattern': 'broken'},
    )
    stale_verify = ToolCall(
        2,
        'failed-verify-stale-verify',
        'verify',
        {'command': 'python -c "print(1)"'},
    )
    corrected_edit = ToolCall(
        0,
        'failed-verify-corrected-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'broken\n',
            'new_text': 'new\n',
        },
    )
    passed_verify = ToolCall(
        0,
        'failed-verify-passed',
        'verify',
        {'command': 'python -m pytest --version'},
    )
    client = FakeModelClient(
        response_with_tool(first_edit),
        response_with_tool(failed_verify),
        response_with_tools(targeted_read, redundant_read, stale_verify),
        response_with_tool(corrected_edit),
        response_with_tool(passed_verify),
        finish_response(
            'failed-verify-finish',
            task_kind='change',
            summary='Corrected sample.txt after the failed test.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
    )

    events = collect_turn(
        conversation,
        'Change sample.txt and run focused tests.',
    )

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'
    redundant_result = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'failed-verify-redundant-read'
    )
    assert redundant_result.success is True
    stale_verify_result = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'failed-verify-stale-verify'
    )
    assert stale_verify_result.success is False
    assert stale_verify_result.error is not None
    assert stale_verify_result.error.code == 'verification_requires_correction'
    assert stale_verify_result.metadata['verification_retry_blocked'] is True
    recovery_names = {
        str(definition['name'])
        for definition in client.calls[2]['tools'] or ()
    }
    assert {'read_file', 'grep', 'apply_patch', 'verify'} <= recovery_names
    recovery_messages = client.calls[2]['messages']
    assert 'ForgeCode verification checkpoint' in str(
        recovery_messages[-1]['content']
    )
    assert 'Choose the smallest useful inspection' in str(
        recovery_messages[-1]['content']
    )
    assert 'do not weaken existing tests' in str(
        recovery_messages[-1]['content']
    )
    post_read_names = {
        str(definition['name'])
        for definition in client.calls[3]['tools'] or ()
    }
    assert {'apply_patch', 'read_file', 'grep'} <= post_read_names


def test_completion_decision_default_is_bounded() -> None:
    conversation = Conversation(client=FakeModelClient(text_response('done')))

    assert conversation.completion_decision_limit == 3


def test_inferred_task_scope_does_not_create_a_hard_write_boundary(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    runtime = tmp_path / 'forge' / 'runtime'
    runtime.mkdir(parents=True)
    state_file = runtime / 'state.py'
    state_file.write_text('safe\n', encoding='utf-8')
    create_scope = ToolCall(
        0,
        'scope-create',
        'create_directory',
        {'path': 'play/src'},
    )
    wrong_edit = ToolCall(
        0,
        'scope-wrong-edit',
        'replace_text',
        {
            'path': 'forge/runtime/state.py',
            'old_text': 'safe\n',
            'new_text': 'corrupted\n',
        },
    )
    correct_edit = ToolCall(
        0,
        'scope-correct-edit',
        'write_file',
        {'path': 'play/index.html', 'content': '<main>game</main>\n'},
    )
    client = FakeModelClient(
        response_with_tool(create_scope),
        response_with_tool(wrong_edit),
        response_with_tool(correct_edit),
        finish_response(
            'scope-finish',
            task_kind='change',
            summary='Created the game entry inside play.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, 'Build the game inside play')

    wrong_result = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'scope-wrong-edit'
    )
    assert wrong_result.success is True
    assert state_file.read_text(encoding='utf-8') == 'corrupted\n'
    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert set(completed.result.changed_paths) == {
        'forge/runtime/state.py',
        'play/index.html',
        'play/src',
    }


def test_explicit_policy_scope_overrides_narrow_planned_scope(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    blocked_file = tmp_path / 'blocked.txt'
    blocked_file.write_text('safe\n', encoding='utf-8')
    task = ActiveTask(
        id='task-plan-scope01',
        goal='Fix the implementation in sample.txt',
        status='in_progress',
        requires_change=True,
        planned=True,
        scope_hints=('**/*clamp*',),
        scope_source='planned',
    )
    forbidden_edit = ToolCall(
        0,
        'policy-scope-forbidden',
        'replace_text',
        {
            'path': 'blocked.txt',
            'old_text': 'safe\n',
            'new_text': 'corrupted\n',
        },
    )
    edit = ToolCall(
        0,
        'policy-scope-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'fixed\n',
        },
    )
    verify = ToolCall(
        0,
        'policy-scope-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    client = FakeModelClient(
        response_with_tool(forbidden_edit),
        response_with_tool(edit),
        response_with_tool(verify),
        finish_response(
            'policy-scope-finish',
            task_kind='change',
            summary='Fixed the explicitly allowed implementation file.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(
            require_changes=True,
            require_verification=True,
            allowed_paths=('sample.txt', 'blocked.txt'),
            forbidden_paths=('blocked.txt',),
        ),
        active_task=task,
    )

    events = collect_turn(conversation, 'Continue the planned fix.')

    forbidden_result = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'policy-scope-forbidden'
    )
    edit_result = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'policy-scope-edit'
    )
    completed = events[-1]
    assert forbidden_result.error is not None
    assert forbidden_result.error.code == 'outside_task_scope'
    assert blocked_file.read_text(encoding='utf-8') == 'safe\n'
    assert edit_result.success is True
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.changed_paths == ('sample.txt',)
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'fixed\n'


def test_runtime_tells_model_that_request_tools_are_available(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    client = FakeModelClient(
        text_response('I will decide how to proceed.'),
        finish_response(
            'finish_answer',
            task_kind='answer',
            summary='I decided to answer.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, 'Describe the tools in this request')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert len(client.calls) == 1
    assert 'tools included with this model request are currently available' in (
        client.calls[0]['system'] or ''
    )


def test_malformed_tool_arguments_recover_without_pausing_tools(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    malformed = ToolCall(
        0,
        'toolu_bad_list',
        'list_directory',
        {'path': '.', '}}{': '?'},
    )
    corrected = ToolCall(
        0,
        'toolu_good_list',
        'list_directory',
        {'path': '.'},
    )
    client = FakeModelClient(
        response_with_tool(malformed),
        response_with_tool(corrected),
        text_response('Inspected the repository root.'),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        stagnation_warning=1,
        stagnation_limit=3,
    )

    events = collect_turn(conversation, 'Inspect the repository')

    tool_events = [
        event for event in events
        if isinstance(event, ToolExecutionCompleted)
    ]
    assert tool_events[0].result.error is not None
    assert tool_events[0].result.error.code == 'invalid_arguments'
    assert tool_events[1].result.success is True
    assert all(call['tools'] is not None for call in client.calls)
    assert all(
        'Repository action tools are paused' not in (call['system'] or '')
        for call in client.calls
    )
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'completed'


def test_edit_protocol_recovery_preempts_stale_stagnation(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    first_read = ToolCall(
        0,
        'stale-protocol-read',
        'read_file',
        {'path': 'sample.txt'},
    )
    second_read = ToolCall(
        0,
        'stale-protocol-grep',
        'grep',
        {'path': 'sample.txt', 'pattern': 'old'},
    )
    malformed_edit = ToolCall(
        0,
        'stale-protocol-bad-edit',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Update File: sample.txt\n'
                '@@\n'
                '- 1 | old\n'
                '+new\n'
                '*** End Patch'
            )
        },
    )
    corrected_edit = ToolCall(
        0,
        'stale-protocol-good-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(first_read),
        response_with_tool(second_read),
        response_with_tool(malformed_edit),
        response_with_tool(corrected_edit),
        finish_response(
            'stale-protocol-finish',
            task_kind='change',
            summary='Changed sample.txt.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
        stagnation_warning=1,
        stagnation_limit=2,
        change_exploration_limit=4,
    )

    events = collect_turn(conversation, 'Change sample.txt')

    malformed_result = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == malformed_edit.id
    )
    assert malformed_result.error is not None
    assert malformed_result.error.code == 'patch_contains_read_line_numbers'
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'completed'
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'


def test_invalid_grep_regex_recovers_as_tool_protocol_failure(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    invalid = ToolCall(
        0,
        'toolu_invalid_regex',
        'grep',
        {'path': 'sample.txt', 'pattern': 'old('},
    )
    corrected = ToolCall(
        0,
        'toolu_literal_search',
        'grep',
        {'path': 'sample.txt', 'pattern': 'old', 'regex': False},
    )
    client = FakeModelClient(
        response_with_tool(invalid),
        response_with_tool(corrected),
        finish_response(
            'toolu_regex_finish',
            task_kind='inspection',
            summary='Found the literal text.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, 'Inspect sample.txt for literal text')

    tool_events = [
        event
        for event in events
        if isinstance(event, ToolExecutionCompleted)
    ]
    assert tool_events[0].result.success is False
    assert tool_events[0].result.error is not None
    assert tool_events[0].result.error.code == 'invalid_pattern'
    assert tool_events[1].result.success is True
    assert all(call['tools'] is not None for call in client.calls)
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'completed'


def test_inspection_finish_without_evidence_is_rejected_once(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    read = ToolCall(
        0,
        'toolu_inspect_read',
        'read_file',
        {'path': 'sample.txt'},
    )
    client = FakeModelClient(
        finish_response('finish_without_evidence', task_kind='inspection'),
        response_with_tool(read),
        finish_response(
            'finish_with_evidence',
            task_kind='inspection',
            summary='sample.txt contains the inspected value.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, 'Inspect sample.txt')

    blocks = [event for event in events if isinstance(event, CompletionBlocked)]
    assert len(blocks) == 1
    assert 'requires repository evidence' in blocks[0].reasons[0]
    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert len(client.calls) == 1
    assert len(client.responses) == 2
    assert 'requires repository evidence' in (
        completed.result.completion_reasons[0]
    )


def test_finish_task_must_be_called_alone(tmp_path: Path) -> None:
    initialize_git_repository(tmp_path)
    read = ToolCall(
        0,
        'toolu_mixed_read',
        'read_file',
        {'path': 'sample.txt'},
    )
    finish = ToolCall(
        1,
        'toolu_mixed_finish',
        'finish_task',
        {
            'task_kind': 'inspection',
            'status': 'completed',
            'summary': 'Inspected.',
            'blocked_reasons': [],
        },
    )
    mixed_response = [
        ModelUsageUpdate(usage=TokenUsage(10, 0)),
        ModelToolCallCompleted(tool_call=read),
        ModelToolCallCompleted(tool_call=finish),
        ModelUsageUpdate(usage=TokenUsage(10, 2)),
    ]
    client = FakeModelClient(
        mixed_response,
        finish_response(
            'toolu_finish_alone',
            task_kind='inspection',
            summary='Inspected sample.txt.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, 'Inspect sample.txt')

    tool_events = [
        event for event in events
        if isinstance(event, ToolExecutionCompleted)
    ]
    mixed_finish = next(
        event for event in tool_events
        if event.tool_call.id == 'toolu_mixed_finish'
    )
    assert mixed_finish.result.success is False
    assert mixed_finish.result.error is not None
    assert mixed_finish.result.error.code == 'finish_must_be_alone'
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'completed'


def test_agent_loop_stops_after_one_completion_rejection(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    edit = ToolCall(
        0,
        'toolu_edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(edit),
        finish_response('finish_once', task_kind='change'),
        finish_response('finish_twice', task_kind='change'),
        finish_response('finish_three', task_kind='change'),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_verification=True),
    )
    events = collect_turn(conversation, 'Change sample.txt')

    blocks = [item for item in events if isinstance(item, CompletionBlocked)]
    assert [item.attempt for item in blocks] == [1]
    assert len(client.calls) == 2
    assert len(client.responses) == 2
    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert completed.result.completion_reasons
    assert conversation.task_manager.active is not None
    assert conversation.task_manager.active.status == 'stuck'


def test_verified_revision_can_finish_with_incomplete_plan(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    plan = ToolCall(
        0,
        'toolu_plan',
        'task_plan',
        {'steps': ['Implement the fix', 'Verify the final revision']},
    )
    edit = ToolCall(
        0,
        'toolu_plan_edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'toolu_plan_verify',
        'verify',
        {'command': 'git diff --check'},
    )
    client = FakeModelClient(
        response_with_tool(plan),
        response_with_tool(edit),
        response_with_tool(verify),
        finish_response('toolu_plan_finish', task_kind='change'),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(
            require_changes=True,
            require_verification=True,
        ),
    )

    events = collect_turn(conversation, 'Change and verify sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    task = conversation.task_manager.active
    assert task is not None
    assert all(step.status == 'completed' for step in task.steps)
    assert len(client.calls) == 4


def test_planned_progress_keeps_targeted_read_and_action_tools(
    tmp_path: Path,
) -> None:
    conversation = Conversation(
        client=FakeModelClient(),
        registry=create_default_registry(tmp_path),
    )

    names = {
        str(definition['name'])
        for definition in conversation._planned_progress_tools() or []
    }

    assert {'read_file', 'apply_patch', 'task_update', 'verify', 'finish_task'} <= names
    assert 'task_plan' not in names


def test_false_blocker_gets_one_bounded_action_recovery(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    searches = [
        ToolCall(
            0,
            f'toolu_find_{index}',
            'find_files',
            {'path': '.', 'pattern': pattern},
        )
        for index, pattern in enumerate(('missing-a', 'missing-b'), start=1)
    ]
    edit = ToolCall(
        0,
        'toolu_recovery_edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'toolu_recovery_verify',
        'verify',
        {'command': 'git diff --check'},
    )
    client = FakeModelClient(
        *(response_with_tool(call) for call in searches),
        finish_response(
            'finish_blocked',
            task_kind='change',
            status='blocked',
            summary='I could not complete the requested code change.',
            blocked_reasons=['No applicable source evidence was found.'],
        ),
        response_with_tool(edit),
        response_with_tool(verify),
        finish_response('finish_wrong_kind', task_kind='inspection'),
        finish_response('finish_recovered', task_kind='change'),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(
            require_changes=True,
            require_verification=True,
        ),
        stagnation_warning=2,
        stagnation_limit=4,
    )

    events = collect_turn(conversation, 'Change and verify the game')

    finish_event = next(
        event
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'finish_blocked'
    )
    assert finish_event.result.error is not None
    assert finish_event.result.error.code == 'finish_rejected'
    recovery_tools = {
        str(tool['name']) for tool in client.calls[3]['tools'] or []
    }
    assert 'replace_text' in recovery_tools
    assert 'read_file' not in recovery_tools
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'
    wrong_kind = next(
        event
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'finish_wrong_kind'
    )
    assert wrong_kind.result.error is not None
    assert wrong_kind.result.error.code == 'finish_rejected'
    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert len(client.calls) == 7


def test_empty_recovery_response_returns_stuck_turn(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    searches = [
        ToolCall(
            0,
            f'toolu_empty_{index}',
            'find_files',
            {'path': '.', 'pattern': pattern},
        )
        for index, pattern in enumerate(('none-a', 'none-b'), start=1)
    ]
    client = FakeModelClient(
        *(response_with_tool(call) for call in searches),
        [ModelUsageUpdate(usage=TokenUsage(10, 0))],
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        stagnation_warning=2,
        stagnation_limit=4,
    )

    events = collect_turn(conversation, 'Inspect missing files')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert 'no usable answer' in completed.result.text
    assert len(client.calls) == 3


def test_empty_response_after_completion_rejection_is_stuck(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    edit = ToolCall(
        0,
        'toolu_edit_empty',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(edit),
        finish_response('finish_unverified', task_kind='change'),
        [ModelUsageUpdate(usage=TokenUsage(10, 0))],
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_verification=True),
    )

    events = collect_turn(conversation, 'Change and verify sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert any(
        'has not been verified' in reason
        for reason in completed.result.completion_reasons
    )
    assert len(client.calls) == 2
    assert len(client.responses) == 1


def test_cleanup_task_can_delete_placeholder_files_end_to_end(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    placeholder_files = (
        'play/notes.txt',
        'play/test.txt',
        'play/.tmp/diff.txt',
        'play/src/.tmp/main_inject.js',
    )
    for relative_path in placeholder_files:
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('obsolete\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'play'], cwd=tmp_path, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'add obsolete files'],
        cwd=tmp_path,
        check=True,
    )
    cleanup = ToolCall(
        0,
        'cleanup-obsolete-files',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                + ''.join(
                    f'*** Delete File: {path}\n'
                    for path in placeholder_files
                )
                + '*** End Patch'
            )
        },
    )
    client = FakeModelClient(
        response_with_tool(cleanup),
        finish_response(
            'cleanup-finish',
            task_kind='change',
            summary='Removed obsolete files from play.',
        ),
    )
    router = AsyncMock()
    router.route.return_value = RouteResult(
        decision=TurnDecision(
            intent='change_task',
            task_relation='new',
            requires_workspace_change=True,
            confidence=0.99,
            reason='The user explicitly authorized cleanup deletion.',
        ),
        usage=TokenUsage(7, 3),
    )
    permission_manager = PermissionManager(
        tmp_path,
        approval_handler=StaticApprovalHandler('allow_once'),
        user_path=tmp_path / 'missing-user-permissions.json',
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        intent_router=router,
        permission_manager=permission_manager,
    )

    events = collect_turn(
        conversation,
        '帮我对play目录进行优化一下，里面是不是有些文件没用可以删除了',
    )

    cleanup_result = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'cleanup-obsolete-files'
    )
    completed = events[-1]
    assert cleanup_result.success is True
    assert all(not (tmp_path / path).exists() for path in placeholder_files)
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert set(completed.result.changed_paths) == set(placeholder_files)
    assert 'without a workspace change' not in completed.result.text


def test_directory_patch_failure_recovers_with_remove_directory(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    obsolete = {
        'play/.keep/README.md': 'keep\n',
        'play/.tmp/diff.txt': 'diff\n',
        'play/notes.txt': 'notes\n',
    }
    for relative_path, content in obsolete.items():
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    subprocess.run(['git', 'add', 'play'], cwd=tmp_path, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'add cleanup targets'],
        cwd=tmp_path,
        check=True,
    )
    directory_patch = ToolCall(
        0,
        'wrong-directory-patch',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Delete File: play/.keep\n'
                '*** Delete File: play/.tmp\n'
                '*** End Patch'
            )
        },
    )
    remove_keep = ToolCall(
        0,
        'remove-keep-directory',
        'remove_directory',
        {'path': 'play/.keep', 'recursive': True},
    )
    remove_tmp = ToolCall(
        0,
        'remove-tmp-directory',
        'remove_directory',
        {'path': 'play/.tmp', 'recursive': True},
    )
    remove_notes = ToolCall(
        0,
        'remove-notes-file',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Delete File: play/notes.txt\n'
                '*** End Patch'
            )
        },
    )
    client = FakeModelClient(
        response_with_tool(directory_patch),
        response_with_tool(remove_keep),
        response_with_tool(remove_tmp),
        response_with_tool(remove_notes),
        finish_response(
            'directory-cleanup-finish',
            task_kind='change',
            summary='Removed obsolete play directories and files.',
        ),
    )
    router = AsyncMock()
    router.route.return_value = RouteResult(
        decision=TurnDecision(
            intent='continue_task',
            task_relation='active',
            requires_workspace_change=True,
            confidence=0.99,
            reason='Continue the explicitly authorized cleanup task.',
        ),
        usage=TokenUsage(7, 3),
    )
    permission_manager = PermissionManager(
        tmp_path,
        approval_handler=StaticApprovalHandler('allow_once'),
        user_path=tmp_path / 'missing-user-permissions.json',
    )
    active_task = ActiveTask(
        id='task-cleanupdirs',
        goal='优化 play 目录并删除无用文件和目录',
        status='stuck',
        requires_change=True,
        scope_hints=('play/**',),
    )
    checkpoint_store = CheckpointStore(
        tmp_path,
        tmp_path.parent / f'{tmp_path.name}-checkpoints',
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        active_task=active_task,
        intent_router=router,
        permission_manager=permission_manager,
        checkpoint_store=checkpoint_store,
    )

    events = collect_turn(conversation, '继续帮我完成任务')

    failed_patch = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'wrong-directory-patch'
    )
    recovery_tool_names = {
        str(tool.get('name', ''))
        for tool in client.calls[1]['tools']
    }
    completed = events[-1]
    assert failed_patch.success is False
    assert failed_patch.error is not None
    assert failed_patch.error.code == 'directory_patch_target'
    assert failed_patch.error.details['recommended_tool'] == 'remove_directory'
    assert {'remove_directory', 'read_file', 'list_directory'} <= recovery_tool_names
    assert not (tmp_path / 'play' / '.keep').exists()
    assert not (tmp_path / 'play' / '.tmp').exists()
    assert not (tmp_path / 'play' / 'notes.txt').exists()
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert len(client.calls) == 5
    assert 'failure limit' not in completed.result.text

    checkpoint_id = checkpoint_store.latest_restorable()
    assert checkpoint_id is not None
    checkpoint_store.restore(checkpoint_id)
    assert (tmp_path / 'play' / '.keep' / 'README.md').read_text(
        encoding='utf-8'
    ) == 'keep\n'
    assert (tmp_path / 'play' / '.tmp' / 'diff.txt').read_text(
        encoding='utf-8'
    ) == 'diff\n'
    assert (tmp_path / 'play' / 'notes.txt').read_text(
        encoding='utf-8'
    ) == 'notes\n'


def test_oversized_write_exposes_chunk_fallback_on_first_failure(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    oversized = ToolCall(
        0,
        'oversized-new-file',
        'write_file',
        {'path': 'large.js', 'content': 'x' * 30_001},
    )
    unrelated_read = ToolCall(
        0,
        'chunk-fallback-read',
        'read_file',
        {'path': 'sample.txt'},
    )
    chunk = ToolCall(
        0,
        'chunked-new-file',
        'write_file_chunk',
        {
            'path': 'large.js',
            'content': 'const ready = true;\n',
            'offset': 0,
            'truncate': True,
            'final': True,
        },
    )
    client = FakeModelClient(
        response_with_tool(oversized),
        response_with_tool(unrelated_read),
        response_with_tool(chunk),
        text_response('Created large.js with the chunk fallback.'),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, '帮我创建 large.js')

    failed = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'oversized-new-file'
    )
    recovery_names = {
        str(definition['name'])
        for definition in client.calls[1]['tools'] or ()
    }
    rejected_read = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'chunk-fallback-read'
    )
    completed = events[-1]
    assert failed.success is False
    assert failed.error is not None
    assert failed.error.code == 'invalid_arguments'
    assert {'write_file_chunk', 'read_file'} <= recovery_names
    assert rejected_read.success is True
    assert (tmp_path / 'large.js').read_text(encoding='utf-8') == (
        'const ready = true;\n'
    )
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
