'''Provider-neutral task state kept outside conversation history.'''

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal
from hashlib import sha256
import json


def anchored_criterion(criterion: dict[str, str], task_id: str) -> dict[str, str]:
    '''Stable identity for a proposal, never an authorization grant.'''
    value = {key: str(criterion.get(key, '')) for key in ('source_quote', 'condition', 'check')}
    clause = str(criterion.get('clause_id', ''))
    identity = json.dumps([task_id, value['source_quote'].strip(), clause], ensure_ascii=False)
    # Preserve legacy IDs so already persisted evidence remains attributable.
    return {**value, 'clause_id': clause,
            'id': criterion.get('id') or 'req-' + sha256(identity.encode()).hexdigest()[:20],
            'origin': 'model_proposal'}


TaskStatus = Literal['in_progress', 'completed', 'blocked', 'stuck', 'failed']
StepStatus = Literal['pending', 'in_progress', 'completed', 'blocked']
ScopeSource = Literal[
    'repository',
    'explicit',
    'inherited',
    'planned',
    'unresolved',
]


@dataclass(frozen=True, slots=True)
class TaskStep:
    id: str
    title: str
    status: StepStatus = 'pending'
    evidence: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskStep:
        return cls(
            id=str(data['id']),
            title=str(data['title']),
            status=str(data.get('status', 'pending')),  # type: ignore[arg-type]
            evidence=tuple(str(item) for item in data.get('evidence', [])),
        )


@dataclass(frozen=True, slots=True)
class ActiveTask:
    id: str
    goal: str
    status: TaskStatus = 'in_progress'
    requires_change: bool = False
    planned: bool = False
    current_step_id: str | None = None
    steps: tuple[TaskStep, ...] = ()
    constraints: tuple[str, ...] = ()
    scope_hints: tuple[str, ...] = ()
    scope_source: ScopeSource = 'repository'
    workspace_paths: tuple[str, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    acceptance_criteria: tuple[dict[str, str], ...] = ()
    user_directives: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActiveTask:
        return cls(
            id=str(data['id']),
            goal=str(data['goal']),
            status=str(data.get('status', 'in_progress')),  # type: ignore[arg-type]
            requires_change=bool(data.get('requires_change', False)),
            planned=bool(data.get('planned', False)),
            current_step_id=(
                str(data['current_step_id'])
                if data.get('current_step_id') is not None
                else None
            ),
            steps=tuple(
                TaskStep.from_dict(item)
                for item in data.get('steps', [])
                if isinstance(item, dict)
            ),
            constraints=tuple(
                str(item) for item in data.get('constraints', [])
            ),
            scope_hints=tuple(
                str(item) for item in data.get('scope_hints', [])
            ),
            scope_source=str(  # type: ignore[arg-type]
                data.get(
                    'scope_source',
                    'explicit' if data.get('scope_hints') else 'repository',
                )
            ),
            workspace_paths=tuple(
                str(item) for item in data.get('workspace_paths', [])
            ),
            blocked_reasons=tuple(
                str(item) for item in data.get('blocked_reasons', [])
            ),
            acceptance_criteria=tuple(
                anchored_criterion(item, str(data['id']))
                for item in data.get('acceptance_criteria', []) if isinstance(item, dict)
            ),
            user_directives=tuple(str(item) for item in data.get('user_directives', [])),
        )

    @property
    def current_step(self) -> TaskStep | None:
        return next(
            (
                step
                for step in self.steps
                if step.id == self.current_step_id
            ),
            None,
        )
