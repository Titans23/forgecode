'''Optional planning tools backed by the current TaskManager.'''

from __future__ import annotations

from pathlib import Path
from dataclasses import asdict
import json
from typing import Literal

from pydantic import Field

from forge.tasks.manager import TaskManager, normalize_step_id
from forge.tools.base import Tool, ToolExecutionError, ToolInput, ToolResult


class TaskGetInput(ToolInput):
    pass


class TaskGetTool(Tool[TaskGetInput]):
    name = 'task_get'
    description = (
        'Return the current ForgeCode task and optional plan. Use only when '
        'you need to inspect task state; the current goal is already injected '
        'into every model request.'
    )
    input_model = TaskGetInput

    def __init__(self, root: Path, manager: TaskManager) -> None:
        super().__init__(root)
        self.manager = manager

    async def execute(self, arguments: TaskGetInput) -> ToolResult:
        del arguments
        return ToolResult.ok(
            'Read the current task.',
            content=self.manager.describe() + (
                '\n\nExecuted check history (historical success is not current acceptance):\n' +
                json.dumps([asdict(item) for item in self.manager.evidence_provider()], ensure_ascii=False)
                if self.manager.evidence_provider is not None else ''),
        )


class AcceptanceCriterion(ToolInput):
    clause_id: str = Field(default='', max_length=100, description='Stable subclause name when one quote contains distinct requirements; reuse it on retries.')
    source_quote: str = Field(min_length=1, max_length=2000, description='Exact quote from the original user goal, not a guessed requirement.')
    condition: str = Field(min_length=1, max_length=2000, description='Observable output, interface, preservation or allowed-transformation condition.')
    check: str = Field(min_length=1, max_length=2000, description='How to test the condition with expectations independent of this implementation; note unavailable checks.')


class TaskPlanInput(ToolInput):
    steps: list[str] = Field(min_length=2, max_length=20)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    scope_hints: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            'Optional repository-relative paths or glob patterns only. '
            'Do not put prose, rationale, or implementation preferences here.'
        ),
    )
    replace: bool = False
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list, max_length=20)


class TaskPlanTool(Tool[TaskPlanInput]):
    name = 'task_plan'
    description = (
        'Create one persistent plan for complex work with multiple dependent '
        'steps, multiple files, or implementation plus verification. Do not '
        'use for questions, directory listings, one command, one file read, '
        'or a small focused edit. A current plan is replaced only when '
        'replace=true.'
    )
    input_model = TaskPlanInput

    def __init__(self, root: Path, manager: TaskManager) -> None:
        super().__init__(root)
        self.manager = manager

    async def execute(self, arguments: TaskPlanInput) -> ToolResult:
        existing = self.manager.active
        if existing is not None and existing.planned and not arguments.replace:
            return ToolResult.ok(
                'A task plan already exists; preserved the existing plan and '
                'current step. Continue it with task_update or perform the '
                'current step action.',
                content=self.manager.describe(),
                metadata={
                    'task_id': existing.id,
                    'step_count': len(existing.steps),
                    'current_step_id': existing.current_step_id,
                    'status': 'already_completed',
                },
            )
        try:
            task = self.manager.plan(
                arguments.steps,
                constraints=arguments.constraints,
                scope_hints=arguments.scope_hints,
                replace_existing=arguments.replace,
                acceptance_criteria=[item.model_dump() for item in arguments.acceptance_criteria],
            )
        except ValueError as error:
            details = {}
            if 'already has a plan' in str(error):
                details = {
                    'recommended_tool': 'task_update',
                    'recovery': (
                        'Use task_get only if the injected task context is '
                        'insufficient, then advance the existing step with '
                        'task_update. Set replace=true only when intentionally '
                        'replacing the whole plan.'
                    ),
                }
            raise ToolExecutionError(
                'task_plan_rejected',
                str(error),
                details=details,
            ) from error
        step_refs = tuple(
            {'id': step.id, 'title': step.title}
            for step in task.steps
        )
        rendered_refs = '; '.join(
            f'{step.id}: {step.title}'
            for step in task.steps
        )
        return ToolResult.ok(
            f'Created a {len(task.steps)}-step task plan: {rendered_refs}',
            content=self.manager.describe(),
            metadata={
                'task_id': task.id,
                'step_count': len(task.steps),
                'steps': step_refs,
                'acceptance_criteria': task.acceptance_criteria,
            },
        )


def invalid_completion_evidence(evidence: list[str]) -> bool:
    '''Compatibility shim: plan wording is not an execution or completion gate.'''
    return False


class TaskUpdateInput(ToolInput):
    step_id: str = Field(
        min_length=1,
        description=(
            'Exact generated step ID such as step-1. A numeric alias such as '
            '1 is also accepted and normalized to step-1.'
        ),
    )
    status: Literal['pending', 'in_progress', 'completed', 'blocked']
    evidence: list[str] = Field(default_factory=list, max_length=20)


class TaskUpdateTool(Tool[TaskUpdateInput]):
    name = 'task_update'
    description = (
        'Advance the current step of an existing complex task plan. Steps are '
        'strictly ordered: do not select a later step, and do not call '
        'in_progress for a step already in progress. task_update is not a '
        'commentary or preparation tool; perform concrete repository work '
        'instead. Use completed only for the current step and include concise '
        'evidence of execution. This tool cannot complete the whole task; '
        'ForgeCode completion checks own that state.'
    )
    input_model = TaskUpdateInput

    def __init__(self, root: Path, manager: TaskManager) -> None:
        super().__init__(root)
        self.manager = manager

    async def execute(self, arguments: TaskUpdateInput) -> ToolResult:
        active = self.manager.active
        canonical_step_id = normalize_step_id(arguments.step_id)
        target = (
            next(
                (
                    step
                    for step in active.steps
                    if step.id == canonical_step_id
                ),
                None,
            )
            if active is not None and active.planned
            else None
        )
        idempotent_repeat = bool(
            target is not None
            and (
                target.status == arguments.status
                or (
                    target.status == 'completed'
                    and arguments.status in {'in_progress', 'completed'}
                )
            )
        )
        if idempotent_repeat and active is not None and target is not None:
            stale_step_redirect = bool(
                target.status == 'completed'
                and target.id != active.current_step_id
            )
            current_step_action_required = bool(
                target.status == 'in_progress'
                and arguments.status == 'in_progress'
            )
            summary = (
                f'{target.id} is already completed. The current step is '
                f'{active.current_step_id or "none"}; do not update '
                f'{target.id} again. Execute the current step action.'
                if stale_step_redirect
                else (
                    f'{target.id} is already in progress. Do not call '
                    'task_update again for preparation or commentary; perform '
                    'the concrete current-step action.'
                    if current_step_action_required
                    else (
                        f'Preserved {target.id} as {target.status}; the requested '
                        f'{arguments.status} transition was already satisfied.'
                    )
                )
            )
            return ToolResult.ok(
                summary,
                content=self.manager.describe(),
                metadata={
                    'task_id': active.id,
                    'step_id': target.id,
                    'requested_status': arguments.status,
                    'status': 'already_completed',
                    'current_step_id': active.current_step_id,
                    'recommended_action': (
                        'execute_current_step'
                        if stale_step_redirect
                        else 'continue_current_step'
                    ),
                    'stale_step_redirect': stale_step_redirect,
                    'current_step_action_required': (
                        current_step_action_required
                    ),
                },
            )
        try:
            task = self.manager.update_step(
                arguments.step_id,
                arguments.status,
                evidence=arguments.evidence,
            )
        except ValueError as error:
            raise ToolExecutionError('task_update_rejected', str(error)) from error
        current = task.current_step.title if task.current_step else 'none'
        return ToolResult.ok(
            f'Updated {arguments.step_id} to {arguments.status}.',
            content=f'Current step: {current}',
            metadata={
                'task_id': task.id,
                'step_id': arguments.step_id,
                'status': arguments.status,
            },
        )


class TaskReviseInput(ToolInput):
    requirement_id: str = Field(min_length=1)
    replacement: AcceptanceCriterion | None = None
    reason: str = Field(min_length=1, max_length=2000)


class TaskReviseTool(Tool[TaskReviseInput]):
    name = 'task_revise_requirement'
    description = ('Correct or retract a model-proposed interpretation using its exact requirement ID. '
                   'Quote the user instruction for a replacement; omit replacement to retract a hypothesis. '
                   'The original user instructions and caller contracts remain authoritative. Changes are audited.')
    input_model = TaskReviseInput

    def __init__(self, root: Path, manager: TaskManager):
        super().__init__(root)
        self.manager = manager

    async def execute(self, arguments: TaskReviseInput) -> ToolResult:
        try:
            task = self.manager.revise_acceptance(arguments.requirement_id,
                arguments.replacement.model_dump() if arguments.replacement else None, arguments.reason)
        except ValueError as error:
            return ToolResult.fail('invalid_requirement_revision', str(error))
        return ToolResult.ok('Updated the model interpretation; prior evidence was not transferred.',
            metadata={'task_id': task.id, 'acceptance_criteria': task.acceptance_criteria,
                      'acceptance_history': task.acceptance_history})


def create_task_tools(
    root: Path,
    manager: TaskManager,
) -> tuple[Tool, ...]:
    return (
        TaskGetTool(root, manager),
        TaskPlanTool(root, manager),
        TaskUpdateTool(root, manager),
        TaskReviseTool(root, manager),
    )
