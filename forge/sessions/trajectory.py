'''Append-only JSONL trajectory recording for M1 agent runs.'''

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import re
import time
from typing import Any
from uuid import uuid4

from forge.runtime.state import (
    CompletionBlocked,
    ConversationEvent,
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    ModelRetryScheduled,
    ModelResponseCompleted,
    ModelTextDelta,
    ModelToolCallArgumentsDelta,
    ModelToolCallCompleted,
    ModelUsageUpdate,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
    VerificationCompleted,
    WorkspaceChanged,
)


_SENSITIVE_KEYS = {
    'api_key',
    'authorization',
    'password',
    'secret',
    'access_token',
    'auth_token',
    'refresh_token',
}
_SENSITIVE_ASSIGNMENT = re.compile(
    r'(?i)\b(api[_-]?key|authorization|password|secret|token)'
    r'(\s*[:=]\s*)([^\s,;]+)'
)


class TrajectoryRecorder:
    '''Write compact, redacted runtime events without owning the Agent Loop.'''

    def __init__(self, path: Path) -> None:
        self.path = path
        self._model_started_at: dict[int, float] = {}
        self._tool_started_at: dict[str, float] = {}
        self._latest_usage: dict[str, Any] | None = None

    @classmethod
    def create(cls, root: Path) -> TrajectoryRecorder:
        '''Create one trajectory file for the current interactive session.'''
        directory = root / '.forge' / 'trajectories'
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')
        path = directory / f'{timestamp}-{uuid4().hex[:8]}.jsonl'
        recorder = cls(path)
        recorder._write('session_started', {'cwd': str(root.resolve())})
        return recorder

    def record_user_message(self, content: str) -> None:
        self._write('user_message', {'content': redact_text(content)})

    def record_error(self, error: Exception) -> None:
        self._write(
            'turn_error',
            {
                'error_type': type(error).__name__,
                'message': redact_text(str(error)),
            },
        )

    def record_event(self, event: ConversationEvent) -> None:
        '''Record stable lifecycle events and skip high-volume stream deltas.'''
        if isinstance(event, (ModelTextDelta, ModelToolCallArgumentsDelta)):
            return
        if isinstance(event, ModelUsageUpdate):
            self._latest_usage = asdict(event.usage)
            if event.request_usage is not None:
                self._latest_usage['last_request'] = asdict(
                    event.request_usage
                )
            self._latest_usage['model_calls'] = event.model_calls
            return
        if isinstance(event, ModelCallStarted):
            self._model_started_at[event.iteration] = time.perf_counter()
            self._latest_usage = None
            self._write('model_call_started', asdict(event))
            return
        if isinstance(event, ModelRetryScheduled):
            self._write('model_retry_scheduled', asdict(event))
            return
        if isinstance(event, ModelResponseCompleted):
            self._write('model_response_completed', asdict(event))
            return
        if isinstance(event, ModelCallCompleted):
            started_at = self._model_started_at.pop(event.iteration, None)
            payload: dict[str, Any] = asdict(event)
            payload['duration_seconds'] = elapsed_seconds(started_at)
            if self._latest_usage is not None:
                payload['turn_usage'] = self._latest_usage
            self._write('model_call_completed', payload)
            return
        if isinstance(event, ModelCallFailed):
            started_at = self._model_started_at.pop(event.iteration, None)
            payload = asdict(event)
            payload['duration_seconds'] = elapsed_seconds(started_at)
            self._write('model_call_failed', payload)
            return
        if isinstance(event, ModelToolCallCompleted):
            self._write(
                'tool_requested',
                {
                    'tool_call_id': event.tool_call.id,
                    'index': event.tool_call.index,
                    'name': event.tool_call.name,
                    'arguments': sanitize(event.tool_call.arguments),
                },
            )
            return
        if isinstance(event, ToolExecutionStarted):
            call = event.tool_call
            self._tool_started_at[call.id] = time.perf_counter()
            self._write(
                'tool_execution_started',
                {
                    'tool_call_id': call.id,
                    'name': call.name,
                    'arguments': sanitize(call.arguments),
                },
            )
            return
        if isinstance(event, ToolExecutionCompleted):
            call = event.tool_call
            result = event.result
            started_at = self._tool_started_at.pop(call.id, None)
            self._write(
                'tool_execution_completed',
                {
                    'tool_call_id': call.id,
                    'name': call.name,
                    'success': result.success,
                    'summary': redact_text(result.summary),
                    'error': (
                        sanitize(asdict(result.error))
                        if result.error is not None
                        else None
                    ),
                    'metadata': sanitize(result.metadata),
                    'duration_seconds': elapsed_seconds(started_at),
                },
            )
            return
        if isinstance(event, WorkspaceChanged):
            self._write('workspace_changed', asdict(event))
            return
        if isinstance(event, VerificationCompleted):
            self._write('verification_completed', asdict(event.evidence))
            return
        if isinstance(event, CompletionBlocked):
            self._write('completion_blocked', asdict(event))
            return
        if isinstance(event, TurnCompleted):
            result = event.result
            self._write(
                'turn_completed',
                {
                    'text': redact_text(result.text),
                    'usage': asdict(result.usage),
                    'last_request_usage': (
                        asdict(result.last_request_usage)
                        if result.last_request_usage is not None
                        else None
                    ),
                    'model_calls': result.model_calls,
                    'tool_calls': len(result.tool_calls),
                    'status': result.status,
                    'changed_paths': result.changed_paths,
                    'verification': (
                        asdict(result.verification)
                        if result.verification is not None
                        else None
                    ),
                    'completion_reasons': result.completion_reasons,
                },
            )

    def _write(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            'type': event_type,
            'timestamp': datetime.now().astimezone().isoformat(),
            **sanitize(payload),
        }
        with self.path.open('a', encoding='utf-8') as file:
            file.write(json.dumps(record, ensure_ascii=False, default=str))
            file.write('\n')


def elapsed_seconds(started_at: float | None) -> float | None:
    if started_at is None:
        return None
    return round(max(0.0, time.perf_counter() - started_at), 6)


def sanitize(value: Any) -> Any:
    '''Redact sensitive fields and keep trajectory values JSON-compatible.'''
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).casefold()
            if (
                normalized in _SENSITIVE_KEYS
                or normalized.endswith('_api_key')
                or normalized.endswith('_password')
                or normalized.endswith('_secret')
            ):
                sanitized[str(key)] = '[REDACTED]'
            else:
                sanitized[str(key)] = sanitize(item)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(value: str) -> str:
    return _SENSITIVE_ASSIGNMENT.sub(r'\1\2[REDACTED]', value)
