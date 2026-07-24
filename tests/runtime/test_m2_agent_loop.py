'''Integration tests for the M2 model-tool-verification loop.'''

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
import subprocess
from typing import Any

from forge.runtime.agent_loop import (
    Conversation,
    build_final_acceptance_audit_feedback,
    completion_review_paths,
    render_completion_ready_context,
)
from forge.runtime.completion import TaskPolicy
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
from forge.tools import create_default_registry
from forge.tools.base import Tool, ToolInput, ToolResult


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
    assert 'list_directory' not in post_explore_names
    assert 'find_files' not in post_explore_names
    post_mutation_names = {
        definition['name'] for definition in client.calls[3]['tools'] or ()
    }
    assert {'read_file', 'grep', 'apply_patch', 'replace_text', 'verify'} <= (
        post_mutation_names
    )
    assert 'list_directory' not in post_mutation_names
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
    assert {'read_file', 'grep', 'apply_patch', 'replace_text'} <= recovery_names
    assert 'list_directory' not in recovery_names
    assert 'find_files' not in recovery_names
    assert 'ForgeCode rejected completion' in str(
        client.calls[3]['messages']
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
        change_exploration_limit=8,
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
    assert 'write_file' not in convergence_names
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
    assert 'write_file' not in mutation_tool_names
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
    assert 'write_file' not in recovery_names
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
    assert 'write_file' not in convergence_names
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
