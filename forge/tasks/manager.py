'''Active goal anchoring and optional plan lifecycle.'''

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from forge.tasks.state import ActiveTask, StepStatus, TaskStep
from forge.tasks.store import TaskStore


class TaskManager:
    '''Keep every turn anchored while persisting only explicit plans.'''

    def __init__(self, root: Path, store: TaskStore | None = None) -> None:
        self.root = root.resolve()
        self.store = store or TaskStore(self.root)
        self.active: ActiveTask | None = None
        self._resume_next_turn = False

    def begin_turn(self, goal: str) -> ActiveTask:
        if self._resume_next_turn and self.active is not None:
            self._resume_next_turn = False
            return self.active
        return self.start(goal)

    def start(self, goal: str) -> ActiveTask:
        clean_goal = goal.strip()
        if not clean_goal:
            raise ValueError('Task goal must not be empty.')
        self.active = ActiveTask(
            id=f'task-{uuid4().hex[:12]}',
            goal=clean_goal,
        )
        self._resume_next_turn = False
        return self.active

    def plan(
        self,
        steps: list[str],
        *,
        constraints: list[str] | None = None,
        scope_hints: list[str] | None = None,
        replace_existing: bool = False,
    ) -> ActiveTask:
        task = self._require_active()
        if task.planned and not replace_existing:
            raise ValueError(
                'The current task already has a plan. Update its steps instead.'
            )
        clean_steps = clean_strings(steps, name='steps', maximum=20)
        if len(clean_steps) < 2:
            raise ValueError('A task plan requires at least two steps.')
        planned_steps = tuple(
            TaskStep(
                id=f'step-{index}',
                title=title,
                status='in_progress' if index == 1 else 'pending',
            )
            for index, title in enumerate(clean_steps, start=1)
        )
        self.active = replace(
            task,
            status='in_progress',
            planned=True,
            current_step_id=planned_steps[0].id,
            steps=planned_steps,
            constraints=tuple(
                clean_strings(constraints or [], name='constraints')
            ),
            scope_hints=tuple(
                clean_strings(scope_hints or [], name='scope_hints')
            ),
            blocked_reasons=(),
        )
        self.store.save(self.active)
        return self.active

    def update_step(
        self,
        step_id: str,
        status: StepStatus,
        *,
        evidence: list[str] | None = None,
    ) -> ActiveTask:
        task = self._require_planned()
        if status not in {'pending', 'in_progress', 'completed', 'blocked'}:
            raise ValueError(f'Unsupported step status: {status}')
        target = next(
            (step for step in task.steps if step.id == step_id),
            None,
        )
        if target is None:
            raise ValueError(f'Task step not found: {step_id}')
        additions = clean_strings(evidence or [], name='evidence')
        updated_steps = [
            replace(
                step,
                status=(
                    'pending'
                    if status == 'in_progress'
                    and step.status == 'in_progress'
                    and step.id != step_id
                    else step.status
                ),
            )
            for step in task.steps
        ]
        updated_steps = [
            replace(
                step,
                status=status,
                evidence=tuple(dict.fromkeys((*step.evidence, *additions))),
            )
            if step.id == step_id
            else step
            for step in updated_steps
        ]
        current_step_id = task.current_step_id
        if status == 'in_progress':
            current_step_id = step_id
        elif step_id == current_step_id and status == 'completed':
            next_step = next(
                (step for step in updated_steps if step.status == 'pending'),
                None,
            )
            if next_step is not None:
                updated_steps = [
                    replace(step, status='in_progress')
                    if step.id == next_step.id
                    else step
                    for step in updated_steps
                ]
                current_step_id = next_step.id
            else:
                current_step_id = None
        self.active = replace(
            task,
            current_step_id=current_step_id,
            steps=tuple(updated_steps),
        )
        self.store.save(self.active)
        return self.active

    def complete(self) -> ActiveTask | None:
        if self.active is None:
            return None
        steps = tuple(
            replace(step, status='completed')
            if step.status != 'blocked'
            else step
            for step in self.active.steps
        )
        self.active = replace(
            self.active,
            status='completed',
            current_step_id=None,
            steps=steps,
            blocked_reasons=(),
        )
        if self.active.planned:
            self.store.save(self.active)
        return self.active

    def block(self, reasons: tuple[str, ...]) -> ActiveTask | None:
        if self.active is None:
            return None
        self.active = replace(
            self.active,
            status='blocked',
            blocked_reasons=tuple(dict.fromkeys(reasons)),
        )
        if self.active.planned:
            self.store.save(self.active)
        return self.active

    def resume(self, task_id: str) -> ActiveTask:
        task = self.store.load(task_id)
        self.active = replace(
            task,
            status='in_progress',
            blocked_reasons=(),
        )
        self._resume_next_turn = True
        self.store.save(self.active)
        return self.active

    def system_suffix(self) -> str:
        task = self.active
        if task is None:
            return ''
        lines = [
            '[Current ForgeCode Task]',
            '',
            'Goal:',
            bounded(task.goal, 20_000),
            '',
            f'Status: {task.status}',
        ]
        if task.current_step is not None:
            lines.extend(
                ['', 'Current step:', task.current_step.title]
            )
        completed = [
            step.title for step in task.steps if step.status == 'completed'
        ]
        if completed:
            lines.extend(
                ['', 'Completed steps:', *[f'- {item}' for item in completed]]
            )
        if task.constraints:
            lines.extend(
                ['', 'Constraints:', *[f'- {item}' for item in task.constraints]]
            )
        if task.scope_hints:
            lines.extend(
                ['', 'Focus paths:', *[f'- {item}' for item in task.scope_hints]]
            )
        lines.extend(
            [
                '',
                'Continue this task. Do not switch to unrelated work.',
            ]
        )
        return '\n'.join(lines)

    def describe(self) -> str:
        task = self.active
        if task is None:
            return 'No active task.'
        completed = sum(
            step.status == 'completed' for step in task.steps
        )
        current = task.current_step.title if task.current_step else 'none'
        return (
            f'id: {task.id}\n'
            f'status: {task.status}\n'
            f'goal: {task.goal}\n'
            f'planned: {str(task.planned).lower()}\n'
            f'current step: {current}\n'
            f'progress: {completed}/{len(task.steps)}'
        )

    def history(self) -> str:
        tasks = self.store.list()
        if not tasks:
            return 'No persisted tasks.'
        return '\n'.join(
            f'- {task.id} [{task.status}]: {task.goal}'
            for task in tasks
        )

    def _require_active(self) -> ActiveTask:
        if self.active is None:
            raise ValueError('No active task.')
        return self.active

    def _require_planned(self) -> ActiveTask:
        task = self._require_active()
        if not task.planned:
            raise ValueError('The current task does not have a plan.')
        return task


def clean_strings(
    values: list[str],
    *,
    name: str,
    maximum: int = 50,
) -> list[str]:
    if len(values) > maximum:
        raise ValueError(f'{name} may contain at most {maximum} items.')
    cleaned = [str(value).strip() for value in values]
    if any(not value for value in cleaned):
        raise ValueError(f'{name} must not contain empty items.')
    if any(len(value) > 1_000 for value in cleaned):
        raise ValueError(f'{name} items are limited to 1000 characters.')
    return list(dict.fromkeys(cleaned))


def bounded(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    return value[:maximum] + '\n[Task goal truncated for model context.]'
