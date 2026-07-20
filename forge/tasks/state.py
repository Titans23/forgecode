'''Provider-neutral task state kept outside conversation history.'''

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


TaskStatus = Literal['in_progress', 'completed', 'blocked', 'stuck']
StepStatus = Literal['pending', 'in_progress', 'completed', 'blocked']


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
    planned: bool = False
    current_step_id: str | None = None
    steps: tuple[TaskStep, ...] = ()
    constraints: tuple[str, ...] = ()
    scope_hints: tuple[str, ...] = ()
    blocked_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActiveTask:
        return cls(
            id=str(data['id']),
            goal=str(data['goal']),
            status=str(data.get('status', 'in_progress')),  # type: ignore[arg-type]
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
            blocked_reasons=tuple(
                str(item) for item in data.get('blocked_reasons', [])
            ),
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
