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
_BEARER_TOKEN = re.compile(r'(?i)\b(Bearer\s+)[^\s,;]+')


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
                    'completion_report': asdict(result.completion_report) if result.completion_report else None,
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
    return _SENSITIVE_ASSIGNMENT.sub(r'\1\2[REDACTED]', _BEARER_TOKEN.sub(r'\1[REDACTED]', value))


def journal_events(path: Path) -> list[dict]:
    """Validate the selected Journal chain and resolve verified external payloads."""
    from forge.sessions.store import SessionStore
    return [{**record, 'payload': SessionStore._payload(record, path)}
            for record in SessionStore._read_records(path)]


def journal_completeness(events, *, truncated_tail=False) -> dict:
    turns, ended, requests, finishes, inputs, tools, tool_ends = set(), set(), {}, {}, {}, set(), set()
    chunks, results = {}, set()
    for event in events:
        kind, payload = event['type'], event['payload']
        turn = event.get('turn_id') or payload.get('turn_id')
        if kind == 'turn_started': turns.add(turn)
        elif kind in {'turn_completed', 'turn_cancelled', 'turn_error'}: ended.add(turn)
        elif kind == 'model_input_snapshot': inputs[payload['sha256']] = payload
        elif kind == 'model_request_started': requests[payload['request_id']] = payload
        elif kind == 'model_request_finished': finishes[payload['request_id']] = payload
        elif kind == 'model_request_chunk':
            key = payload['request_id']
            chunks[key] = chunks.get(key, 0) + len(payload.get('text', ''))
        elif kind == 'tool_started': tools.add(payload['tool_call_id'])
        elif kind == 'tool_completed': tool_ends.add(payload['tool_call_id'])
        elif kind == 'tool_result_message':
            for block in payload.get('message', {}).get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    results.add(block['tool_use_id'])
    closed = bool(turns) and turns <= ended and requests.keys() <= finishes.keys() and tools <= tool_ends and not truncated_tail
    problems = []
    if not closed: problems.append('open_lifecycle')
    if not requests: problems.append('no_model_requests')
    if tools - results: problems.append('missing_tool_results')
    from forge.runtime.request_snapshot import RequestSnapshot
    for key, request in requests.items():
        snapshot, finish = inputs.get(request.get('input_sha256')), finishes.get(key, {})
        if snapshot is None:
            problems.append('missing_model_input')
        else:
            RequestSnapshot.restore(snapshot)
        if finish.get('response_complete') is not True or finish.get('outcome') != 'completed':
            problems.append('incomplete_response')
        if finish.get('text_characters', 0) != chunks.get(key, 0):
            problems.append('missing_response_text')
    return {'record_closed': closed, 'responses_complete': not problems,
            'training_eligible': False, 'training_reason': 'Provenance and independent acceptance must be checked by training export.',
            'problems': sorted(set(problems)), 'model_requests': len(requests)}


def journal_to_atif(path: Path, *, secrets=()) -> dict:
    """Offline ATIF v1.7 projection of Journal facts; no inferred private reasoning."""
    from hashlib import sha256
    from harbor.models.trajectories.trajectory import Trajectory
    from forge.observability.export_queue import redact
    events = journal_events(path)
    if not events:
        raise ValueError('Journal contains no events.')
    complete = journal_completeness(events, truncated_tail=not path.read_bytes().endswith(b'\n'))
    inputs, starts, texts, partial_tools, steps, tool_steps = {}, {}, {}, {}, [], {}
    tool_results = {}
    for event in events:
        if event['type'] == 'tool_result_message':
            message = event['payload'].get('message', event['payload'])
            for block in message.get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    tool_results[block['tool_use_id']] = block
    def step(source, message, event, **fields):
        value = {'step_id': len(steps) + 1, 'source': source, 'message': message,
                 'timestamp': event.get('timestamp'), 'extra': {'journal_event_uuid': event['uuid'], **fields.pop('extra', {})}, **fields}
        steps.append(value)
        return value
    for event in events:
        kind, payload = event['type'], event['payload']
        if kind == 'model_input_snapshot': inputs[payload['sha256']] = payload
        elif kind == 'turn_started': step('user', payload['prompt'], event)
        elif kind == 'model_request_started': starts[payload['request_id']] = event
        elif kind == 'model_request_chunk': texts.setdefault(payload['request_id'], []).append(payload.get('text', ''))
        elif kind == 'model_tool_chunk':
            key = payload['request_id']
            block = partial_tools.setdefault(key, {}).setdefault(str(payload['index']), {'partial_json': ''})
            block.update({key: value for key, value in payload.items() if key not in {'partial_json', 'turn_id'}})
            block['partial_json'] += payload.get('partial_json', '')
        elif kind == 'model_request_finished':
            key = payload['request_id']
            snapshot = inputs.get(payload.get('input_sha256'), {})
            blocks = payload.get('tool_blocks') or {}
            calls = [{'tool_call_id': block['id'], 'function_name': block['name'], 'arguments': block['arguments']}
                     for block in blocks.values() if block.get('complete') and 'arguments' in block]
            value = step('agent', ''.join(texts.pop(key, [])), event,
                model_name=payload.get('returned_model') or payload.get('requested_model'), llm_call_count=1,
                tool_calls=calls or None, extra={'request': payload, 'actual_model_input': snapshot.get('request'),
                    'partial_tools': partial_tools.get(key, {}), 'incomplete': payload.get('response_complete') is not True})
            for call in calls: tool_steps[call['tool_call_id']] = value
            starts.pop(key, None)
        elif kind == 'tool_completed':
            value = tool_steps.get(payload['tool_call_id'])
            if value:
                block = tool_results.get(payload['tool_call_id'], {})
                value.setdefault('observation', {'results': []})['results'].append({
                    'source_call_id': payload['tool_call_id'],
                    'content': json.dumps(block.get('content'), ensure_ascii=False), 'extra': payload})
        elif kind in {'context_compacted', 'session_resumed', 'session_forked', 'conversation_rewound'}:
            step('system', kind, event, extra={'relationship': payload})
    for key, event in starts.items():
        step('agent', ''.join(texts.get(key, [])), event, llm_call_count=1,
             extra={'request_id': key, 'incomplete': True, 'partial_tools': partial_tools.get(key, {})})
    if not steps:
        step('system', 'No conversation messages are available.', events[0])
    value = {'schema_version': 'ATIF-v1.7', 'session_id': events[0]['session_id'],
             'agent': {'name': 'ForgeCode', 'version': '0.1.2'}, 'steps': steps,
             'extra': {'journal_sha256': sha256(path.read_bytes()).hexdigest(), 'completeness': complete,
                       'usage': 'diagnostic', 'redacted': True}}
    # Sharing projection only: raw Journal stays private and unmodified.
    shared = redact(sanitize(value), secrets)
    return Trajectory.model_validate(shared).to_json_dict()
