'''Model-declared task completion protocol.'''

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from forge.tools.base import Tool, ToolInput, ToolResult


TaskKind = Literal['answer', 'inspection', 'change']
FinishStatus = Literal['completed', 'blocked']


class FinishTaskInput(ToolInput):
    task_kind: TaskKind
    status: FinishStatus
    summary: str = Field(min_length=1, max_length=20_000)
    blocked_reasons: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def validate_status(self) -> FinishTaskInput:
        if self.status == 'blocked' and not self.blocked_reasons:
            raise ValueError(
                'blocked_reasons must explain why a blocked task cannot '
                'continue'
            )
        if self.status == 'completed' and self.blocked_reasons:
            raise ValueError(
                'blocked_reasons must be empty when status is completed'
            )
        return self


class FinishTaskTool(Tool[FinishTaskInput]):
    name = 'finish_task'
    description = (
        'Declare the model-chosen outcome and finish the current user turn. '
        'Call this tool alone, only after all necessary repository actions. '
        'Choose task_kind=answer for a direct response, inspection after '
        'collecting repository evidence, or change after creating a real Diff '
        'and verifying the latest workspace revision. Use status=blocked with '
        'specific blocked_reasons when the goal cannot be completed. The '
        'runtime validates the declaration against objective evidence.'
    )
    input_model = FinishTaskInput

    def __init__(self, root: Path) -> None:
        super().__init__(root)

    async def execute(self, arguments: FinishTaskInput) -> ToolResult:
        return ToolResult.ok(
            f'Declared {arguments.task_kind} task {arguments.status}.',
            metadata={
                'finish_task': True,
                'task_kind': arguments.task_kind,
                'status': arguments.status,
                'summary': arguments.summary,
                'blocked_reasons': arguments.blocked_reasons,
            },
        )
