'''Model-declared task completion protocol.'''

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from forge.tools.base import Tool, ToolInput, ToolResult
from forge.tools.task import AcceptanceCriterion


TaskKind = Literal['answer', 'inspection', 'change']
FinishStatus = Literal['completed', 'partial', 'blocked', 'failed']


class FinishTaskInput(ToolInput):
    task_kind: TaskKind
    status: FinishStatus
    summary: str = Field(min_length=1, max_length=20_000)
    blocked_reasons: list[str] = Field(default_factory=list, max_length=20)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list, max_length=20,
        description='For an explicit reconciliation contract, register original requirements without creating a plan. '
        'Used only if no criteria exist; later completion is read-only. Use task_plan to add distinct clauses; omit for failed.')

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
        if self.status == 'failed' and not self.blocked_reasons:
            self.blocked_reasons = [self.summary]
        return self


class FinishTaskTool(Tool[FinishTaskInput]):
    name = 'finish_task'
    description = (
        'Submit a result for completion. Call alone after necessary actions. '
        'Use partial for incomplete work, blocked for an observed external dependency, '
        'or failed for an unsuccessful attempt. Explain checks and limitations. '
        'Caller acceptance is assessed separately; unmet requirements downgrade '
        'completed to partial, or return bounded repair feedback when enabled '
        'by the caller. Submission does not certify correctness.'
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
                'acceptance_criteria': [item.model_dump() for item in arguments.acceptance_criteria],
            },
        )


class ReviewDeliveryTool(Tool[ToolInput]):
    name = 'review_delivery'
    description = (
        'Inspect current acceptance gaps before submitting, without ending the turn. '
        'Returns recorded evidence and unmet requirements; does not run new tests. '
        'Use the findings to repair or explicitly submit partial. A clear report '
        'does not prove correctness: use independent inputs, exact source comparisons, '
        'and clean-directory delivery checks where relevant.'
    )
    input_model = ToolInput

    async def execute(self, arguments: ToolInput) -> ToolResult:
        # The turn runner supplies its current evidence after workspace refresh.
        return ToolResult.ok('Delivery review requested.', metadata={'review_delivery': True})
