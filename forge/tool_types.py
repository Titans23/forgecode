"""Provider-neutral tool results; no runtime, permission or SDK imports."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolError:
    '''Structured failure returned without crashing the Agent Loop.'''

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResult:
    '''Provider-independent result of one tool execution.'''

    success: bool
    summary: str
    content: str = ''
    error: ToolError | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.success and self.error is not None:
            raise ValueError('A successful tool result cannot contain an error.')
        if not self.success and self.error is None:
            raise ValueError('A failed tool result must contain an error.')

    @classmethod
    def ok(
        cls,
        summary: str,
        *,
        content: str = '',
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls(
            success=True,
            summary=summary,
            content=content,
            metadata=metadata or {},
        )

    @classmethod
    def fail(
        cls,
        code: str,
        message: str,
        *,
        summary: str | None = None,
        content: str = '',
        details: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls(
            success=False,
            summary=summary or message,
            content=content,
            error=ToolError(
                code=code,
                message=message,
                details=details or {},
            ),
            metadata=metadata or {},
        )


class ToolExecutionError(RuntimeError):
    '''Expected operational failure raised inside a concrete tool.'''

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


