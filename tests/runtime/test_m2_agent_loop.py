'''Integration tests for the M2 model-tool-verification loop.'''

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
import subprocess
from typing import Any
from unittest.mock import AsyncMock

from forge.permissions.approval import StaticApprovalHandler
from forge.permissions.policy import PermissionManager
from forge.runtime.agent_loop import (
    Conversation,
    build_final_acceptance_audit_feedback,
    completion_review_paths,
    is_placeholder_mutation,
    is_substantive_mutation,
    is_test_file_path,
    mutation_path_operations,
    mutation_targets_overlap,
    placeholder_only_implementation,
    render_completion_ready_context,
)
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


def test_recovery_placeholder_mutations_are_rejected_unless_requested() -> None:
    marker = ToolCall(
        0,
        'marker',
        'write_file',
        {'path': 'play/.touch', 'content': 'noop'},
    )

    assert is_placeholder_mutation(marker, '升级 play 中的游戏') is True
    assert is_placeholder_mutation(marker, '创建 play/.touch 文件') is False
    assert is_placeholder_mutation(
        ToolCall(
            0,
            'tmp-json',
            'write_file',
            {'path': 'play/tmp2.json', 'content': '{}'},
        ),
        '升级 play 中的游戏',
    ) is True
    assert is_placeholder_mutation(
        ToolCall(
            0,
            'progress-marker',
            'write_file',
            {'path': 'play/notes.txt', 'content': 'progress marker'},
        ),
        '升级 play 中的游戏',
    ) is True
    assert is_placeholder_mutation(
        ToolCall(
            0,
            'bracket-placeholder',
            'write_file',
            {'path': 'play/proposal.txt', 'content': '[placeholder]'},
        ),
        '升级 play 中的游戏',
    ) is True
    assert is_placeholder_mutation(
        ToolCall(
            0,
            'plan-placeholder',
            'write_file',
            {'path': 'play/notes.txt', 'content': 'plan placeholder'},
        ),
        '升级 play 中的游戏',
    ) is True
    assert is_placeholder_mutation(
        ToolCall(
            0,
            'tmp-probe-patch',
            'apply_patch',
            {
                'patch': (
                    '*** Begin Patch\n'
                    '*** Update File: play/.tmp_probe.txt\n'
                    '@@\n-test\n+probe\n'
                    '*** End Patch'
                )
            },
        ),
        '升级 play 中的游戏',
    ) is True
    assert is_placeholder_mutation(
        ToolCall(
            0,
            'readme-tmp',
            'write_file',
            {'path': 'play/README.tmp', 'content': 'init'},
        ),
        '升级 play 中的游戏',
    ) is True
    assert is_placeholder_mutation(
        ToolCall(
            0,
            'dot-tmpcheck',
            'write_file',
            {'path': 'play/src/.tmpcheck', 'content': 'init'},
        ),
        '升级 play 中的游戏',
    ) is True
    assert is_placeholder_mutation(
        ToolCall(
            0,
            'continue-marker',
            'write_file',
            {'path': 'play/src/notes.txt', 'content': 'continue marker'},
        ),
        '升级 play 中的游戏',
    ) is True
    assert is_placeholder_mutation(
        ToolCall(
            0,
            'trivial-smoke-test',
            'write_file',
            {
                'path': 'play/tests/pvz.test.js',
                'content': (
                    "import { test } from 'node:test';\n"
                    "import assert from 'node:assert/strict';\n"
                    "test('project smoke check', () => {\n"
                    "  assert.equal(typeof process.cwd(), 'string');\n"
                    "});\n"
                ),
            },
        ),
        '升级 play 中的游戏',
    ) is True
    assert is_placeholder_mutation(
        ToolCall(
            0,
            'marker-js',
            'write_file',
            {'path': 'play/src/.tmp/inject.js', 'content': '// marker'},
        ),
        '升级 play 中的游戏',
    ) is True
    directory = ToolCall(
        0,
        'assets-directory',
        'create_directory',
        {'path': 'play/src/assets'},
    )
    assert is_substantive_mutation(directory, '升级 play 中的游戏') is False
    assert is_substantive_mutation(
        directory,
        '创建 play/src/assets 目录',
    ) is True


def test_unrelated_success_does_not_resolve_failed_mutation_target() -> None:
    assert mutation_targets_overlap(
        ('play/index.html',),
        ('play/game.js',),
    ) is False
    assert mutation_targets_overlap(
        ('play/game.js',),
        ('play/game.js',),
    ) is True
    assert mutation_targets_overlap(
        ('play/src/main.js',),
        ('play/src/.gitkeep',),
    ) is True


def test_placeholder_cleanup_deletions_are_not_rejected() -> None:
    delete_patch = ToolCall(
        0,
        'cleanup-placeholders',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Delete File: play/notes.txt\n'
                '*** Delete File: play/test.txt\n'
                '*** Delete File: play/.tmp/diff.txt\n'
                '*** Delete File: play/src/.tmp/main_inject.js\n'
                '*** End Patch'
            )
        },
    )

    assert mutation_path_operations(delete_patch) == (
        ('delete', 'play/notes.txt'),
        ('delete', 'play/test.txt'),
        ('delete', 'play/.tmp/diff.txt'),
        ('delete', 'play/src/.tmp/main_inject.js'),
    )
    assert is_placeholder_mutation(
        delete_patch,
        '帮我优化 play 目录，把没用的文件删除',
    ) is False
    assert is_substantive_mutation(
        delete_patch,
        '帮我优化 play 目录，把没用的文件删除',
    ) is True


def test_unified_diff_placeholder_deletion_is_not_rejected() -> None:
    delete_patch = ToolCall(
        0,
        'cleanup-unified-placeholder',
        'apply_patch',
        {
            'patch': (
                '--- a/play/test.txt\n'
                '+++ /dev/null\n'
                '@@ -1 +0,0 @@\n'
                '-test\n'
            )
        },
    )

    assert mutation_path_operations(delete_patch) == (
        ('delete', 'play/test.txt'),
    )
    assert is_placeholder_mutation(delete_patch, '清理 play 目录') is False


def test_move_checks_destination_instead_of_deleted_source() -> None:
    cleanup_move = ToolCall(
        0,
        'move-away-from-placeholder',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Update File: play/test.txt\n'
                '*** Move to: play/docs/legacy-notes.txt\n'
                '@@\n'
                '-test\n'
                '+archived\n'
                '*** End Patch'
            )
        },
    )
    placeholder_move = ToolCall(
        0,
        'move-into-placeholder',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Update File: play/docs/legacy-notes.txt\n'
                '*** Move to: play/test.txt\n'
                '@@\n'
                '-archived\n'
                '+test\n'
                '*** End Patch'
            )
        },
    )

    assert mutation_path_operations(cleanup_move) == (
        ('delete', 'play/test.txt'),
        ('write', 'play/docs/legacy-notes.txt'),
    )
    assert is_placeholder_mutation(cleanup_move, '整理 play 目录') is False
    assert is_placeholder_mutation(placeholder_move, '整理 play 目录') is True


def test_mixed_patch_still_rejects_placeholder_production() -> None:
    mixed_patch = ToolCall(
        0,
        'mixed-placeholder-write',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Delete File: play/old.txt\n'
                '*** Add File: play/test.txt\n'
                '+noop\n'
                '*** End Patch'
            )
        },
    )

    assert mutation_path_operations(mixed_patch) == (
        ('delete', 'play/old.txt'),
        ('write', 'play/test.txt'),
    )
    assert is_placeholder_mutation(mixed_patch, '优化 play 目录') is True


def test_standard_cross_language_test_paths_are_recognized() -> None:
    assert is_test_file_path('src/test/java/example/ServiceTest.java')
    assert is_test_file_path('tests/unit/test_service.py')
    assert is_test_file_path('src/__tests__/service.test.ts')
    assert is_test_file_path('internal/service/service_test.go')
    assert is_test_file_path('Service.Tests/ServiceTests.cs')
    assert not is_test_file_path('src/latest/service.py')
    assert not is_test_file_path('src/contest/service.py')


def test_placeholder_only_diff_cannot_complete_implementation_request() -> None:
    changed = ('play/src/core/.gitkeep', 'play/src/config/.gitkeep')

    assert placeholder_only_implementation(
        '帮我在 play 目录实现一个高级版本的雷霆战机',
        changed,
    ) is True
    assert placeholder_only_implementation(
        '帮我创建一个 play 目录',
        ('play/.gitkeep',),
    ) is False


def test_final_acceptance_audit_is_high_priority_and_bounded() -> None:
    feedback = build_final_acceptance_audit_feedback()

    assert feedback['role'] == 'user'
    assert 'every clause of my original request' in feedback['content']
    assert 'credential-shaped URLs' in feedback['content']
    assert 'without more repository exploration' in feedback['content']


def test_completion_audit_persists_after_all_diff_paths_are_reviewed() -> None:
    context = render_completion_ready_context(
        ('forge/runtime/agent_loop.py',),
        None,
        4,
        80,
        {'forge/runtime/agent_loop.py'},
        require_diff_review=True,
    )

    assert 'unreviewed changed path' not in context
    assert 'Audit the final Diff against every clause' in context
    assert 'reset boundary' in context
    assert 'credential-shaped inputs' in context


def test_completion_review_requires_final_diff_page() -> None:
    call = ToolCall(0, 'paged-diff', 'git_diff', {'path': 'sample.txt'})
    partial = ToolResult.ok(
        'Read partial Git diff page for sample.txt.',
        content='diff page',
        metadata={
            'path': 'sample.txt',
            'paged_diff': True,
            'diff_complete': False,
        },
    )
    complete = ToolResult.ok(
        'Read final Git diff page for sample.txt.',
        content='final diff page',
        metadata={
            'path': 'sample.txt',
            'paged_diff': True,
            'diff_complete': True,
        },
    )

    assert completion_review_paths(
        [(call, partial)],
        ('sample.txt',),
    ) == set()
    assert completion_review_paths(
        [(call, complete)],
        ('sample.txt',),
    ) == {'sample.txt'}


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


def test_large_tested_change_delegates_initial_exploration(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    add_tracked_smoke_test(tmp_path)
    registry = create_default_registry(tmp_path)
    explore = StubExploreTool(tmp_path)
    registry.unregister('explore_repository')
    registry.register(explore)
    delegation = ToolCall(
        0,
        'large-task-explore',
        'explore_repository',
        {},
    )
    edit = ToolCall(
        0,
        'large-task-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'large-task-verify',
        'verify',
        {'command': 'python -m pytest -q'},
    )
    final_diff = ToolCall(
        0,
        'large-task-final-diff',
        'git_diff',
        {'path': 'sample.txt'},
    )
    focused_reads = (
        ToolCall(
            0,
            'large-task-read',
            'read_file',
            {'path': 'sample.txt'},
        ),
        ToolCall(
            1,
            'large-task-grep-old',
            'grep',
            {'path': 'sample.txt', 'pattern': 'old'},
        ),
        ToolCall(
            2,
            'large-task-grep-new',
            'grep',
            {'path': 'sample.txt', 'pattern': 'new'},
        ),
        ToolCall(
            3,
            'large-task-grep-lines',
            'grep',
            {'path': 'sample.txt', 'pattern': '^old$'},
        ),
        ToolCall(
            4,
            'large-task-grep-prefix',
            'grep',
            {'path': 'sample.txt', 'pattern': '^o'},
        ),
        ToolCall(
            5,
            'large-task-grep-suffix',
            'grep',
            {'path': 'sample.txt', 'pattern': 'd$'},
        ),
        ToolCall(
            6,
            'large-task-grep-any',
            'grep',
            {'path': 'sample.txt', 'pattern': '.+'},
        ),
        ToolCall(
            7,
            'large-task-grep-literal',
            'grep',
            {'path': 'sample.txt', 'pattern': 'ol'},
        ),
    )
    client = FakeModelClient(
        response_with_tool(delegation),
        response_with_tools(*focused_reads),
        response_with_tool(edit),
        response_with_tool(verify),
        response_with_tool(final_diff),
        finish_response(
            'large-task-finish',
            task_kind='change',
            summary='Implemented and tested the large change.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=registry,
        task_policy=TaskPolicy(require_changes=True),
    )
    prompt = (
        'Implement a production-quality cross-file change. '
        + 'Trace runtime and permission behavior carefully. ' * 20
        + 'Run focused tests and then the full test suite.'
    )

    events = collect_turn(conversation, prompt)

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed', (
        completed.result.completion_reasons
    )
    assert explore.calls == 1
    focused_read_results = [
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id.startswith('large-task-')
        and event.tool_call.name in {'read_file', 'grep'}
    ]
    assert len(focused_read_results) == 8
    assert all(result.success for result in focused_read_results)
    assert {
        definition['name'] for definition in client.calls[0]['tools'] or ()
    } == {'explore_repository'}
    post_explore_names = {
        definition['name'] for definition in client.calls[1]['tools'] or ()
    }
    assert 'ForgeCode Explore handoff' in str(client.calls[1]['messages'])
    assert {'read_file', 'grep', 'apply_patch', 'replace_text'} <= (
        post_explore_names
    )
    assert 'list_directory' in post_explore_names
    assert 'find_files' not in post_explore_names
    post_mutation_names = {
        definition['name'] for definition in client.calls[3]['tools'] or ()
    }
    assert {
        'read_file',
        'list_directory',
        'grep',
        'apply_patch',
        'replace_text',
        'verify',
    } <= post_mutation_names
    assert 'find_files' not in post_mutation_names
    assert 'run_command' not in post_mutation_names
    assert 'ForgeCode post-edit checkpoint' in str(
        client.calls[3]['messages']
    )
    assert 'ForgeCode large-task routing' in str(
        client.calls[0]['messages']
    )


def test_self_declared_incomplete_change_resumes_bounded_editing(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    add_tracked_smoke_test(tmp_path)
    partial_edit = ToolCall(
        0,
        'incomplete-partial-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'partial\n',
        },
    )
    corrected_edit = ToolCall(
        0,
        'incomplete-corrected-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'partial\n',
            'new_text': 'new\n',
        },
    )
    first_verify = ToolCall(
        0,
        'incomplete-first-verify',
        'verify',
        {'command': 'python -m pytest --version'},
    )
    final_verify = ToolCall(
        0,
        'incomplete-final-verify',
        'verify',
        {'command': 'python -m pytest -q'},
    )
    final_diff = ToolCall(
        0,
        'incomplete-final-diff',
        'git_diff',
        {'path': 'sample.txt'},
    )
    client = FakeModelClient(
        response_with_tool(partial_edit),
        response_with_tool(first_verify),
        text_response(
            'This revision does not implement the requested behavior yet. '
            'I did not run the full test suite.'
        ),
        response_with_tool(corrected_edit),
        response_with_tool(final_verify),
        response_with_tool(final_diff),
        finish_response(
            'incomplete-finish',
            task_kind='change',
            summary='Implemented the requested behavior and ran the tests.',
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
        'Change sample.txt and run the full test suite.',
    )

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed', (
        completed.result.completion_reasons
    )
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'
    recovery_names = {
        definition['name'] for definition in client.calls[3]['tools'] or ()
    }
    assert {
        'apply_patch',
        'replace_text',
        'verify',
    } <= recovery_names
    assert 'read_file' not in recovery_names
    assert 'list_directory' not in recovery_names
    assert 'grep' not in recovery_names
    assert 'find_files' not in recovery_names
    assert 'ForgeCode rejected completion' in str(
        client.calls[3]['messages']
    )


def test_incomplete_recovery_budget_resets_after_workspace_progress(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    edits = [
        ToolCall(
            0,
            f'incomplete-progress-edit-{index}',
            'replace_text',
            {
                'path': 'sample.txt',
                'old_text': f'revision-{index - 1}\n' if index > 1 else 'old\n',
                'new_text': f'revision-{index}\n',
            },
        )
        for index in range(1, 5)
    ]
    client = FakeModelClient(
        response_with_tool(edits[0]),
        text_response('尚未完成复杂版本。'),
        response_with_tool(edits[1]),
        text_response('尚未完成复杂版本。'),
        response_with_tool(edits[2]),
        text_response('尚未完成复杂版本。'),
        response_with_tool(edits[3]),
        finish_response(
            'incomplete-progress-finish',
            task_kind='change',
            summary='Implemented the complete requested behavior.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
    )

    events = collect_turn(conversation, 'Change sample.txt in four stages.')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed', (
        completed.result.completion_reasons
    )
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'revision-4\n'


def test_repeated_incomplete_declarations_without_progress_still_stop(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    edit = ToolCall(
        0,
        'same-revision-incomplete-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'partial\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(edit),
        text_response('尚未完成复杂版本。'),
        text_response('尚未完成复杂版本。'),
        text_response('尚未完成复杂版本。'),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
    )

    events = collect_turn(conversation, 'Change sample.txt completely.')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert 'repeatedly declared' in ' '.join(
        completed.result.completion_reasons
    )


def test_explicit_test_change_request_requires_a_test_file_diff(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    add_tracked_smoke_test(tmp_path)
    edit = ToolCall(
        0,
        'missing-test-change-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'missing-test-change-verify',
        'verify',
        {'command': 'python -m pytest -q'},
    )
    client = FakeModelClient(
        response_with_tool(edit),
        response_with_tool(verify),
        finish_response(
            'missing-test-change-finish',
            task_kind='change',
            summary='Changed sample.txt and ran existing tests.',
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
        'Change sample.txt, add focused regression tests, and run tests.',
    )

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert any(
        'task-local Diff contains no test file' in reason
        for reason in completed.result.completion_reasons
    )
    assert 'Test Change Contract' in str(client.calls[0]['system'])


def test_full_suite_completion_requires_final_diff_review(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    add_tracked_smoke_test(tmp_path)
    edit = ToolCall(
        0,
        'unreviewed-full-suite-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'unreviewed-full-suite-verify',
        'verify',
        {'command': 'python -m pytest -q'},
    )
    final_diff = ToolCall(
        0,
        'recovered-full-suite-diff',
        'git_diff',
        {'path': 'sample.txt'},
    )
    client = FakeModelClient(
        response_with_tool(edit),
        response_with_tool(verify),
        finish_response(
            'unreviewed-full-suite-finish',
            task_kind='change',
            summary='Changed sample.txt and ran the full suite.',
        ),
        response_with_tool(final_diff),
        finish_response(
            'reviewed-full-suite-finish',
            task_kind='change',
            summary='Reviewed sample.txt and ran the full suite.',
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
        'Change sample.txt and run the full test suite.',
    )

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert all(
        call.id != 'reviewed-full-suite-finish'
        for call in completed.result.tool_calls
    )
    blocked = [event for event in events if isinstance(event, CompletionBlocked)]
    assert any(
        'inspect the final Diff' in reason
        and 'sample.txt' in reason
        for event in blocked
        for reason in event.reasons
    )
    completion_prompt = str(client.calls[2]['system'])
    assert 'Final Diff review is mandatory' in completion_prompt
    assert 'against every clause of the original request' in completion_prompt
    assert 'behavioral coverage for each acceptance criterion' in completion_prompt
    assert 'next model request/history' in completion_prompt
    assert 'add coverage instead of repurposing' in completion_prompt
    assert 'sample.txt' in completion_prompt



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
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(
            require_changes=True,
            require_verification=True,
        ),
        active_task=task,
    )

    events = collect_turn(conversation, '\ufeffcontinue')
    completed = events[-1]

    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.changed_paths == ('sample.txt',)
    assert completed.result.verification is not None
    assert '[ForgeCode Resumed Change Contract]' in str(
        client.calls[0]['system']
    )
    assert 'Do not create another edit' in str(client.calls[0]['system'])
    first_tool_names = {
        str(definition['name'])
        for definition in client.calls[0]['tools'] or ()
    }
    assert first_tool_names == {
        'finish_task',
        'git_diff',
        'git_status',
        'verify',
    }
    assert all(
        {
            str(definition['name'])
            for definition in call['tools'] or ()
        }
        == first_tool_names
        for call in client.calls
    )
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == (
        'earlier task edit\n'
    )



def test_bare_continue_after_completed_task_does_not_call_model_or_tools(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    client = FakeModelClient()
    task = ActiveTask(
        id='task-complete00001',
        goal='Implement the game in play',
        status='completed',
        requires_change=True,
        scope_hints=('play/**',),
        scope_source='explicit',
        workspace_paths=('play/game.js',),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        active_task=task,
    )

    events = collect_turn(conversation, '\ufeffcontinue')
    completed = events[-1]

    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.model_calls == 0
    assert completed.result.tool_calls == ()
    assert '请直接描述下一项具体修改要求' in completed.result.text
    assert client.calls == []
    assert conversation.task_manager.active == task



def test_prose_completion_reviews_every_changed_file(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    first = ToolCall(
        0,
        'multi-write-first',
        'write_file',
        {'path': 'alpha.js', 'content': 'export const alpha = 1;\n'},
    )
    second = ToolCall(
        1,
        'multi-write-second',
        'write_file',
        {'path': 'beta.js', 'content': 'export const beta = 2;\n'},
    )
    diff_first = ToolCall(
        0,
        'multi-diff-first',
        'git_diff',
        {'path': 'alpha.js'},
    )
    diff_second = ToolCall(
        0,
        'multi-diff-second',
        'git_diff',
        {'path': 'beta.js'},
    )
    client = FakeModelClient(
        response_with_tools(first, second),
        text_response('Created alpha.js and beta.js.'),
        response_with_tool(diff_first),
        response_with_tool(diff_second),
        text_response('Created and reviewed alpha.js and beta.js.'),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
    )

    events = collect_turn(conversation, 'Create alpha.js and beta.js')
    completed = events[-1]

    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.changed_paths == ('alpha.js', 'beta.js')
    review_names = {
        str(definition['name'])
        for definition in client.calls[2]['tools'] or ()
    }
    assert review_names == {
        'finish_task',
        'git_diff',
        'git_status',
        'verify',
    }
    assert 'unreviewed changed files' in str(client.calls[2]['messages'])
    assert len(client.calls) == 5


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


def test_unrelated_writes_cannot_extend_edit_recovery_indefinitely(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    failed_patch = ToolCall(
        0,
        'failed-target-edit',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Update File: sample.txt\n'
                '@@\n'
                '-missing\n'
                '+fixed\n'
                '*** End Patch\n'
            )
        },
    )
    unrelated_writes = [
        ToolCall(
            0,
            f'unrelated-{index}',
            'write_file',
            {
                'path': f'unrelated-{index}.py',
                'content': f'VALUE = {index}\n',
            },
        )
        for index in range(1, 4)
    ]
    client = FakeModelClient(
        response_with_tool(failed_patch),
        *(response_with_tool(call) for call in unrelated_writes),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
        mutation_recovery_limit=4,
    )

    events = collect_turn(conversation, 'Refactor the project')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert completed.result.model_calls == 4
    assert 'edit-recovery cycle(s)' in completed.result.text
    assert not (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'fixed\n'


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


def test_existing_file_recovery_exposes_edits_not_new_scaffolding(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    existing_write = ToolCall(
        0,
        'existing-file-write',
        'write_file',
        {'path': 'sample.txt', 'content': 'dummy'},
    )
    corrected_edit = ToolCall(
        0,
        'existing-file-correction',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(existing_write),
        response_with_tool(corrected_edit),
        finish_response(
            'existing-file-finish',
            task_kind='change',
            summary='Updated the existing implementation file.',
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
    recovery_names = {
        definition['name'] for definition in client.calls[1]['tools'] or ()
    }
    assert 'create_directory' not in recovery_names
    assert 'write_file' not in recovery_names
    assert {'apply_patch', 'replace_text'} <= recovery_names
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


def test_edit_recovery_hides_write_tool_after_two_failures(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    invalid_patches = [
        ToolCall(
            0,
            f'repeated-patch-failure-{index}',
            'apply_patch',
            {
                'patch': (
                    '*** Begin Patch\n'
                    '*** Update File: sample.txt\n'
                    '@@\n'
                    f'-missing-{index}\n'
                    '+new\n'
                    '*** End Patch\n'
                )
            },
        )
        for index in range(2)
    ]
    corrected_edit = ToolCall(
        0,
        'strategy-change-replace',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(invalid_patches[0]),
        response_with_tool(invalid_patches[1]),
        response_with_tool(corrected_edit),
        finish_response(
            'strategy-change-finish',
            task_kind='change',
            summary='Changed editing strategy and completed sample.txt.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_changes=True),
    )

    events = collect_turn(conversation, 'Change sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    recovery_names = {
        definition['name'] for definition in client.calls[2]['tools'] or ()
    }
    assert 'apply_patch' not in recovery_names
    assert 'replace_text' in recovery_names
    assert 'Disabled repeated failing write tool(s)' in str(
        client.calls[2]['messages']
    )


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


def test_desired_state_prompt_cannot_claim_change_without_diff(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    client = FakeModelClient(
        text_response('已把 play 下的游戏朝原版方向推进并验证通过。'),
        text_response('已经完成修改。'),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(
        conversation,
        '当前play下的植物大战僵尸还是太简单了，我想复刻原版',
    )

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert completed.result.changed_paths == ()
    assert 'requires a real task-local workspace change' in completed.result.text
    assert 'Do not ask for confirmation' in str(client.calls[1]['messages'])


def test_followup_requirement_inherits_change_contract(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    first_edit = ToolCall(
        0,
        'initial-game-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'base-game\n',
        },
    )
    followup_edit = ToolCall(
        0,
        'followup-animation-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'base-game\n',
            'new_text': 'animated-game\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(first_edit),
        finish_response(
            'initial-game-finish',
            task_kind='change',
            summary='Implemented the base game.',
        ),
        text_response('我会按动画、贴图、功能三部分继续推进。'),
        response_with_tool(followup_edit),
        finish_response(
            'followup-game-finish',
            task_kind='change',
            summary='Implemented the requested animation requirement.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    first_events = collect_turn(
        conversation,
        '当前play下的植物大战僵尸还是太简单了，我想复刻原版',
    )
    followup_events = collect_turn(
        conversation,
        '要求动画、贴图还有功能都需要',
    )

    first_completed = first_events[-1]
    followup_completed = followup_events[-1]
    assert isinstance(first_completed, TurnCompleted)
    assert isinstance(followup_completed, TurnCompleted)
    assert first_completed.result.status == 'completed'
    assert followup_completed.result.status == 'completed'
    assert followup_completed.result.changed_paths == ('sample.txt',)
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == (
        'animated-game\n'
    )
    followup_retry = client.calls[3]
    assert '[ForgeCode Turn Change Contract]' in (
        followup_retry['system'] or ''
    )
    assert 'Do not ask for confirmation' in str(followup_retry['messages'])


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


def test_pending_write_failure_hides_finish_and_bounds_invalid_attempts(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    edit = ToolCall(
        0,
        'initial-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'verify-initial-edit',
        'verify',
        {'command': 'git diff --check'},
    )
    failed_edit = ToolCall(
        0,
        'unresolved-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'missing\n',
            'new_text': 'extra\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(edit),
        response_with_tool(verify),
        response_with_tool(failed_edit),
        *(
            finish_response(
                f'premature-finish-{index}',
                task_kind='change',
                summary='Finished despite the unresolved edit.',
            )
            for index in range(1, 4)
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        max_tool_protocol_recoveries=3,
    )

    events = collect_turn(conversation, 'Apply and verify all required edits')

    finish_result = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'premature-finish-1'
    )
    assert finish_result.error is not None
    assert finish_result.error.code == 'tool_not_available_in_phase'
    assert 'finish_task' not in {
        definition['name'] for definition in client.calls[3]['tools'] or ()
    }
    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert completed.result.model_calls == 6
    assert completed.result.verification is not None
    assert completed.result.verification.success is True
    assert 'malformed or schema-invalid tool requests' in completed.result.text
    assert 'Finished despite the unresolved edit.' not in completed.result.text


def test_required_change_convergence_allows_edit_after_stagnation(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    investigation = read_only_stagnation_calls('action-success')
    convergence_read = ToolCall(
        0,
        'action-success-targeted-read',
        'read_file',
        {'path': 'sample.txt', 'start_line': 1, 'end_line': 1},
    )
    edit = ToolCall(
        0,
        'action-success-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'action-success-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    summary = 'Changed sample.txt after Action Recovery and verified it.'
    client = FakeModelClient(
        *(response_with_tool(call) for call in investigation),
        response_with_tool(convergence_read),
        response_with_tool(edit),
        response_with_tool(verify),
        finish_response(
            'action-success-finish',
            task_kind='change',
            summary=summary,
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

    events = collect_turn(conversation, 'Change and verify sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.model_calls == 13
    assert completed.result.changed_paths == ('sample.txt',)
    assert completed.result.verification is not None
    assert completed.result.verification.success is True
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'
    convergence_names = {
        str(definition['name'])
        for definition in client.calls[8]['tools'] or ()
    }
    assert 'apply_patch' in convergence_names
    assert 'replace_text' in convergence_names
    assert 'write_file' in convergence_names
    assert 'read_file' in convergence_names
    assert 'grep' in convergence_names
    post_read_names = {
        str(definition['name'])
        for definition in client.calls[10]['tools'] or ()
    }
    assert 'apply_patch' in post_read_names
    assert 'replace_text' in post_read_names
    assert 'read_file' not in post_read_names
    assert 'grep' not in post_read_names
    assert all(
        '[ForgeCode Action Recovery]' not in (call['system'] or '')
        for call in client.calls
    )


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
    assert 'replace_text' not in available_names
    assert 'write_file_chunk' not in available_names
    assert 'find_files' in available_names
    assert 'list_directory' in available_names


def test_failed_edit_gets_one_focused_correction_attempt(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    read = ToolCall(
        0,
        'action-transfer-read',
        'read_file',
        {'path': 'sample.txt'},
    )
    failed_edit = ToolCall(
        0,
        'action-transfer-failed-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'not-present\n',
            'new_text': 'new\n',
        },
    )
    valid_edit = ToolCall(
        0,
        'action-transfer-valid-edit',
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
        'action-transfer-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    client = FakeModelClient(
        response_with_tool(read),
        response_with_tool(failed_edit),
        response_with_tool(valid_edit),
        response_with_tool(verify),
        finish_response(
            'action-transfer-finish',
            task_kind='change',
            summary='Recovered from the failed edit and verified.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, 'Fix sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.changed_paths == ('sample.txt',)
    assert '[ForgeCode Action Recovery]' not in (
        client.calls[1]['system'] or ''
    )
    assert '[Failed Mutation Recovery]' in (
        client.calls[2]['system'] or ''
    )
    mutation_tool_names = {
        str(definition['name'])
        for definition in client.calls[2]['tools'] or ()
    }
    assert {'read_file', 'grep', 'apply_patch'} <= mutation_tool_names
    assert 'write_file' in mutation_tool_names
    assert 'replace_text' in mutation_tool_names
    assert 'write_file_chunk' not in mutation_tool_names
    assert 'verify' not in mutation_tool_names
    assert 'run_command' not in mutation_tool_names
    assert 'finish_task' not in mutation_tool_names
    assert 'verify' in {
        str(definition['name'])
        for definition in client.calls[3]['tools'] or ()
    }


def test_process_workspace_change_does_not_clear_failed_edit_recovery(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    registry = create_default_registry(tmp_path)
    registry.register(ProcessModifyTool(tmp_path))
    failed_edit = ToolCall(
        0,
        'process-after-failed-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'missing\n',
            'new_text': 'new\n',
        },
    )
    process_change = ToolCall(1, 'process-change', 'process_modify', {})
    valid_edit = ToolCall(
        0,
        'focused-recovery-edit',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Update File: sample.txt\n'
                '@@\n'
                '-temporary\n'
                '+new\n'
                '*** End Patch\n'
            )
        },
    )
    verify = ToolCall(
        0,
        'process-recovery-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    client = FakeModelClient(
        response_with_tools(failed_edit, process_change),
        response_with_tool(valid_edit),
        response_with_tool(verify),
        finish_response(
            'process-recovery-finish',
            task_kind='change',
            summary='Recovered with a focused edit and verified it.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=registry,
        task_policy=TaskPolicy(require_verification=True),
    )

    events = collect_turn(conversation, 'Fix and verify sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert '[Failed Mutation Recovery]' in (
        client.calls[1]['system'] or ''
    )
    recovery_names = {
        str(definition['name'])
        for definition in client.calls[1]['tools'] or ()
    }
    assert 'apply_patch' in recovery_names
    assert 'replace_text' in recovery_names
    assert 'write_file' in recovery_names
    assert 'run_command' not in recovery_names
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'


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
    assert 'replace_text' not in post_read_names
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.status == 'completed'


def test_required_change_moves_from_exploration_to_edit_only_convergence(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    investigation = read_only_stagnation_calls('action-read-only')
    edit = ToolCall(
        0,
        'action-convergence-edit',
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
        'action-convergence-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    client = FakeModelClient(
        *(response_with_tool(call) for call in investigation),
        response_with_tool(edit),
        response_with_tool(verify),
        finish_response(
            'action-convergence-finish',
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
        change_exploration_limit=8,
    )

    events = collect_turn(conversation, 'Change and verify sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.model_calls == 12
    assert completed.result.changed_paths == ('sample.txt',)
    convergence_names = {
        str(definition['name'])
        for definition in client.calls[8]['tools'] or ()
    }
    assert 'apply_patch' in convergence_names
    assert 'replace_text' in convergence_names
    assert 'write_file' in convergence_names
    assert 'read_file' in convergence_names
    assert 'grep' in convergence_names
    assert 'verify' not in convergence_names
    assert 'ForgeCode implementation checkpoint' in str(
        client.calls[8]['messages']
    )
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'


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
    assert redundant_result.success is False
    assert redundant_result.error is not None
    assert redundant_result.error.code == 'recovery_read_already_used'
    stale_verify_result = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'failed-verify-stale-verify'
    )
    assert stale_verify_result.success is False
    assert stale_verify_result.error is not None
    assert stale_verify_result.error.code == 'tool_not_available_in_phase'
    recovery_names = {
        str(definition['name'])
        for definition in client.calls[2]['tools'] or ()
    }
    assert {'read_file', 'grep', 'apply_patch', 'replace_text'} <= recovery_names
    assert 'verify' not in recovery_names
    recovery_messages = client.calls[2]['messages']
    assert 'ForgeCode verification repair checkpoint' in str(
        recovery_messages[-1]['content']
    )
    assert 'fix that production-code root cause first' in str(
        recovery_messages[-1]['content']
    )
    assert 'Do not weaken, delete, or rewrite existing tests' in str(
        recovery_messages[-1]['content']
    )
    post_read_names = {
        str(definition['name'])
        for definition in client.calls[3]['tools'] or ()
    }
    assert {'apply_patch', 'replace_text'} <= post_read_names
    assert 'read_file' not in post_read_names
    assert 'grep' not in post_read_names


def test_completion_decision_default_is_bounded() -> None:
    conversation = Conversation(client=FakeModelClient(text_response('done')))

    assert conversation.completion_decision_limit == 3


def test_verified_change_stagnation_allows_final_summary_recovery(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    edit = ToolCall(
        0,
        'convergence-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'convergence-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    initial_diff = ToolCall(0, 'initial-diff', 'git_diff', {})
    redundant_diffs = [
        ToolCall(0, f'redundant-diff-{index}', 'git_diff', {})
        for index in range(1, 9)
    ]
    summary = 'Updated sample.txt and verified it with git diff --check.'
    client = FakeModelClient(
        response_with_tool(edit),
        response_with_tool(verify),
        response_with_tool(initial_diff),
        *(response_with_tool(call) for call in redundant_diffs),
        text_response(summary),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_verification=True),
        completion_decision_limit=8,
    )

    events = collect_turn(conversation, 'Change and verify sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.model_calls == 12
    assert completed.result.text == summary
    assert completed.result.changed_paths == ('sample.txt',)
    assert completed.result.verification is not None
    assert completed.result.verification.success is True
    assert client.responses == []
    final_request = (
        (client.calls[-1]['system'] or '')
        + str(client.calls[-1]['messages'])
    )
    assert '[ForgeCode Finalization Recovery]' in final_request
    assert client.calls[-1]['tools'] is None
    assert 'Runtime Tool Availability' not in (
        client.calls[-1]['system'] or ''
    )


def test_unverified_change_stagnation_allows_final_summary_recovery(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    edit = ToolCall(
        0,
        'unverified-convergence-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    initial_diff = ToolCall(0, 'unverified-initial-diff', 'git_diff', {})
    redundant_diffs = [
        ToolCall(0, f'unverified-redundant-diff-{index}', 'git_diff', {})
        for index in range(1, 9)
    ]
    summary = 'Updated sample.txt; no verification was required or run.'
    client = FakeModelClient(
        response_with_tool(edit),
        response_with_tool(initial_diff),
        *(response_with_tool(call) for call in redundant_diffs),
        text_response(summary),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        completion_decision_limit=8,
    )

    events = collect_turn(conversation, 'Change sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.text == summary
    assert completed.result.changed_paths == ('sample.txt',)
    assert completed.result.verification is None
    assert client.calls[-1]['tools'] is None
    final_request = (
        (client.calls[-1]['system'] or '')
        + str(client.calls[-1]['messages'])
    )
    assert '[ForgeCode Finalization Recovery]' in final_request
    assert 'not required / not run' in final_request


def test_novel_repository_evidence_cannot_extend_completion_ready_loop(
    tmp_path: Path,
) -> None:
    for index in range(1, 9):
        path = tmp_path / 'notes' / f'evidence-{index}.txt'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'evidence {index}\n', encoding='utf-8')
    initialize_git_repository(tmp_path)
    edit = ToolCall(
        0,
        'novel-ready-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'novel-ready-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    diff = ToolCall(0, 'novel-ready-diff', 'git_diff', {})
    novel_reads = [
        ToolCall(
            0,
            f'novel-ready-read-{index}',
            'read_file',
            {'path': f'notes/evidence-{index}.txt'},
        )
        for index in range(1, 9)
    ]
    summary = (
        'Updated and verified sample.txt after reviewing '
        'notes/evidence-1.txt.'
    )
    client = FakeModelClient(
        response_with_tool(edit),
        response_with_tool(verify),
        response_with_tool(diff),
        *(response_with_tool(call) for call in novel_reads),
        text_response(summary),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_verification=True),
        completion_decision_limit=8,
    )

    events = collect_turn(conversation, 'Change and verify sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert completed.result.model_calls == 12
    assert completed.result.text == summary
    assert client.calls[-1]['tools'] is None
    assert '[ForgeCode Finalization Recovery]' in (
        client.calls[-1]['system'] or ''
    )


def test_finalization_recovery_stops_after_one_more_redundant_diff(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    edit = ToolCall(
        0,
        'finalization-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'finalization-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    initial_diff = ToolCall(0, 'finalization-diff', 'git_diff', {})
    redundant_diffs = [
        ToolCall(0, f'finalization-repeat-{index}', 'git_diff', {})
        for index in range(1, 10)
    ]
    client = FakeModelClient(
        response_with_tool(edit),
        response_with_tool(verify),
        response_with_tool(initial_diff),
        *(response_with_tool(call) for call in redundant_diffs),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        task_policy=TaskPolicy(require_verification=True),
        completion_decision_limit=8,
    )

    events = collect_turn(conversation, 'Change and verify sample.txt')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert completed.result.model_calls == 12
    assert 'finalization recovery' in completed.result.text.casefold()
    assert completed.result.changed_paths == ('sample.txt',)
    assert completed.result.verification is not None
    assert completed.result.verification.success is True
    assert len(client.calls) == 12
    recovery_request = (
        (client.calls[-1]['system'] or '')
        + str(client.calls[-1]['messages'])
    )
    assert '[ForgeCode Finalization Recovery]' in recovery_request
    assert client.calls[-1]['tools'] is None


def test_unfinished_explicit_plan_does_not_enter_finalization_recovery(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    plan = ToolCall(
        0,
        'unfinished-plan',
        'task_plan',
        {'steps': ['Edit sample', 'Complete remaining work']},
    )
    edit = ToolCall(
        0,
        'unfinished-plan-edit',
        'replace_text',
        {
            'path': 'sample.txt',
            'old_text': 'old\n',
            'new_text': 'new\n',
        },
    )
    verify = ToolCall(
        0,
        'unfinished-plan-verify',
        'verify',
        {'command': 'git diff --check'},
    )
    diff = ToolCall(0, 'unfinished-plan-diff', 'git_diff', {})
    redundant_diffs = [
        ToolCall(0, f'unfinished-plan-repeat-{index}', 'git_diff', {})
        for index in range(1, 9)
    ]
    client = FakeModelClient(
        response_with_tool(plan),
        response_with_tool(edit),
        response_with_tool(verify),
        response_with_tool(diff),
        *(response_with_tool(call) for call in redundant_diffs),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, 'Complete both planned steps')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert completed.result.model_calls == 10
    assert len(client.calls) == 10
    assert all(call['tools'] is not None for call in client.calls)
    assert all(
        '[ForgeCode Finalization Recovery]' not in (call['system'] or '')
        for call in client.calls
    )


def test_inferred_task_scope_blocks_unrelated_workspace_write(
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
    assert wrong_result.error is not None
    assert wrong_result.error.code == 'outside_task_scope'
    assert state_file.read_text(encoding='utf-8') == 'safe\n'
    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert set(completed.result.changed_paths) == {
        'play/index.html',
        'play/src/.gitkeep',
    }


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


def test_false_blocker_is_rejected_without_open_ended_recovery(
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
    recovery_searches = [
        ToolCall(
            0,
            f'toolu_recovery_{index}',
            'find_files',
            {'path': '.', 'pattern': f'still-missing-{index}'},
        )
        for index in range(1, 5)
    ]
    client = FakeModelClient(
        *(response_with_tool(call) for call in searches),
        finish_response(
            'finish_blocked',
            task_kind='change',
            status='blocked',
            summary='I could not complete the requested code change.',
            blocked_reasons=['No applicable source evidence was found.'],
        ),
        *(response_with_tool(call) for call in recovery_searches),
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

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'stuck'
    assert len(client.calls) == 3
    assert len(client.responses) == 4
    assert all(call['tools'] is not None for call in client.calls)
    finish_event = next(
        event for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.name == 'finish_task'
    )
    assert finish_event.result.error is not None
    assert finish_event.result.error.code == 'finish_rejected'


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


def test_change_plan_upgrades_a_misclassified_turn_contract(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    plan = ToolCall(
        0,
        'upgrade-change-plan',
        'task_plan',
        {
            'steps': [
                '实现更复杂的游戏逻辑',
                '验证修改后的游戏',
            ],
            'scope_hints': ['sample.txt'],
        },
    )
    client = FakeModelClient(
        response_with_tool(plan),
        text_response('I only prepared the implementation plan.'),
        text_response('I still did not modify sample.txt.'),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, '查看 sample.txt 并制定后续步骤')

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert conversation.task_manager.active is not None
    assert conversation.task_manager.active.requires_change is True
    assert completed.result.changed_paths == ()
    assert completed.result.status == 'stuck'
    assert any(
        'requires a real task-local workspace change' in reason
        for reason in completed.result.completion_reasons
    )


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
            allows_deletion=True,
            confidence=0.99,
            reason='The user explicitly authorized cleanup deletion.',
        ),
        usage=TokenUsage(7, 3),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
        intent_router=router,
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
            allows_deletion=True,
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
    assert 'remove_directory' in recovery_tool_names
    assert 'read_file' not in recovery_tool_names
    assert 'list_directory' not in recovery_tool_names
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


def test_placeholder_write_rejection_enters_bounded_edit_recovery(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    play = tmp_path / 'play'
    play.mkdir()
    app = play / 'app.js'
    app.write_text('old\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'play/app.js'], cwd=tmp_path, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'add play app'],
        cwd=tmp_path,
        check=True,
    )
    placeholder = ToolCall(
        0,
        'placeholder-write',
        'write_file',
        {'path': 'play/test.txt', 'content': 'noop'},
    )
    corrected = ToolCall(
        0,
        'corrected-project-edit',
        'replace_text',
        {
            'path': 'play/app.js',
            'old_text': 'old\n',
            'new_text': 'optimized\n',
        },
    )
    client = FakeModelClient(
        response_with_tool(placeholder),
        response_with_tool(corrected),
        finish_response(
            'placeholder-recovery-finish',
            task_kind='change',
            summary='Completed the real project optimization.',
        ),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(conversation, '优化 play 目录的实现')

    rejected = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'placeholder-write'
    )
    completed = events[-1]
    recovery_tool_names = {
        str(tool.get('name', ''))
        for tool in client.calls[1]['tools']
    }
    assert rejected.success is False
    assert rejected.error is not None
    assert rejected.error.code == 'placeholder_mutation_denied'
    assert 'list_directory' not in recovery_tool_names
    assert 'find_files' not in recovery_tool_names
    assert app.read_text(encoding='utf-8') == 'optimized\n'
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
    assert len(client.calls) == 3
    assert 'without a workspace change' not in completed.result.text


def test_enhancement_task_cannot_delete_existing_target_before_rewrite(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    play = tmp_path / 'play'
    play.mkdir()
    game = play / 'game.js'
    game.write_text('const level = 1;\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'play/game.js'], cwd=tmp_path, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'add game'],
        cwd=tmp_path,
        check=True,
    )
    delete = ToolCall(
        0,
        'unsafe-delete-game',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Delete File: play/game.js\n'
                '*** End Patch'
            ),
        },
    )
    update = ToolCall(
        0,
        'safe-update-game',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Update File: play/game.js\n'
                '@@\n'
                '-const level = 1;\n'
                '+const level = 2;\n'
                '*** End Patch'
            ),
        },
    )
    client = FakeModelClient(
        response_with_tool(delete),
        response_with_tool(update),
        text_response('Enhanced play/game.js without deleting the original.'),
    )
    conversation = Conversation(
        client=client,
        registry=create_default_registry(tmp_path),
    )

    events = collect_turn(
        conversation,
        '帮我将 play/game.js 做得高级复杂一点',
    )

    delete_result = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'unsafe-delete-game'
    )
    completed = events[-1]
    assert delete_result.success is False
    assert delete_result.error is not None
    assert delete_result.error.code == 'destructive_edit_not_requested'
    assert game.read_text(encoding='utf-8') == 'const level = 2;\n'
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'


def test_enhancement_task_cannot_remove_existing_directory(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    play = tmp_path / 'play'
    play.mkdir()
    game = play / 'game.js'
    game.write_text('const level = 1;\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'play/game.js'], cwd=tmp_path, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'add game directory'],
        cwd=tmp_path,
        check=True,
    )
    remove = ToolCall(
        0,
        'unsafe-remove-play',
        'remove_directory',
        {'path': 'play', 'recursive': True},
    )
    update = ToolCall(
        0,
        'safe-update-game-after-directory-rejection',
        'apply_patch',
        {
            'patch': (
                '*** Begin Patch\n'
                '*** Update File: play/game.js\n'
                '@@\n'
                '-const level = 1;\n'
                '+const level = 2;\n'
                '*** End Patch'
            ),
        },
    )
    client = FakeModelClient(
        response_with_tool(remove),
        response_with_tool(update),
        text_response('Enhanced the game without deleting its directory.'),
    )
    router = AsyncMock()
    router.route.return_value = RouteResult(
        decision=TurnDecision(
            intent='change_task',
            task_relation='new',
            requires_workspace_change=True,
            allows_deletion=False,
            confidence=0.99,
            reason='The user requested an enhancement, not deletion.',
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

    events = collect_turn(conversation, '帮我把 play 目录里的游戏做得更高级')

    remove_result = next(
        event.result
        for event in events
        if isinstance(event, ToolExecutionCompleted)
        and event.tool_call.id == 'unsafe-remove-play'
    )
    completed = events[-1]
    assert remove_result.success is False
    assert remove_result.error is not None
    assert remove_result.error.code == 'destructive_edit_not_requested'
    assert game.read_text(encoding='utf-8') == 'const level = 2;\n'
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'


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
    assert 'write_file_chunk' in recovery_names
    assert 'read_file' not in recovery_names
    assert rejected_read.success is False
    assert rejected_read.error is not None
    assert rejected_read.error.code == 'tool_not_available_in_phase'
    assert (tmp_path / 'large.js').read_text(encoding='utf-8') == (
        'const ready = true;\n'
    )
    assert isinstance(completed, TurnCompleted)
    assert completed.result.status == 'completed'
