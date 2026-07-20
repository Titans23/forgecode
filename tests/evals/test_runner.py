'''Tests for the disposable M2 evaluation runner.'''

import asyncio
from collections.abc import AsyncIterator
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from evals.runner import EvalCase, PreparedFixture, load_case, run_case
from forge.runtime.state import (
    ModelStreamEvent,
    ModelTextDelta,
    ModelToolCallCompleted,
    ModelUsageUpdate,
    TokenUsage,
    ToolCall,
)


def initialize_project(root: Path) -> str:
    fixture = root / 'fixtures' / 'demo'
    hidden = fixture / 'tests' / 'hidden'
    hidden.mkdir(parents=True)
    (fixture / 'value.txt').write_text('old\n', encoding='utf-8')
    (hidden / 'test_secret.txt').write_text('secret\n', encoding='utf-8')
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
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'fixture baseline'],
        cwd=root,
        check=True,
    )
    return subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def make_case(commit: str) -> EvalCase:
    public_script = (
        'assert open('
        + repr('value.txt')
        + ').read() == '
        + repr('new\n')
    )
    hidden_script = (
        'assert open('
        + repr('tests/hidden/test_secret.txt')
        + ').read() == '
        + repr('secret\n')
    )
    return EvalCase(
        id='demo-001',
        repo='fixtures/demo',
        base_commit=commit,
        task='Change value.txt from old to new.',
        test_command=subprocess.list2cmdline(
            [sys.executable, '-c', public_script]
        ),
        hidden_test_command=subprocess.list2cmdline(
            [sys.executable, '-c', hidden_script]
        ),
        allowed_paths=('value.txt',),
        forbidden_paths=('tests/hidden/**',),
    )


class FakeModelClient:
    provider = 'fake'

    def __init__(self, *responses: list[ModelStreamEvent]) -> None:
        self.responses = list(responses)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        for event in self.responses.pop(0):
            yield event


class FailingModelClient:
    provider = 'fake'

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        raise RuntimeError('model unavailable')
        yield ModelTextDelta(text='unreachable')


def tool_response(call: ToolCall) -> list[ModelStreamEvent]:
    return [
        ModelUsageUpdate(usage=TokenUsage(10, 0)),
        ModelToolCallCompleted(tool_call=call),
        ModelUsageUpdate(usage=TokenUsage(10, 2)),
    ]


def final_response() -> list[ModelStreamEvent]:
    return tool_response(
        ToolCall(
            index=0,
            id='toolu_finish',
            name='finish_task',
            arguments={
                'task_kind': 'change',
                'status': 'completed',
                'summary': 'Implemented and verified.',
                'blocked_reasons': [],
            },
        )
    )


def test_load_case_validates_real_yaml() -> None:
    path = Path('evals/cases/python-calculator-001.yaml')

    case = load_case(path)

    assert case.id == 'python-calculator-001'
    assert case.allowed_paths == ('src/calculator/**', 'tests/public/**')
    assert case.forbidden_paths == ('tests/hidden/**',)


def test_prepared_fixture_uses_commit_and_hides_tests(tmp_path: Path) -> None:
    commit = initialize_project(tmp_path)
    case = make_case(commit)

    with PreparedFixture(tmp_path, case) as prepared:
        workspace = prepared.workspace
        assert (workspace / 'value.txt').read_text() == 'old\n'
        assert not (workspace / 'tests' / 'hidden').exists()
        status = subprocess.run(
            ['git', 'status', '--short'],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        assert status.stdout == ''

        prepared.restore_hidden_tests()
        hidden = workspace / 'tests' / 'hidden' / 'test_secret.txt'
        assert hidden.read_text() == 'secret\n'


def test_run_case_passes_with_fake_agent_and_independent_hidden_test(
    tmp_path: Path,
) -> None:
    commit = initialize_project(tmp_path)
    case = make_case(commit)
    edit = ToolCall(
        index=0,
        id='toolu_edit',
        name='write_file',
        arguments={
            'path': 'value.txt',
            'content': 'new\n',
        },
    )
    verify = ToolCall(
        index=0,
        id='toolu_verify',
        name='verify',
        arguments={'command': case.test_command},
    )
    client = FakeModelClient(
        tool_response(edit),
        tool_response(verify),
        final_response(),
    )
    output_dir = tmp_path / 'out'

    outcome = asyncio.run(
        run_case(
            case,
            project_root=tmp_path,
            output_dir=output_dir,
            client=client,
        )
    )

    assert outcome.passed is True
    assert outcome.task_status == 'completed'
    assert outcome.changed_paths == ('value.txt',)
    assert outcome.public_tests is not None
    assert outcome.public_tests.success is True
    assert outcome.hidden_tests is not None
    assert outcome.hidden_tests.success is True
    assert outcome.trajectory is not None
    assert Path(outcome.trajectory).is_file()
    report = json.loads(
        (output_dir / 'demo-001-latest.json').read_text(encoding='utf-8')
    )
    assert report['passed'] is True


def test_run_case_preserves_trajectory_when_model_fails(
    tmp_path: Path,
) -> None:
    commit = initialize_project(tmp_path)
    case = make_case(commit)
    output_dir = tmp_path / 'out'

    outcome = asyncio.run(
        run_case(
            case,
            project_root=tmp_path,
            output_dir=output_dir,
            client=FailingModelClient(),
        )
    )

    assert outcome.passed is False
    assert outcome.error is not None
    assert 'model unavailable' in outcome.error
    assert outcome.trajectory is not None
    assert Path(outcome.trajectory).is_file()
