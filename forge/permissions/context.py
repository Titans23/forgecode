"""Trusted final tool context, available only while the permission callback runs."""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolApprovalContext:
    execution_id: str
    call: Any
    tracker: Any


tool_approval_context: ContextVar[ToolApprovalContext | None] = ContextVar('tool_approval_context', default=None)
