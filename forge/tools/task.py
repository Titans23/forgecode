'''Optional planning tools backed by the current TaskManager.'''

from __future__ import annotations

from pathlib import Path
import re
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
            content=self.manager.describe(),
        )


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
            },
        )


_UNRESOLVED_COMPLETION_EVIDENCE = re.compile(
    r'(?:失败|未完成|未实现|尚未|接下来|下一步|准备(?:去|中|做|创建|实现|修改)?|'
    r'将(?:要|会|以)?|待(?:办|完成|实现|处理)|'
    r'\bfailed\b|\bfailure\b|\bcannot\b|\bcould not\b|'
    r'\bnot (?:completed|implemented|created|fixed)\b|'
    r'\bnext(?: step)?\b|\bwill\b|\bplanning to\b|'
    r'\bprepar(?:e|ed|ing) to\b)',
    re.IGNORECASE,
)
_RESOLVED_COMPLETION_EVIDENCE = re.compile(
    r'(?:已(?:完成|实现|创建|修改|修复|解决|通过)|'
    r'成功(?:完成|创建|修改|修复|通过)?|'
    r'(?:失败|错误|问题).{0,24}(?:已)?(?:修复|解决|通过)|'
    r'\b(?:completed|implemented|created|updated|fixed|resolved|passed)\b)',
    re.IGNORECASE,
)


def invalid_completion_evidence(evidence: list[str]) -> bool:
    '''Reject evidence that describes only failure or intended future work.'''
    rendered = '；'.join(item.strip() for item in evidence if item.strip())
    return bool(
        rendered
        and _UNRESOLVED_COMPLETION_EVIDENCE.search(rendered)
        and not _RESOLVED_COMPLETION_EVIDENCE.search(rendered)
    )


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
        if (
            arguments.status == 'completed'
            and invalid_completion_evidence(arguments.evidence)
        ):
            raise ToolExecutionError(
                'task_completion_evidence_invalid',
                'A step cannot be completed with evidence that only reports '
                'failure, preparation, or future work. Perform the current '
                'step action, then provide evidence of the result.',
                details={
                    'step_id': canonical_step_id,
                    'current_step_id': (
                        active.current_step_id if active is not None else None
                    ),
                    'recommended_action': 'execute_current_step',
                },
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


def create_task_tools(
    root: Path,
    manager: TaskManager,
) -> tuple[TaskGetTool, TaskPlanTool, TaskUpdateTool]:
    return (
        TaskGetTool(root, manager),
        TaskPlanTool(root, manager),
        TaskUpdateTool(root, manager),
    )
