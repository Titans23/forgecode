'''Typed configuration and runtime values for lifecycle hooks.'''

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


HookEventName = Literal[
    'SessionStart',
    'BeforeModelCall',
    'PreToolUse',
    'PostToolUse',
    'BeforeFileEdit',
    'AfterFileEdit',
    'BeforeCompact',
    'AfterVerification',
    'SessionEnd',
]


class HookMatcher(BaseModel):
    '''Optional glob filters for one hook.'''

    model_config = ConfigDict(extra='forbid')

    tools: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()

    @field_validator('tools', 'paths')
    @classmethod
    def reject_empty_patterns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError('matcher patterns must not be empty')
        return value


class HookSpec(BaseModel):
    '''One configured external command hook.'''

    model_config = ConfigDict(extra='forbid')

    id: str = Field(min_length=1, max_length=100)
    command: tuple[str, ...] = Field(min_length=1)
    matcher: HookMatcher = Field(default_factory=HookMatcher)
    timeout_seconds: float = Field(default=10.0, ge=0.1, le=300.0)
    enabled: bool = True

    @field_validator('command')
    @classmethod
    def reject_empty_command(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not part for part in value):
            raise ValueError('command arguments must not be empty')
        return value


class HookSettings(BaseModel):
    '''Validated lifecycle hook configuration.'''

    model_config = ConfigDict(extra='forbid')

    hooks: dict[HookEventName, tuple[HookSpec, ...]] = Field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class HookEvent:
    '''One lifecycle event delivered to matching hooks.'''

    name: HookEventName
    session_id: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    arguments: dict[str, Any] | None = None
    paths: tuple[str, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HookExecution:
    '''Sanitized audit record for one hook command.'''

    hook_id: str
    event: HookEventName
    decision: Literal['allow', 'deny', 'error']
    duration_seconds: float
    exit_code: int | None
    timed_out: bool
    arguments_modified: bool = False
    context_injected: bool = False
    reason: str = ''
    output_preview: str = ''

    def as_dict(self) -> dict[str, Any]:
        return {
            'hook_id': self.hook_id,
            'event': self.event,
            'decision': self.decision,
            'duration_seconds': self.duration_seconds,
            'exit_code': self.exit_code,
            'timed_out': self.timed_out,
            'arguments_modified': self.arguments_modified,
            'context_injected': self.context_injected,
            'reason': self.reason,
            'output_preview': self.output_preview,
        }


@dataclass(frozen=True, slots=True)
class HookOutcome:
    '''Combined result of all hooks matched for one event.'''

    allowed: bool = True
    arguments: dict[str, Any] | None = None
    additional_context: tuple[str, ...] = ()
    reason: str = ''
    executions: tuple[HookExecution, ...] = ()
