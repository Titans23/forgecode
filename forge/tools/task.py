'''Optional planning tools backed by the current TaskManager.'''

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from forge.tasks.manager import TaskManager
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
    scope_hints: list[str] = Field(default_factory=list, max_length=20)
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
        try:
            task = self.manager.plan(
                arguments.steps,
                constraints=arguments.constraints,
                scope_hints=arguments.scope_hints,
                replace_existing=arguments.replace,
            )
        except ValueError as error:
            raise ToolExecutionError('task_plan_rejected', str(error)) from error
        return ToolResult.ok(
            f'Created a {len(task.steps)}-step task plan.',
            content=self.manager.describe(),
            metadata={'task_id': task.id, 'step_count': len(task.steps)},
        )


class TaskUpdateInput(ToolInput):
    step_id: str = Field(min_length=1)
    status: Literal['pending', 'in_progress', 'completed', 'blocked']
    evidence: list[str] = Field(default_factory=list, max_length=20)


class TaskUpdateTool(Tool[TaskUpdateInput]):
    name = 'task_update'
    description = (
        'Update one step of an existing complex task plan. Use only when a '
        'step actually starts, completes, or becomes blocked. This tool cannot '
        'complete the whole task; ForgeCode completion checks own that state.'
    )
    input_model = TaskUpdateInput

    def __init__(self, root: Path, manager: TaskManager) -> None:
        super().__init__(root)
        self.manager = manager

    async def execute(self, arguments: TaskUpdateInput) -> ToolResult:
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
