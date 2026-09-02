'''Deterministic completion checks for code-changing tasks.'''

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
import re
from typing import Literal

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
        verification_history: tuple[VerificationEvidence, ...] = (),
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
            or bool(verification_history)
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

        evidence_history = list(verification_history)
        if verification is not None and (
            not evidence_history or evidence_history[-1] != verification
        ):
            evidence_history.append(verification)
        current_evidence = tuple(
            item
            for item in evidence_history
            if item.workspace_revision == tracker.revision
        )
        successful_evidence = tuple(
            item for item in current_evidence if item.success
        )
        unresolved_failures = unresolved_verification_failures(
            current_evidence
        )

        if verification_required:
            if not successful_evidence:
                reasons.append(
                    'The current code has not been verified with the verify tool.'
                )
            elif (
                self.policy.require_task_verification
                and not any(
                    is_task_verification_command(item.command)
                    for item in successful_evidence
                )
            ):
                reasons.append(
                    'The latest verification is only a structural or no-op '
                    'check; run a task-level test, build, lint, type-check, '
                    'syntax check, or other command that exercises the requested '
                    'behavior.'
                )
            if (
                successful_evidence
                and self.policy.required_verification_commands
                and not any(
                    fnmatchcase(
                        item.command.strip(),
                        pattern,
                    )
                    for item in successful_evidence
                    for pattern in self.policy.required_verification_commands
                )
            ):
                reasons.append(
                    'The latest verification command does not match the '
                    'task-required verification command contract.'
                )
            if (
                evidence_history
                and not current_evidence
            ):
                reasons.append(
                    'The code changed after verification; run verify again for '
                    f'workspace revision {tracker.revision}.'
                )
        if unresolved_failures:
            rendered = ', '.join(
                f'{item.command!r} (exit {item.exit_code})'
                for item in unresolved_failures
            )
            reasons.append(
                'The latest verification failed and remains unresolved on the '
                f'current workspace revision: {rendered}.'
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
    r'python(?:\d+(?:\.\d+)?)?\s+-m\s+(?:py_compile|compileall)\b|'
    r'(?:test\s+-(?:e|f|d)|find|ls|dir|cat|type|head|tail|wc|stat)\b|'
    r'(?:echo|printf|pwd|true|:)\b)',
    re.IGNORECASE,
)

VerificationKind = Literal['structural', 'behavior']


def verification_kind(command: str) -> VerificationKind:
    '''Classify whether a command exercises behavior or only structure.'''
    segments = re.split(r'\s*(?:&&|\|\||[;|])\s*', command.strip())
    return (
        'behavior'
        if any(
            segment and not _NON_TASK_VERIFICATION.match(segment.strip())
            for segment in segments
        )
        else 'structural'
    )


def is_task_verification_command(command: str) -> bool:
    '''Return whether a command exercises the requested task, not only plumbing.'''
    return verification_kind(command) == 'behavior'


def verification_command_key(command: str, cwd: str) -> str:
    '''Return a stable identity for retries of one verification obligation.'''
    normalized = ' '.join(command.casefold().split())
    normalized_cwd = cwd.strip().replace('\\', '/').casefold() or '.'
    return f'{normalized_cwd}\0{normalized}'


def unresolved_verification_failures(
    evidence: tuple[VerificationEvidence, ...],
) -> tuple[VerificationEvidence, ...]:
    '''Keep failures until the same command succeeds on the same revision.'''
    unresolved: dict[str, VerificationEvidence] = {}
    for item in evidence:
        key = verification_command_key(item.command, item.cwd)
        if item.success:
            unresolved.pop(key, None)
        else:
            unresolved[key] = item
    return tuple(unresolved.values())
