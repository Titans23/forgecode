'''Single source of truth for one model/tool turn.

The legacy loop kept independent counters and recovery flags in several
branches.  These value objects make the facts that matter to execution
explicit and serializable without changing the public Conversation API.
'''

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Literal

from forge.runtime.state import TokenUsage, ToolCall, VerificationEvidence
from forge.tools.base import ToolResult


ExecutionStatus = Literal[
    'executed',
    'cached',
    'rejected',
    'cancelled',
    'indeterminate',
]


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    '''Fact record for one requested tool operation.'''

    tool_call_id: str
    tool_name: str
    status: ExecutionStatus
    success: bool
    duration_seconds: float = 0.0
    workspace_revision: int = 0
    environment_epoch: int = 0
    error_code: str | None = None
    summary: str = ''

    def as_dict(self) -> dict[str, Any]:
        return {
            'tool_call_id': self.tool_call_id,
            'tool_name': self.tool_name,
            'status': self.status,
            'success': self.success,
            'duration_seconds': self.duration_seconds,
            'workspace_revision': self.workspace_revision,
            'environment_epoch': self.environment_epoch,
            'error_code': self.error_code,
            'summary': self.summary,
        }


@dataclass(slots=True)
class EvidenceLedger:
    '''Append-only verification history with current-state queries.'''

    verification: list[VerificationEvidence] = field(default_factory=list)

    def add(self, evidence: VerificationEvidence) -> None:
        self.verification.append(evidence)

    def current(
        self,
        *,
        workspace_revision: int,
        environment_epoch: int,
    ) -> tuple[VerificationEvidence, ...]:
        return tuple(
            item
            for item in self.verification
            if item.workspace_revision == workspace_revision
            and item.environment_epoch == environment_epoch
        )

    def latest_current(
        self,
        *,
        workspace_revision: int,
        environment_epoch: int,
    ) -> VerificationEvidence | None:
        for item in reversed(self.verification):
            if (
                item.workspace_revision == workspace_revision
                and item.environment_epoch == environment_epoch
            ):
                return item
        return None


@dataclass(slots=True)
class TurnState:
    '''Mutable budget and execution facts for exactly one user turn.'''

    goal: str = ''
    constraints: tuple[str, ...] = ()
    max_model_calls: int | None = None
    max_tool_calls: int | None = None
    max_input_tokens: int | None = None
    max_seconds: float | None = None
    model_calls: int = 0
    tool_requests: int = 0
    usage: TokenUsage = field(default_factory=lambda: TokenUsage(0, 0))
    last_request_usage: TokenUsage | None = None
    timings: dict[str, float] = field(default_factory=dict)
    request_counts: dict[str, int] = field(default_factory=dict)
    protocol_errors: int = 0
    failure_counts: dict[str, int] = field(default_factory=dict)
    feedback_count: int = 0
    execution_records: list[ExecutionRecord] = field(default_factory=list)
    evidence: EvidenceLedger = field(default_factory=EvidenceLedger)
    workspace_revision: int = 0
    environment_epoch: int = 0
    stop_reason: str = ''
    started_at: float = field(default_factory=monotonic)

    def can_request_model(self) -> bool:
        return self.budget_reason() is None

    def budget_reason(self, *, include_model: bool = True) -> str | None:
        if self.max_seconds is not None and monotonic() - self.started_at >= self.max_seconds:
            return 'time_budget_exhausted'
        if self.max_input_tokens is not None and self.usage.total_input_tokens >= self.max_input_tokens:
            return 'token_budget_exhausted'
        if include_model and self.max_model_calls is not None and self.model_calls >= self.max_model_calls:
            return 'model_budget_exhausted'
        return None

    def can_request_tool_batch(self, count: int) -> bool:
        return (
            count >= 0
            and (
                self.max_tool_calls is None
                or self.tool_requests + count <= self.max_tool_calls
            )
        )

    def record_model_request(self, usage: TokenUsage | None = None, *, stage: str = 'model') -> None:
        self.model_calls += 1
        self.request_counts[stage] = self.request_counts.get(stage, 0) + 1
        if usage is not None:
            self.record_usage(usage)

    def record_usage(self, usage: TokenUsage) -> None:
        self.usage = add_usage(self.usage, usage)
        self.last_request_usage = usage

    def add_time(self, stage: str, seconds: float) -> None:
        self.timings[stage] = self.timings.get(stage, 0.0) + max(0.0, seconds)

    def record_tool_request(self) -> None:
        self.tool_requests += 1

    def record_execution(self, record: ExecutionRecord) -> None:
        self.execution_records.append(record)

    def observe_workspace(
        self,
        revision: int,
        environment_epoch: int | None = None,
    ) -> None:
        self.workspace_revision = revision
        if environment_epoch is not None:
            self.environment_epoch = environment_epoch

    def statistics(self) -> dict[str, int | float]:
        counts = {
            'executed': 0,
            'cached': 0,
            'rejected': 0,
            'cancelled': 0,
            'indeterminate': 0,
        }
        for record in self.execution_records:
            counts[record.status] += 1
        counts.update(
            {
                'model_requests': self.model_calls,
                'tool_requests': self.tool_requests,
                'recovery_feedback': self.feedback_count,
            }
        )
        counts.update({f'{key}_stage_requests': value for key, value in self.request_counts.items()})
        counts.update({f'{key}_seconds': round(value, 6) for key, value in self.timings.items()})
        counts['elapsed_seconds'] = round(monotonic() - self.started_at, 6)
        return counts


class BudgetExhausted(RuntimeError):
    '''A terminal budget condition, never an external user blocker.'''

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def add_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    return TokenUsage(
        left.input_tokens + right.input_tokens,
        left.output_tokens + right.output_tokens,
        left.cache_creation_input_tokens + right.cache_creation_input_tokens,
        left.cache_read_input_tokens + right.cache_read_input_tokens,
    )


def execution_record_from_result(
    call: ToolCall,
    result: ToolResult,
    *,
    status: ExecutionStatus,
    duration_seconds: float,
    workspace_revision: int = 0,
    environment_epoch: int = 0,
) -> ExecutionRecord:
    return ExecutionRecord(
        tool_call_id=call.id,
        tool_name=call.name,
        status=status,
        success=result.success,
        duration_seconds=duration_seconds,
        workspace_revision=workspace_revision,
        environment_epoch=environment_epoch,
        error_code=result.error.code if result.error is not None else None,
        summary=result.summary,
    )


def execution_status_for_result(result: ToolResult) -> ExecutionStatus:
    '''Infer a durable status when an event carries only a ToolResult.'''
    explicit = result.metadata.get('execution_status')
    if explicit in {
        'executed',
        'cached',
        'rejected',
        'cancelled',
        'indeterminate',
    }:
        return explicit
    if result.metadata.get('cache_hit'):
        return 'cached'
    if result.error is not None and result.error.code in {
        'permission_denied',
        'invalid_arguments',
        'unknown_tool',
        'hook_denied',
        'outside_task_scope',
        'tool_not_available_in_phase',
        'placeholder_write_denied',
    }:
        return 'rejected'
    if result.error is not None and result.error.code.endswith('cancelled'):
        return 'cancelled'
    return 'executed'
