'''Run ForgeCode evaluation cases in disposable local Git workspaces.'''

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from io import BytesIO
import json
from hashlib import sha256
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
from time import monotonic
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
import yaml

from forge.api import HarnessSession, RunProfile, SessionOptions
from forge.config import ForgeConfig, model_environment
from forge.runtime.completion import TaskPolicy, matches_any
from forge.runtime.model_client import ModelClient
from forge.runtime.state import ToolCall, TurnResult
from forge.sandbox.path_policy import inspect_path
from forge.tools.shell import ProcessResult
from forge.sessions.store import SessionStore
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EvalCase(BaseModel):
    '''Validated schema for one YAML evaluation case.'''

    model_config = ConfigDict(extra='forbid')

    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    repo: str = Field(min_length=1)
    base_commit_repository: str = '.'
    base_commit: str = Field(min_length=1)
    source_archive_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    task: str = Field(min_length=1)
    setup_command: str | None = None
    build_command: str | None = None
    test_command: str = Field(min_length=1)
    hidden_test_command: str = Field(min_length=1)
    timeout_seconds: float = Field(default=300, gt=0, le=1800)
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ('tests/hidden/**',)
    success_criteria: dict[str, bool] = Field(default_factory=dict)
    max_model_calls: int = Field(default=80, gt=0, strict=True)
    max_tool_calls: int = Field(default=120, gt=0, strict=True)
    max_turn_seconds: int = Field(default=1800, gt=0, strict=True)
    intent_routing: bool = True

    @field_validator('repo')
    @classmethod
    def relative_fixture(cls, value):
        path = PurePosixPath(value)
        if path.is_absolute() or '..' in path.parts or str(path) != value or '\\' in value or ':' in value:
            raise ValueError('Fixture repository must be a canonical relative path')
        return value


@dataclass(frozen=True, slots=True)
class CommandEvidence:
    '''Compact evaluator-owned evidence for one shell command.'''

    command: str
    exit_code: int
    duration_seconds: float
    timed_out: bool
    stdout: str
    stderr: str
    execution_boundary: str = 'host-process'

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
    run_id: str = field(default_factory=lambda: uuid4().hex)
    stop_reason: str = ''
    model_calls: int | None = None
    usage: dict[str, Any] | None = None
    session_id: str | None = None
    journal: str | None = None
    cleanup: dict[str, Any] | None = None
    run_configuration: dict[str, Any] = field(default_factory=dict)
    retained_workspace: str | None = None
    independent_grading: dict[str, Any] | None = None
    agent_seconds: float | None = None
    total_seconds: float | None = None
    request_evidence: dict[str, Any] | None = None
    statistics: dict[str, Any] | None = None

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
        self._temporary = TemporaryDirectory(prefix=f'forge-{case.id}-', delete=False)
        self.root = Path(self._temporary.name)
        self.retain = False
        if sys.platform == 'win32':
            from forge.sandbox.windows_worker import grant_controller_access
            grant_controller_access(self.root)
        self.source_root = self.root / 'source'
        self.workspace = self.source_root / PurePosixPath(case.repo)
        self.hidden_files: tuple[HiddenFile, ...] = ()

    def __enter__(self) -> PreparedFixture:
        try:
            return self._prepare()
        except BaseException:
            self._temporary.cleanup()
            raise

    def _prepare(self) -> PreparedFixture:
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
        if self.case.source_archive_sha256 is not None and sha256(archive.stdout).hexdigest() != self.case.source_archive_sha256:
            raise ValueError('Evaluation source archive differs from the frozen input')
        with tarfile.open(fileobj=BytesIO(archive.stdout), mode='r:') as file:
            self.source_root.mkdir()
            file.extractall(self.source_root, filter='data')
        if not self.workspace.is_dir():
            raise RuntimeError(
                f'Fixture path missing from archive: {self.case.repo}'
            )
        self.hidden_files = remove_hidden_tests(self.workspace)
        initialize_workspace_git(self.workspace)
        self._workspace_anchor = inspect_path(self.workspace, absolute=True)
        return self

    def restore_hidden_tests(self) -> None:
        self._workspace_anchor.assert_current()
        for hidden in self.hidden_files:
            destination = self.workspace / PurePosixPath(hidden.path)
            inspect_path(destination, absolute=True).assert_current()
            destination.parent.mkdir(parents=True, exist_ok=True)
            inspect_path(destination, absolute=True).assert_current()
            destination.write_bytes(hidden.content)

    def __exit__(self, *args: object) -> None:
        if not self.retain:
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
    timeout_seconds: float,
    *, session: HarnessSession,
) -> CommandEvidence:
    result = await session.backend.execute(ToolCall(0, 'grader-' + uuid4().hex, 'run_command',
        {'command': command, 'timeout_seconds': timeout_seconds}), session.conversation.registry)
    metadata = result.metadata
    return CommandEvidence(command=command, exit_code=metadata.get('exit_code', 125),
        duration_seconds=metadata.get('duration_seconds', 0), timed_out=metadata.get('timed_out', False),
        stdout=bounded_output(metadata.get('stdout', '')),
        stderr=bounded_output(metadata.get('stderr', '') or (result.summary if not result.success else '')),
        execution_boundary=metadata.get('execution_boundary', session.backend.mode))


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
    session: HarnessSession,
) -> TurnResult:
    return await session.run(build_agent_prompt(case))


async def run_case(
    case: EvalCase,
    *,
    project_root: Path = PROJECT_ROOT,
    output_dir: Path | None = None,
    client: ModelClient | None = None,
    config: ForgeConfig | None = None,
    sandbox: str = 'workspace-write',
    model_client_factory=None,
    approval_handler=None,
    fixture_setup=None,
    fixture_capture=None,
    final_grader=None,
) -> EvalOutcome:
    '''Run one case; an optional independent grader runs only after clean session close.

    fixture_setup(prepared) prepares trusted dependencies before creating clients.
    fixture_capture(prepared, outcome) preserves candidate evidence after clean
    close, including exceptions/cancellation without a terminal model result.
    final_grader(case, prepared, result, outcome) grades a separate evaluator copy;
    common acceptance (including budget/terminal status) remains authoritative.
    '''
    started = monotonic()
    destination = (
        output_dir
        if output_dir is not None
        else project_root / '.forge' / 'evals'
    )
    destination.mkdir(parents=True, exist_ok=True)
    outcome = EvalOutcome(case_id=case.id)
    try:
        if client is not None and model_client_factory is not None:
            raise ValueError('Choose client or model_client_factory, not both')
        if config is None:
            if client is None:
                config = ForgeConfig.from_env(model_environment(project_root / '.env'))
            else:
                provider = getattr(client, 'provider', 'anthropic')
                config = ForgeConfig(api_key='injected-client', model_id=getattr(client, 'model', 'scripted-client'),
                    provider=provider if provider in {'anthropic', 'deepseek', 'openai_responses'} else 'anthropic')
        profile = RunProfile(name='evaluation', max_model_calls=case.max_model_calls,
            max_tool_calls=case.max_tool_calls, wall_seconds=case.max_turn_seconds,
            intent_routing=case.intent_routing if client is None else False)
        outcome.run_configuration = {'provider': config.provider, 'model': config.model_id,
            'profile': asdict(profile), 'sandbox': sandbox, 'injected_client': client is not None,
            'base_commit': case.base_commit, 'repo': case.repo, 'verifier_timeout_seconds': case.timeout_seconds}
        with PreparedFixture(project_root, case) as prepared:
            workspace = prepared.workspace
            session = None
            result = None
            try:
                if fixture_setup is not None:
                    outcome.setup = await fixture_setup(prepared)
                    if not outcome.setup.success:
                        outcome.stop_reason = 'setup_failed'
                        outcome.reasons.append('Setup command failed.')
                        return outcome
                options = SessionOptions(workspace, sandbox=sandbox, approval='auto', config=config, profile=profile,
                    data_root=destination.resolve() / 'sessions' / outcome.run_id)
                policy = TaskPolicy(require_changes=True, require_verification=True,
                    allowed_paths=case.allowed_paths, forbidden_paths=case.forbidden_paths)
                session = await HarnessSession.open(options, task_policy=policy, approval_handler=approval_handler,
                    model_client_factory=(lambda *args, **kwargs: client) if client is not None else model_client_factory)
                outcome.session_id = session.session_id
                outcome.journal = str(session.journal.path)
                outcome.trajectory = str(session.recorder.path)
                async with session:
                    if case.setup_command is not None:
                        outcome.setup = await execute_command(case.setup_command, case.timeout_seconds, session=session)
                        if not outcome.setup.success:
                            outcome.reasons.append('Setup command failed.')
                    if outcome.reasons:
                        # No agent attempt after failed setup; still close the
                        # admitted boundary and preserve a failed case report.
                        outcome.stop_reason = 'setup_failed'
                    else:
                        agent_started = monotonic()
                        try:
                            result = await run_agent(case, session)
                        finally:
                            outcome.agent_seconds = monotonic() - agent_started
                        record_agent_result(result, outcome)
                        if final_grader is None:
                            await grade_result(case, prepared, session, result, outcome)
            except BaseException as error:
                report = getattr(error, 'cleanup_report', None)
                if session is None and report is not None:
                    outcome.cleanup = report
                    if report.get('state') != 'clean':
                        prepared.retain = True
                        outcome.retained_workspace = str(workspace)
                raise
            finally:
                if session is not None:
                    outcome.cleanup = session.cleanup or {'state': 'unknown'}
                    if outcome.cleanup.get('state') != 'clean':
                        prepared.retain = True
                        outcome.retained_workspace = str(workspace)
                        outcome.reasons.append('Execution cleanup is unconfirmed; workspace retained.')
                    elif fixture_capture is not None:
                        try:
                            await fixture_capture(prepared, outcome)
                        except Exception as error:
                            prepared.retain = True
                            outcome.retained_workspace = str(workspace)
                            outcome.reasons.append('Candidate capture failed; workspace retained: ' + type(error).__name__)
            if final_grader is not None and result is not None and outcome.cleanup.get('state') == 'clean':
                await final_grader(case, prepared, result, outcome)
                outcome.reasons.extend(acceptance_reasons(case, result, outcome))
    except asyncio.CancelledError:
        outcome.task_status = 'cancelled'
        outcome.stop_reason = 'cancelled'
        outcome.error = 'CancelledError'
        outcome.reasons.append('Evaluation cancelled; no success or automatic replay.')
        raise
    except Exception as error:
        outcome.error = f'{type(error).__name__}: {error}'
        outcome.reasons.append('Evaluation runner failed.')
    finally:
        outcome.total_seconds = monotonic() - started
        if outcome.journal is not None:
            try:
                outcome.request_evidence = observed_requests(Path(outcome.journal))
            except Exception as error:
                outcome.reasons.append('Request accounting could not be verified: ' + type(error).__name__)
        outcome.reasons = list(dict.fromkeys(outcome.reasons))
        outcome.passed = not outcome.reasons and outcome.error is None and outcome.task_status == 'completed'
        write_outcome(outcome, destination)
    return outcome


def observed_requests(journal):
    """Read existing all-role observations; old root-only logs remain explicit."""
    records = SessionStore._read_records(journal)
    direct, observations, terminal_counts = [], [], []
    tool_ids = set()
    from forge.observability.usage_ledger import token_usage
    for record in records:
        kind = record.get('type')
        if kind not in {'observation', 'model_request_started', 'model_request_finished', 'tool_started', 'turn_completed'}:
            continue
        payload = SessionStore._payload(record, journal)
        if kind == 'turn_completed':
            count = (payload.get('statistics') or {}).get('model_requests')
            if count is not None:
                terminal_counts.append(count)
        elif kind != 'observation':
            direct.append((kind, payload))
        else:
            event = payload['event']
            attributes = event['attributes']
            if event['event_type'] == 'tool.intent':
                tool_ids.add(attributes['execution_id'])
            elif event['event_type'] == 'model.request.started':
                observations.append(('model_request_started', {
                    'request_id': attributes['model_request_id'], 'role': attributes['role']}))
            elif event['event_type'] in {'model.request.finished', 'model.request.failed'}:
                raw = attributes.get('raw_usage')
                usage = asdict(token_usage(attributes['provider'], raw)) if raw is not None else None
                observations.append(('model_request_finished', {
                    'request_id': attributes['model_request_id'], 'role': attributes['role'],
                    'outcome': 'completed' if event['event_type'] == 'model.request.finished' else 'failed',
                    'reason': attributes.get('error_kind', ''), 'usage': usage,
                    'usage_is_final': attributes.get('usage_is_final')}))
    all_roles = bool(observations)
    started, finished, tools = {}, {}, 0
    for kind, payload in observations if all_roles else direct:
        if kind == 'tool_started':
            tools += 1
            continue
        bucket = started if kind == 'model_request_started' else finished
        identity = payload['request_id']
        if identity in bucket:
            raise ValueError('Duplicate model request event')
        bucket[identity] = payload
    if finished.keys() - started.keys():
        raise ValueError('Finished request has no recorded start')
    totals = {key: 0 for key in ('input_tokens', 'output_tokens',
                                'cache_creation_input_tokens', 'cache_read_input_tokens')}
    unknown = []
    for identity in started:
        entry = finished.get(identity, {})
        usage = entry.get('usage')
        if usage is None or entry.get('usage_is_final') is not True:
            unknown.append(identity)
        if usage is not None:
            for key in totals:
                totals[key] += usage[key]
    expected = sum(terminal_counts) if terminal_counts else None
    complete = expected is not None and expected == len(started)
    return {'observed_requests': len(started), 'observed_tool_calls': len(tool_ids) if all_roles else tools,
            'accounting_complete': complete, 'terminal_shared_requests': expected,
            'missing_request_count': max(0, expected - len(started)) if expected is not None else None,
            'request_roles': dict(Counter(entry.get('role', 'root_unclassified') for entry in started.values())),
            'tool_scope': ('All-role Journal observation tool intents.' if all_roles else
                'Legacy root-only tool events; nested coverage is not established.'),
            'reported_usage': totals if any(entry.get('usage') is not None for entry in finished.values()) else None,
            'usage_complete': not unknown and complete, 'unknown_usage_request_ids': unknown,
            'unfinished_request_ids': sorted(started.keys() - finished.keys()),
            'request_outcomes': [{key: entry.get(key) for key in ('request_id', 'role', 'outcome', 'reason', 'usage_is_final')}
                                 for entry in finished.values()],
            'scope': ('All-role Journal observations; usage normalized by the shared provider normalizer.' if all_roles else
                'Legacy root-only Journal events; terminal shared totals checked when available.')}


def record_agent_result(result, outcome):
    outcome.task_status = result.status
    outcome.changed_paths = result.changed_paths
    outcome.stop_reason = result.stop_reason
    outcome.model_calls = result.model_calls
    outcome.usage = asdict(result.usage)
    outcome.statistics = result.statistics


async def grade_result(case, prepared, session, result, outcome):
    """Evaluator-owned commands run outside the agent loop, within its boundary."""
    if case.build_command is not None:
        outcome.build = await execute_command(case.build_command, case.timeout_seconds, session=session)
    outcome.public_tests = await execute_command(case.test_command, case.timeout_seconds, session=session)
    outcome.diff_check = await execute_command('git diff HEAD --check', 30, session=session)
    prepared.restore_hidden_tests()
    outcome.hidden_tests = await execute_command(case.hidden_test_command, case.timeout_seconds, session=session)
    outcome.reasons.extend(acceptance_reasons(case, result, outcome))


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


def write_outcome(outcome: EvalOutcome, output_dir: Path) -> Path:
    encoded = json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2)
    (output_dir / f'{outcome.case_id}-{outcome.run_id}.json').write_text(encoded, encoding='utf-8')
    path = output_dir / f'{outcome.case_id}-latest.json'
    path.write_text(
        encoded,
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
    *, sandbox: str = 'workspace-write',
) -> list[EvalOutcome]:
    outcomes: list[EvalOutcome] = []
    for _, case in cases:
        print(f'[{case.id}] running', flush=True)
        outcome = await run_case(case, output_dir=output_dir, sandbox=sandbox)
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
        '--sandbox', choices=('read-only', 'workspace-write', 'full-access'), default='workspace-write',
        help='Execution boundary for agent and graders; full-access must be explicitly selected.',
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
        run_selected_cases(cases, arguments.output_dir.resolve(), sandbox=arguments.sandbox)
    )
    passed = sum(outcome.passed for outcome in outcomes)
    print(f'\nResult: {passed}/{len(outcomes)} cases passed.', flush=True)
    return 0 if passed == len(outcomes) else 1


if __name__ == '__main__':
    raise SystemExit(main())
