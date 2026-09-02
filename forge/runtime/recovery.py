'''Small deterministic state for one active Agent Loop recovery.'''

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal


RecoveryKind = Literal[
    'none',
    'protocol',
    'edit',
    'verify',
    'dependency',
    'process',
    'stagnation',
]
RecoveryAction = Literal['none', 'inspect', 'act', 'verify', 'finish']


@dataclass(slots=True)
class RecoveryState:
    '''Describe the single recovery obligation active for a turn.'''

    kind: RecoveryKind = 'none'
    fingerprint: str = ''
    attempts: int = 0
    last_progress_revision: int = 0
    required_next_action: RecoveryAction = 'none'

    @property
    def active(self) -> bool:
        return self.kind != 'none'

    def matches(
        self,
        kind: RecoveryKind | None = None,
        action: RecoveryAction | None = None,
    ) -> bool:
        return bool(
            self.active
            and (kind is None or self.kind == kind)
            and (action is None or self.required_next_action == action)
        )

    def activate(
        self,
        kind: RecoveryKind,
        action: RecoveryAction,
        *,
        fingerprint: str,
        revision: int,
    ) -> None:
        same_failure = bool(
            self.kind == kind
            and self.fingerprint == fingerprint
            and self.last_progress_revision == revision
        )
        self.kind = kind
        self.fingerprint = fingerprint
        self.attempts = self.attempts + 1 if same_failure else 1
        self.last_progress_revision = revision
        if same_failure and self.attempts >= 2:
            alternate: RecoveryAction = action
            if action == 'inspect':
                alternate = 'act'
            elif action in {'act', 'verify'}:
                alternate = 'inspect'
            self.required_next_action = alternate
        else:
            self.required_next_action = action

    def transition(self, action: RecoveryAction) -> None:
        if self.active:
            self.required_next_action = action

    def clear(self) -> None:
        self.kind = 'none'
        self.fingerprint = ''
        self.attempts = 0
        self.required_next_action = 'none'

    def note_progress(self, revision: int) -> None:
        if revision != self.last_progress_revision:
            self.clear()
            self.last_progress_revision = revision

    def exhausted(self, maximum_same_failure_attempts: int = 2) -> bool:
        return self.active and self.attempts >= maximum_same_failure_attempts

    def to_dict(self) -> dict[str, Any]:
        return {
            'kind': self.kind,
            'fingerprint': self.fingerprint,
            'attempts': self.attempts,
            'last_progress_revision': self.last_progress_revision,
            'required_next_action': self.required_next_action,
        }


def failure_fingerprint(
    kind: RecoveryKind,
    operation: str,
    target: str,
    error: str,
) -> str:
    '''Normalize one failure without retaining an unbounded error payload.'''
    parts = (
        kind,
        _normalize(operation),
        _normalize(target),
        _normalize(error)[:500],
    )
    return '|'.join(parts)


def _normalize(value: str) -> str:
    return re.sub(r'\s+', ' ', value.strip().casefold())
