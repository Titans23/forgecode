'''Run ForgeCode evaluation cases in disposable local Git workspaces.'''

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
import yaml

from forge.runtime.agent_loop import Conversation
from forge.runtime.completion import TaskPolicy, matches_any
from forge.runtime.model_client import AnthropicModelClient, ModelClient
from forge.runtime.router import ModelIntentRouter
from forge.runtime.state import TurnCompleted, TurnResult
from forge.sessions.trajectory import TrajectoryRecorder
from forge.tools import create_default_registry
from forge.tools.shell import ProcessResult, run_process


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EvalCase(BaseModel):
    '''Validated schema for one YAML evaluation case.'''

    model_config = ConfigDict(extra='forbid')

    id: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    base_commit_repository: str = '.'
    base_commit: str = Field(min_length=1)
    task: str = Field(min_length=1)
    setup_command: str | None = None
    build_command: str | None = None
    test_command: str = Field(min_length=1)
    hidden_test_command: str = Field(min_length=1)
    timeout_seconds: float = Field(default=300, gt=0, le=1800)
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ('tests/hidden/**',)
    success_criteria: dict[str, bool] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommandEvidence:
    '''Compact evaluator-owned evidence for one shell command.'''

    command: str
    exit_code: int
    duration_seconds: float
    timed_out: bool
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return not self.timed_out and self.exit_code == 0

    @classmethod
    def from_process(
        cls,
        command: str,
        result: ProcessResult,
    ) -> CommandEvidence:
        return cls(
            command=command,
            exit_code=result.exit_code,
            duration_seconds=result.duration_seconds,
            timed_out=result.timed_out,
            stdout=bounded_output(result.stdout),
            stderr=bounded_output(result.stderr),
        )


@dataclass(slots=True)
class EvalOutcome:
    '''Serializable acceptance result for one case.'''

    case_id: str
    passed: bool = False
    task_status: str = 'failed'
    changed_paths: tuple[str, ...] = ()
    reasons: list[str] = field(default_factory=list)
    setup: CommandEvidence | None = None
    build: CommandEvidence | None = None
    public_tests: CommandEvidence | None = None
    hidden_tests: CommandEvidence | None = None
    diff_check: CommandEvidence | None = None
    trajectory: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_case(path: Path) -> EvalCase:
    '''Load and validate one YAML case without executing it.'''
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'Evaluation case must be a YAML mapping: {path}')
    return EvalCase.model_validate(data)


def bounded_output(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


@dataclass(frozen=True, slots=True)
class HiddenFile:
    path: str
    content: bytes


class PreparedFixture(AbstractContextManager['PreparedFixture']):
    '''Extract a fixed revision and keep hidden tests outside Agent reach.'''

    def __init__(self, project_root: Path, case: EvalCase) -> None:
        self.project_root = project_root.resolve()
        self.case = case
        self._temporary = TemporaryDirectory(prefix=f'forge-{case.id}-')
        self.root = Path(self._temporary.name)
        self.workspace = self.root / PurePosixPath(case.repo)
        self.hidden_files: tuple[HiddenFile, ...] = ()

    def __enter__(self) -> PreparedFixture:
        repository = (
            self.project_root / self.case.base_commit_repository
        ).resolve()
        archive = subprocess.run(
            [
                'git',
                'archive',
                '--format=tar',
                self.case.base_commit,
                '--',
                self.case.repo,
            ],
            cwd=repository,
            check=True,
            capture_output=True,
        )
        with tarfile.open(fileobj=BytesIO(archive.stdout), mode='r:') as file:
            file.extractall(self.root, filter='data')
        if not self.workspace.is_dir():
            raise RuntimeError(
                f'Fixture path missing from archive: {self.case.repo}'
            )
        self.hidden_files = remove_hidden_tests(self.workspace)
        initialize_workspace_git(self.workspace)
        return self

    def restore_hidden_tests(self) -> None:
        for hidden in self.hidden_files:
            destination = self.workspace / PurePosixPath(hidden.path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(hidden.content)

    def __exit__(self, *args: object) -> None:
        self._temporary.cleanup()


def remove_hidden_tests(workspace: Path) -> tuple[HiddenFile, ...]:
    hidden_root = workspace / 'tests' / 'hidden'
    if not hidden_root.exists():
        return ()
    files = tuple(
        HiddenFile(
            path=file.relative_to(workspace).as_posix(),
            content=file.read_bytes(),
        )
        for file in sorted(hidden_root.rglob('*'))
        if file.is_file()
    )
    shutil.rmtree(hidden_root)
    return files


def initialize_workspace_git(workspace: Path) -> None:
    commands = (
        ['git', 'init', '--quiet'],
        ['git', 'config', 'user.email', 'forge-eval@example.test'],
        ['git', 'config', 'user.name', 'ForgeCode Eval'],
    )
    for command in commands:
        subprocess.run(command, cwd=workspace, check=True, capture_output=True)
    exclude = workspace / '.git' / 'info' / 'exclude'
    exclude.write_text(
        (
            '\n.venv/\nnode_modules/\ntarget/\n'
            '__pycache__/\n*.py[cod]\n'
            '.forge/\ntests/hidden/\n'
        ),
        encoding='utf-8',
    )
    subprocess.run(
        ['git', 'add', '-A'], cwd=workspace, check=True, capture_output=True
    )
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'evaluation baseline'],
        cwd=workspace,
        check=True,
        capture_output=True,
    )


async def execute_command(
    command: str,
    workspace: Path,
    timeout_seconds: float,
) -> CommandEvidence:
    result = await run_process(
        command,
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        shell=True,
    )
    return CommandEvidence.from_process(command, result)


def build_agent_prompt(case: EvalCase) -> str:
    allowed = '\n'.join(f'- {path}' for path in case.allowed_paths) or '- none'
    forbidden = (
        '\n'.join(f'- {path}' for path in case.forbidden_paths) or '- none'
    )
    commands = [f'- public tests: {case.test_command}']
    if case.build_command is not None:
        commands.append(f'- build: {case.build_command}')
    return (
        f'{case.task}\n\n'
        'This is a ForgeCode evaluation task. Hidden tests are unavailable '
        'and must not be searched for. Use only public repository evidence.\n\n'
        f'Allowed modification paths:\n{allowed}\n\n'
        f'Forbidden modification paths:\n{forbidden}\n\n'
        'Required public verification commands:\n'
        + '\n'.join(commands)
        + '\n\nUse the verify tool for the final relevant verification.'
    )


async def run_agent(
    case: EvalCase,
    workspace: Path,
    recorder: TrajectoryRecorder,
    client: ModelClient | None,
) -> TurnResult:
    policy = TaskPolicy(
        require_changes=True,
        require_verification=True,
        allowed_paths=case.allowed_paths,
        forbidden_paths=case.forbidden_paths,
    )
    agent_client = client or AnthropicModelClient.from_config()
    intent_router = (
        None
        if client is not None
        else ModelIntentRouter(AnthropicModelClient.from_config(max_tokens=600))
    )
    conversation = Conversation(
        client=agent_client,
        registry=create_default_registry(workspace),
        task_policy=policy,
        intent_router=intent_router,
    )
    prompt = build_agent_prompt(case)
    recorder.record_user_message(prompt)
    final: TurnResult | None = None
    try:
        async for event in conversation.stream(prompt):
            recorder.record_event(event)
            if isinstance(event, TurnCompleted):
                final = event.result
    except Exception as error:
        recorder.record_error(error)
        raise
    if final is None:
        raise RuntimeError('Agent Loop ended without TurnCompleted.')
    return final


async def run_case(
    case: EvalCase,
    *,
    project_root: Path = PROJECT_ROOT,
    output_dir: Path | None = None,
    client: ModelClient | None = None,
) -> EvalOutcome:
    '''Run one case and persist evaluator-owned evidence outside the fixture.'''
    destination = (
        output_dir
        if output_dir is not None
        else project_root / '.forge' / 'evals'
    )
    destination.mkdir(parents=True, exist_ok=True)
    outcome = EvalOutcome(case_id=case.id)
    recorder: TrajectoryRecorder | None = None

    try:
        with PreparedFixture(project_root, case) as prepared:
            workspace = prepared.workspace
            if case.setup_command is not None:
                outcome.setup = await execute_command(
                    case.setup_command,
                    workspace,
                    case.timeout_seconds,
                )
                if not outcome.setup.success:
                    outcome.reasons.append('Setup command failed.')

            result: TurnResult | None = None
            if not outcome.reasons:
                recorder = TrajectoryRecorder.create(workspace)
                try:
                    result = await run_agent(case, workspace, recorder, client)
                    outcome.task_status = result.status
                    outcome.changed_paths = result.changed_paths

                    if case.build_command is not None:
                        outcome.build = await execute_command(
                            case.build_command,
                            workspace,
                            case.timeout_seconds,
                        )
                    outcome.public_tests = await execute_command(
                        case.test_command,
                        workspace,
                        case.timeout_seconds,
                    )
                    outcome.diff_check = await execute_command(
                        'git diff HEAD --check',
                        workspace,
                        30,
                    )

                    prepared.restore_hidden_tests()
                    outcome.hidden_tests = await execute_command(
                        case.hidden_test_command,
                        workspace,
                        case.timeout_seconds,
                    )
                    outcome.reasons.extend(
                        acceptance_reasons(case, result, outcome)
                    )
                finally:
                    if recorder.path.exists():
                        outcome.trajectory = copy_trajectory(
                            recorder.path,
                            destination,
                            case.id,
                        )
    except Exception as error:
        outcome.error = f'{type(error).__name__}: {error}'
        outcome.reasons.append('Evaluation runner failed.')

    outcome.reasons = list(dict.fromkeys(outcome.reasons))
    outcome.passed = not outcome.reasons and outcome.error is None
    write_outcome(outcome, destination)
    return outcome


def acceptance_reasons(
    case: EvalCase,
    result: TurnResult,
    outcome: EvalOutcome,
) -> list[str]:
    reasons: list[str] = []
    if result.status != 'completed':
        reasons.append(f'Agent task status was {result.status}.')
    if not result.changed_paths:
        reasons.append('Final Diff was empty.')
    if result.verification is None or not result.verification.success:
        reasons.append('Agent did not produce successful verify evidence.')

    forbidden = tuple(
        path
        for path in result.changed_paths
        if matches_any(path, case.forbidden_paths)
    )
    if forbidden:
        reasons.append('Forbidden paths changed: ' + ', '.join(forbidden))
    outside = tuple(
        path
        for path in result.changed_paths
        if case.allowed_paths and not matches_any(path, case.allowed_paths)
    )
    if outside:
        reasons.append(
            'Paths outside allowed scope changed: ' + ', '.join(outside)
        )

    checks = (
        ('Build command failed.', outcome.build),
        ('Public tests failed.', outcome.public_tests),
        ('Hidden tests failed.', outcome.hidden_tests),
        ('git diff HEAD --check failed.', outcome.diff_check),
    )
    for message, evidence in checks:
        if evidence is not None and not evidence.success:
            reasons.append(message)
    if outcome.hidden_tests is None:
        reasons.append('Hidden tests were not executed.')
    return reasons


def copy_trajectory(path: Path, output_dir: Path, case_id: str) -> str:
    timestamp = datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')
    destination = output_dir / f'{case_id}-{timestamp}.jsonl'
    shutil.copy2(path, destination)
    return str(destination.resolve())


def write_outcome(outcome: EvalOutcome, output_dir: Path) -> Path:
    path = output_dir / f'{outcome.case_id}-latest.json'
    path.write_text(
        json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return path


def discover_case_paths(cases_dir: Path) -> tuple[Path, ...]:
    return tuple(sorted(cases_dir.glob('*.yaml')))


def select_cases(
    cases_dir: Path,
    selectors: Sequence[str],
) -> list[tuple[Path, EvalCase]]:
    available = [
        (path, load_case(path)) for path in discover_case_paths(cases_dir)
    ]
    if not selectors:
        return available
    selected: list[tuple[Path, EvalCase]] = []
    for selector in selectors:
        match = next(
            (
                item
                for item in available
                if item[1].id == selector or item[0].name == selector
            ),
            None,
        )
        if match is None:
            raise ValueError(f'Unknown evaluation case: {selector}')
        if match not in selected:
            selected.append(match)
    return selected


async def run_selected_cases(
    cases: Sequence[tuple[Path, EvalCase]],
    output_dir: Path,
) -> list[EvalOutcome]:
    outcomes: list[EvalOutcome] = []
    for _, case in cases:
        print(f'[{case.id}] running', flush=True)
        outcome = await run_case(case, output_dir=output_dir)
        outcomes.append(outcome)
        state = 'PASS' if outcome.passed else 'FAIL'
        print(f'[{case.id}] {state}', flush=True)
        for reason in outcome.reasons:
            print(f'  - {reason}', flush=True)
    return outcomes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Run ForgeCode cases in disposable local workspaces.'
    )
    parser.add_argument(
        '--case',
        action='append',
        default=[],
        help='Case ID or YAML filename. Repeat to run multiple cases.',
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List available case IDs without running them.',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=PROJECT_ROOT / '.forge' / 'evals',
        help='Directory for JSON reports and copied JSONL trajectories.',
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    cases_dir = PROJECT_ROOT / 'evals' / 'cases'
    try:
        cases = select_cases(cases_dir, arguments.case)
    except (ValueError, OSError) as error:
        print(f'Error: {error}')
        return 2

    if arguments.list:
        for path, case in cases:
            print(f'{case.id}\t{path.name}')
        return 0
    if not cases:
        print('No evaluation cases found.')
        return 2

    outcomes = asyncio.run(
        run_selected_cases(cases, arguments.output_dir.resolve())
    )
    passed = sum(outcome.passed for outcome in outcomes)
    print(f'\nResult: {passed}/{len(outcomes)} cases passed.', flush=True)
    return 0 if passed == len(outcomes) else 1


if __name__ == '__main__':
    raise SystemExit(main())
