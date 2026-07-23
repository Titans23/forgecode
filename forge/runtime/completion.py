'''Deterministic completion checks for code-changing tasks.'''

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from forge.runtime.state import VerificationEvidence
from forge.runtime.workspace import WorkspaceTracker
from forge.tools.shell import run_process


@dataclass(frozen=True, slots=True)
class TaskPolicy:
    '''Explicit requirements supplied by a caller or evaluation case.'''

    require_changes: bool = False
    require_verification: bool = False
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = (
        'tests/hidden/**',
        '**/tests/hidden/**',
    )


@dataclass(frozen=True, slots=True)
class CompletionDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()


def verification_command_runs_tests(command: str) -> bool:
    '''Return whether a verification command invokes a recognizable test runner.'''
    normalized = ' '.join(command.casefold().split())
    markers = (
        'pytest',
        'unittest',
        'npm test',
        'npm run test',
        'pnpm test',
        'yarn test',
        'bun test',
        'cargo test',
        'go test',
        'dotnet test',
        'mvn test',
        'gradle test',
        'ctest',
    )
    return any(marker in normalized for marker in markers)


def verification_command_runs_full_suite(command: str) -> bool:
    '''Return whether a test command represents an unscoped full-suite run.'''
    normalized = ' '.join(command.casefold().split())
    if not verification_command_runs_tests(normalized):
        return False
    if 'pytest' not in normalized:
        return True
    pytest_arguments = normalized.split('pytest', maxsplit=1)[1]
    focused_markers = (
        ' -k ',
        ' -m ',
        '::',
        '.py',
        ' tests/',
        ' test/',
        ' --version',
        ' --help',
        ' --collect-only',
    )
    return not any(marker in pytest_arguments for marker in focused_markers)


class CompletionGate:
    '''Reject final answers that violate the active task policy.'''

    def __init__(
        self,
        root: Path,
        policy: TaskPolicy | None = None,
    ) -> None:
        self.root = root.resolve()
        self.policy = policy or TaskPolicy()

    async def evaluate(
        self,
        tracker: WorkspaceTracker,
        verification: VerificationEvidence | None,
        *,
        mutation_attempted: bool,
        require_verification: bool = False,
        require_tests: bool = False,
        require_full_tests: bool = False,
    ) -> CompletionDecision:
        changed_paths = tracker.changed_paths
        verification_required = (
            self.policy.require_verification or require_verification
        )
        code_task = (
            mutation_attempted
            or self.policy.require_changes
            or verification_required
            or bool(changed_paths)
            or verification is not None
        )
        if not code_task:
            return CompletionDecision(allowed=True)

        reasons: list[str] = []
        if not tracker.available:
            reasons.append(
                'Git workspace tracking is unavailable for this task.'
            )
        if (
            self.policy.require_changes
            or mutation_attempted
        ) and not changed_paths:
            reasons.append(
                'The task requires a code change, but the final Diff is empty.'
            )

        reasons.extend(self._path_violations(changed_paths))

        if verification_required:
            if verification is None:
                reasons.append(
                    'The current code has not been verified with the verify tool.'
                )
            elif not verification.success:
                reasons.append(
                    f'The latest verification failed with exit code '
                    f'{verification.exit_code}.'
                )
            elif verification.workspace_revision != tracker.revision:
                reasons.append(
                    'The code changed after verification; run verify again for '
                    f'workspace revision {tracker.revision}.'
                )
            elif require_tests and not verification_command_runs_tests(
                verification.command
            ):
                reasons.append(
                    'The user explicitly requested tests, but the latest '
                    'verification command did not run a recognized test runner.'
                )
            elif require_full_tests and not verification_command_runs_full_suite(
                verification.command
            ):
                reasons.append(
                    'The user explicitly requested the full test suite, but '
                    'the latest verification command was focused or partial.'
                )
        elif (
            verification is not None
            and verification.workspace_revision == tracker.revision
            and not verification.success
        ):
            reasons.append(
                f'The latest verification failed with exit code '
                f'{verification.exit_code}.'
            )

        if changed_paths and tracker.available:
            reasons.extend(
                await self._diff_check_reasons(changed_paths)
            )

        return CompletionDecision(
            allowed=not reasons,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    async def _diff_check_reasons(
        self,
        changed_paths: tuple[str, ...],
    ) -> list[str]:
        tracked: list[str] = []
        untracked: list[str] = []
        for path in changed_paths:
            listed = await run_process(
                ['git', 'ls-files', '--error-unmatch', '--', path],
                cwd=self.root,
                timeout_seconds=30,
            )
            if listed.exit_code == 0:
                tracked.append(path)
            elif (self.root / path).is_file():
                untracked.append(path)

        reasons: list[str] = []
        if tracked:
            diff_check = await run_process(
                [
                    'git',
                    'diff',
                    'HEAD',
                    '--check',
                    '--',
                    *tracked,
                ],
                cwd=self.root,
                timeout_seconds=30,
            )
            if diff_check.exit_code != 0:
                reasons.append(
                    'git diff --check found a deterministic Patch error.'
                )

        for path in untracked:
            diff_check = await run_process(
                [
                    'git',
                    'diff',
                    '--no-index',
                    '--check',
                    '--',
                    '/dev/null',
                    path,
                ],
                cwd=self.root,
                timeout_seconds=30,
            )
            if (
                diff_check.exit_code not in {0, 1}
                or diff_check.stdout.strip()
            ):
                reasons.append(
                    'Git whitespace checking found a deterministic Patch '
                    f'error in untracked file: {path}.'
                )
        return reasons

    def _path_violations(self, paths: tuple[str, ...]) -> list[str]:
        reasons: list[str] = []
        forbidden = tuple(
            path
            for path in paths
            if matches_any(path, self.policy.forbidden_paths)
        )
        if forbidden:
            reasons.append(
                'Forbidden paths were modified: ' + ', '.join(forbidden)
            )

        if self.policy.allowed_paths:
            outside = tuple(
                path
                for path in paths
                if not matches_any(path, self.policy.allowed_paths)
            )
            if outside:
                reasons.append(
                    'Paths outside the allowed scope were modified: '
                    + ', '.join(outside)
                )
        return reasons

def matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    candidate = path.replace('\\', '/')
    return any(fnmatchcase(candidate, pattern) for pattern in patterns)
