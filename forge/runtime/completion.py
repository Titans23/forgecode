'''Deterministic completion checks for code-changing tasks.'''

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
import re

from forge.runtime.state import VerificationEvidence
from forge.runtime.workspace import WorkspaceTracker
from forge.tools.shell import run_process


@dataclass(frozen=True, slots=True)
class TaskPolicy:
    '''Explicit requirements supplied by a caller or evaluation case.'''

    require_changes: bool = False
    require_verification: bool = False
    require_task_verification: bool = False
    allowed_paths: tuple[str, ...] = ()
    required_paths: tuple[str, ...] = ()
    required_verification_commands: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = (
        'tests/hidden/**',
        '**/tests/hidden/**',
    )


@dataclass(frozen=True, slots=True)
class CompletionDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()


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
    ) -> CompletionDecision:
        changed_paths = tracker.changed_paths
        verification_required = (
            self.policy.require_verification
            or self.policy.require_task_verification
            or bool(self.policy.required_verification_commands)
            or require_verification
        )
        code_task = (
            mutation_attempted
            or self.policy.require_changes
            or verification_required
            or bool(changed_paths)
            or verification is not None
            or bool(self.policy.required_paths)
        )
        if not code_task:
            return CompletionDecision(allowed=True)

        reasons: list[str] = []
        if not tracker.available:
            reasons.append(
                'Workspace change tracking is unavailable for this task.'
            )
        if (
            self.policy.require_changes
            or mutation_attempted
        ) and not changed_paths:
            reasons.append(
                'The task requires a code change, but the final Diff is empty.'
            )

        reasons.extend(self._path_violations(changed_paths))
        reasons.extend(self._required_path_reasons())

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
            elif (
                self.policy.require_task_verification
                and not is_task_verification_command(verification.command)
            ):
                reasons.append(
                    'The latest verification is only a structural or no-op '
                    'check; run a task-level test, build, lint, type-check, '
                    'syntax check, or other command that exercises the requested '
                    'behavior.'
                )
            if (
                verification is not None
                and self.policy.required_verification_commands
                and not any(
                    fnmatchcase(
                        verification.command.strip(),
                        pattern,
                    )
                    for pattern in self.policy.required_verification_commands
                )
            ):
                reasons.append(
                    'The latest verification command does not match the '
                    'task-required verification command contract.'
                )
            if (
                verification is not None
                and verification.workspace_revision != tracker.revision
            ):
                reasons.append(
                    'The code changed after verification; run verify again for '
                    f'workspace revision {tracker.revision}.'
                )
        elif verification is not None and not verification.success:
            reasons.append(
                f'The latest verification failed with exit code '
                f'{verification.exit_code}.'
            )
            if verification.workspace_revision != tracker.revision:
                reasons.append(
                    'The code changed after the failed verification; run verify '
                    f'again for workspace revision {tracker.revision}.'
                )

        if changed_paths and tracker.git_available:
            reasons.extend(
                await self._diff_check_reasons(changed_paths)
            )

        return CompletionDecision(
            allowed=not reasons,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    def _required_path_reasons(self) -> list[str]:
        reasons: list[str] = []
        for raw_path in self.policy.required_paths:
            candidate = (self.root / raw_path).resolve(strict=False)
            try:
                candidate.relative_to(self.root)
            except ValueError:
                reasons.append(
                    f'Required task artifact is outside the workspace: {raw_path}.'
                )
                continue
            if not candidate.exists():
                reasons.append(
                    f'Required task artifact does not exist: {raw_path}.'
                )
        return reasons

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
                detail = (
                    diff_check.stdout.strip()
                    or diff_check.stderr.strip()
                )[:2_000]
                reasons.append(
                    'git diff --check found a deterministic Patch error.'
                    + (f'\n{detail}' if detail else '')
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
                detail = (
                    diff_check.stdout.strip()
                    or diff_check.stderr.strip()
                )[:2_000]
                reasons.append(
                    'Git whitespace checking found a deterministic Patch '
                    f'error in untracked file: {path}.'
                    + (f'\n{detail}' if detail else '')
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


_NON_TASK_VERIFICATION = re.compile(
    r'^(?:git\s+(?:status|diff(?:\s+--check)?|log)\b|'
    r'(?:echo|printf|pwd|true|:)\b)',
    re.IGNORECASE,
)


def is_task_verification_command(command: str) -> bool:
    '''Return whether a command exercises the requested task, not only plumbing.'''
    segments = re.split(r'\s*(?:&&|\|\||[;|])\s*', command.strip())
    return any(
        segment and not _NON_TASK_VERIFICATION.match(segment.strip())
        for segment in segments
    )
