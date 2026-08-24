'''Opt-in, single-file Qwen tool-call distillation extension for ForgeCode.

This module deliberately does not register itself, patch ForgeCode globals, or
read ForgeCode's normal Anthropic configuration.  Import it explicitly and
inject the returned client into ``Conversation(client=...)``.  Optional
packages (``openai``, ``transformers`` and ``ms-swift``) are loaded only by the
features that need them, so the base ForgeCode installation stays unchanged.

Typical collection setup::

    endpoint = QwenEndpoint.from_env()
    recorder = TraceRecorder(Path('data/teacher.jsonl'))
    session = create_recorded_conversation(
        endpoint=endpoint,
        recorder=recorder,
        episode=EpisodeSource(...),
        conversation_kwargs={'registry': registry},
        outcome_validator=run_hidden_tests,
    )
    async for event in session.stream(task_prompt):
        ...

The recorder sees the exact arguments passed to ``ModelClient.stream``.  In
ForgeCode that boundary is after context preparation, so dynamic system text,
phase-specific tools and the compacted message history are captured without a
change to the core agent loop.
'''

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit
from uuid import uuid4

from forge.runtime.agent_loop import Conversation
from forge.runtime.model_client import (
    ModelCallError,
    ModelClient,
    ModelOutputTruncatedError,
    ModelProtocolError,
)
from forge.runtime.state import (
    ConversationEvent,
    ModelResponseCompleted,
    ModelStreamEvent,
    ModelTextDelta,
    ModelToolCallArgumentsDelta,
    ModelToolCallCompleted,
    ModelToolCallStarted,
    ModelUsageUpdate,
    TokenUsage,
    ToolCall,
    ToolExecutionCompleted,
    TurnCompleted,
    VerificationCompleted,
    WorkspaceChanged,
)


TRACE_VERSION = 'forgecode-qwen-distillation/v1'
DATASET_VERSION = 'forgecode-qwen-sft/v1'
DEFAULT_MODEL = 'Qwen/Qwen3.5-4B'
DEFAULT_BASE_URL = 'http://127.0.0.1:8000/v1'
ALLOWED_LICENSES = frozenset(
    {'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', 'ISC', 'MIT', 'MPL-2.0'}
)
BEHAVIOR_CLASSES = frozenset(
    {'no_tool', 'clarification', 'safe_refusal', 'safety'}
)
HIDDEN_KEYS = frozenset(
    {'chain_of_thought', 'reasoning', 'reasoning_content', 'thinking', 'thinking_content'}
)
THINK_BLOCK = re.compile(
    r'<(?:think|thinking)>.*?</(?:think|thinking)>',
    flags=re.IGNORECASE | re.DOTALL,
)
SECRET_PATTERNS = (
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    re.compile(r'\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{30,}\b'),
    re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}={0,2}'),
)


class ExtensionConfigurationError(ValueError):
    '''The opt-in extension was configured with an invalid endpoint.'''


class TraceRejected(ValueError):
    '''A recorded episode is not eligible for training.'''

    def __init__(self, reasons: Iterable[str]) -> None:
        self.reasons = tuple(reasons)
        super().__init__('; '.join(self.reasons))


@dataclass(frozen=True, slots=True)
class QwenEndpoint:
    '''One Qwen/OpenAI-compatible endpoint, isolated from ForgeConfig.'''

    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str = 'EMPTY'
    max_tokens: int = 8_192
    context_window: int = 32_768
    timeout_seconds: float = 120.0
    role: Literal['main', 'router', 'summary', 'explore'] = 'main'

    def __post_init__(self) -> None:
        model = self.model.strip()
        base_url = self.base_url.strip().rstrip('/')
        api_key = self.api_key.strip() or 'EMPTY'
        parsed = urlsplit(base_url)
        if not model:
            raise ExtensionConfigurationError('Qwen model must not be empty')
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            raise ExtensionConfigurationError('Qwen base_url must be absolute HTTP(S)')
        if not 1 <= self.max_tokens < self.context_window:
            raise ExtensionConfigurationError(
                'max_tokens must be positive and smaller than context_window'
            )
        if self.timeout_seconds <= 0:
            raise ExtensionConfigurationError('timeout_seconds must be positive')
        object.__setattr__(self, 'model', model)
        object.__setattr__(self, 'base_url', base_url)
        object.__setattr__(self, 'api_key', api_key)

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        role: Literal['main', 'router', 'summary', 'explore'] = 'main',
    ) -> 'QwenEndpoint':
        source = os.environ if environ is None else environ
        prefix = f'QWEN_DISTILL_{role.upper()}_'

        def value(name: str, default: str) -> str:
            return source.get(prefix + name, source.get('QWEN_DISTILL_' + name, default))

        try:
            return cls(
                role=role,
                model=value('MODEL', DEFAULT_MODEL),
                base_url=value('BASE_URL', DEFAULT_BASE_URL),
                api_key=value('API_KEY', 'EMPTY'),
                max_tokens=int(value('MAX_TOKENS', '8192')),
                context_window=int(value('CONTEXT_WINDOW', '32768')),
                timeout_seconds=float(value('TIMEOUT_SECONDS', '120')),
            )
        except ValueError as error:
            raise ExtensionConfigurationError(
                f'Invalid QWEN_DISTILL_{role.upper()}_* value: {error}'
            ) from error


def qwen_role_endpoints(
    environ: Mapping[str, str] | None = None,
) -> dict[str, QwenEndpoint]:
    '''Load four isolated role endpoints without touching ``ForgeConfig``.'''
    return {
        role: QwenEndpoint.from_env(environ, role=role)
        for role in ('main', 'router', 'summary', 'explore')
    }


@dataclass(slots=True)
class _PendingCall:
    index: int
    call_id: str = ''
    name: str = ''
    argument_parts: list[str] = field(default_factory=list)
    emitted_parts: int = 0
    started: bool = False
    allowed: bool = False


class QwenModelClient:
    '''ModelClient-compatible Qwen adapter with no ForgeCode registration.'''

    provider = 'qwen-extension'

    def __init__(
        self,
        endpoint: QwenEndpoint,
        *,
        sdk_client: Any | None = None,
        max_retries: int = 3,
    ) -> None:
        if max_retries < 0:
            raise ValueError('max_retries must not be negative')
        self.endpoint = endpoint
        self.model = endpoint.model
        self.max_tokens = endpoint.max_tokens
        self.context_window = endpoint.context_window
        self.max_retries = max_retries
        if sdk_client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as error:
                raise RuntimeError(
                    'Qwen extension inference requires `pip install openai`.'
                ) from error
            sdk_client = AsyncOpenAI(
                api_key=endpoint.api_key,
                base_url=endpoint.base_url,
                timeout=endpoint.timeout_seconds,
                max_retries=0,
            )
        self._client = sdk_client

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        request: dict[str, Any] = {
            'model': self.model,
            'messages': to_openai_messages(messages, system=system),
            'max_tokens': self.max_tokens,
            'stream': True,
            'stream_options': {'include_usage': True},
            'extra_body': {
                'chat_template_kwargs': {'enable_thinking': False},
            },
        }
        if tools:
            request['tools'] = to_openai_tools(tools)
            request['tool_choice'] = 'auto'
            request['parallel_tool_calls'] = True
        allowed = {_tool_name(tool) for tool in tools or []}
        for attempt in range(self.max_retries + 1):
            emitted = False
            try:
                async for event in self._stream_once(request, allowed):
                    if isinstance(
                        event,
                        (
                            ModelTextDelta,
                            ModelToolCallStarted,
                            ModelToolCallArgumentsDelta,
                            ModelToolCallCompleted,
                        ),
                    ):
                        emitted = True
                    yield event
                return
            except (ModelProtocolError, ModelOutputTruncatedError):
                raise
            except Exception as error:
                reason, retryable = _classify_openai_error(error)
                if retryable and not emitted and attempt < self.max_retries:
                    await asyncio.sleep(min(0.5 * 2**attempt, 4.0))
                    continue
                if emitted and retryable:
                    reason = 'stream_interrupted'
                raise ModelCallError(
                    reason,
                    f'Qwen extension request failed: {reason}.',
                    retryable=retryable and not emitted,
                ) from error
        raise AssertionError('retry loop ended unexpectedly')

    async def _stream_once(
        self,
        request: dict[str, Any],
        allowed_names: set[str],
    ) -> AsyncIterator[ModelStreamEvent]:
        created = self._client.chat.completions.create(**request)
        stream = await created if inspect.isawaitable(created) else created
        pending: dict[int, _PendingCall] = {}
        finish_reason: str | None = None
        semantic_output = False
        last_usage: TokenUsage | None = None

        async for chunk in stream:
            raw_usage = _get(chunk, 'usage')
            if raw_usage is not None:
                usage = _usage(raw_usage)
                if usage != last_usage:
                    last_usage = usage
                    yield ModelUsageUpdate(usage=usage)
            for choice in _get(chunk, 'choices', ()) or ():
                raw_finish = _get(choice, 'finish_reason')
                if raw_finish is not None:
                    finish_reason = str(raw_finish)
                delta = _get(choice, 'delta')
                if delta is None:
                    continue
                text = _get(delta, 'content')
                if text:
                    semantic_output = True
                    yield ModelTextDelta(
                        text=str(text),
                        index=int(_get(choice, 'index', 0) or 0),
                    )
                for raw in _get(delta, 'tool_calls', ()) or ():
                    index = int(_get(raw, 'index', 0) or 0)
                    item = pending.setdefault(index, _PendingCall(index))
                    call_id = _get(raw, 'id')
                    if call_id:
                        item.call_id = str(call_id)
                    function = _get(raw, 'function')
                    if function is None:
                        continue
                    name = _get(function, 'name')
                    if name:
                        item.name += str(name)
                    arguments = _get(function, 'arguments')
                    if arguments is not None:
                        item.argument_parts.append(str(arguments))
                        async for event in _start_and_flush(item, allowed_names):
                            yield event

        if finish_reason in {'length', 'max_tokens'}:
            raise ModelOutputTruncatedError(
                tuple(item.name for item in pending.values() if item.name)
            )
        for index in sorted(pending):
            item = pending[index]
            async for event in _start_and_flush(item, allowed_names):
                yield event
            if not item.call_id or not item.name:
                raise ModelProtocolError(
                    f'Incomplete Qwen tool call at index {index}.',
                    reason='incomplete_tool_call',
                    tool_name=item.name or None,
                )
            if not item.allowed:
                raise ModelProtocolError(
                    f'Model requested unavailable tool: {item.name}.',
                    reason='unavailable_tool',
                    tool_name=item.name,
                )
            raw_arguments = ''.join(item.argument_parts).strip() or '{}'
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as error:
                raise ModelProtocolError(
                    f'Invalid JSON arguments for tool {item.name}.',
                    reason='invalid_tool_arguments',
                    tool_name=item.name,
                ) from error
            if not isinstance(arguments, dict):
                raise ModelProtocolError(
                    f'Arguments for {item.name} must be an object.',
                    reason='invalid_tool_arguments',
                    tool_name=item.name,
                )
            semantic_output = True
            yield ModelToolCallCompleted(
                tool_call=ToolCall(
                    index=index,
                    id=item.call_id,
                    name=item.name,
                    arguments=arguments,
                )
            )
        if not semantic_output:
            raise ModelProtocolError(
                'Qwen endpoint returned no visible text or tool calls.',
                reason='empty_model_response',
            )
        yield ModelResponseCompleted(stop_reason=finish_reason)


async def _start_and_flush(
    item: _PendingCall,
    allowed_names: set[str],
) -> AsyncIterator[ModelStreamEvent]:
    if (
        not item.started
        and item.call_id
        and item.name
        and item.name in allowed_names
    ):
        item.started = True
        item.allowed = True
        yield ModelToolCallStarted(
            index=item.index,
            id=item.call_id,
            name=item.name,
        )
    if item.started and item.allowed:
        while item.emitted_parts < len(item.argument_parts):
            part = item.argument_parts[item.emitted_parts]
            item.emitted_parts += 1
            yield ModelToolCallArgumentsDelta(
                index=item.index,
                partial_json=part,
            )


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get('type') == 'function' and isinstance(tool.get('function'), dict):
            result.append(dict(tool))
            continue
        result.append(
            {
                'type': 'function',
                'function': {
                    'name': str(tool.get('name', '')),
                    'description': str(tool.get('description', '')),
                    'parameters': tool.get('input_schema', {'type': 'object'}),
                },
            }
        )
    return result


def to_openai_messages(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if system is not None:
        result.append({'role': 'system', 'content': system})
    for message in messages:
        role = str(message.get('role', ''))
        content = message.get('content', '')
        if role == 'assistant':
            result.append(_assistant_to_openai(message))
        elif role == 'user' and isinstance(content, list):
            result.extend(_user_blocks_to_openai(content))
        elif role == 'tool':
            result.append(
                {
                    'role': 'tool',
                    'tool_call_id': str(message.get('tool_call_id', '')),
                    'content': _text(content),
                }
            )
        else:
            result.append({'role': role, 'content': _text(content)})
    return result


def _assistant_to_openai(message: dict[str, Any]) -> dict[str, Any]:
    if isinstance(message.get('tool_calls'), list):
        return dict(message)
    content = message.get('content', '')
    if not isinstance(content, list):
        return {'role': 'assistant', 'content': _text(content)}
    text_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            text_parts.append(str(block))
        elif block.get('type') == 'text':
            text_parts.append(str(block.get('text', '')))
        elif block.get('type') == 'tool_use':
            calls.append(
                {
                    'id': str(block.get('id', '')),
                    'type': 'function',
                    'function': {
                        'name': str(block.get('name', '')),
                        'arguments': json.dumps(
                            block.get('input', {}),
                            ensure_ascii=False,
                            separators=(',', ':'),
                        ),
                    },
                }
            )
    value: dict[str, Any] = {
        'role': 'assistant',
        'content': ''.join(text_parts) or None,
    }
    if calls:
        value['tool_calls'] = calls
    return value


def _user_blocks_to_openai(blocks: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict) and block.get('type') == 'tool_result':
            if text_parts:
                result.append({'role': 'user', 'content': ''.join(text_parts)})
                text_parts.clear()
            result.append(
                {
                    'role': 'tool',
                    'tool_call_id': str(block.get('tool_use_id', '')),
                    'content': _text(block.get('content', '')),
                }
            )
        elif isinstance(block, dict) and block.get('type') == 'text':
            text_parts.append(str(block.get('text', '')))
        else:
            text_parts.append(_text(block))
    if text_parts:
        result.append({'role': 'user', 'content': ''.join(text_parts)})
    return result or [{'role': 'user', 'content': ''}]


@dataclass(frozen=True, slots=True)
class EpisodeSource:
    '''License and split provenance written once and later verified in-place.'''

    task_id: str
    repository: str
    source_revision: str
    task_template: str
    split: Literal['train', 'validation', 'test']
    license_id: str
    source_is_public: bool
    benchmark: str | None = None
    behavior: str = 'tool_use'
    metadata: Mapping[str, Any] = field(default_factory=dict)


class TraceRecorder:
    '''Append-only provider-neutral recorder owned entirely by this extension.'''

    def __init__(
        self,
        path: Path,
        *,
        episode_id: str | None = None,
        execution_verified: bool = False,
    ) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.episode_id = episode_id or uuid4().hex
        self.execution_verified = execution_verified
        self.sequence = 0
        self.latest_request_id: str | None = None
        self.revision = 0
        self.call_to_request: dict[str, str] = {}
        self.pending_tool: ToolExecutionCompleted | None = None
        self.started = False
        self.finished = False

    def start(self, source: EpisodeSource) -> None:
        if self.started:
            raise RuntimeError('episode already started')
        self.started = True
        self.append('episode_started', asdict(source))

    def model_request(
        self,
        *,
        role: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
    ) -> str:
        request_id = f'req_{uuid4().hex}'
        self.latest_request_id = request_id
        self.append(
            'model_request',
            {
                'request_id': request_id,
                'role': role,
                'model': model,
                'system': _strip_assistant_reasoning(system),
                'messages': canonical_messages(messages),
                'tools': to_openai_tools(tools),
                'workspace_revision': self.revision,
                'enable_thinking': False,
            },
        )
        return request_id

    def model_response(
        self,
        *,
        request_id: str,
        text: str,
        calls: list[ToolCall],
        usage: TokenUsage,
        finish_reason: str | None,
        truncated: bool = False,
        error: Exception | None = None,
        partial_calls: Mapping[int, Mapping[str, Any]] | None = None,
    ) -> None:
        call_values = [
            {
                'index': call.index,
                'id': call.id,
                'name': call.name,
                'arguments': call.arguments,
            }
            for call in calls
        ]
        for call in calls:
            self.call_to_request[call.id] = request_id
        self.append(
            'model_response',
            {
                'request_id': request_id,
                'text': _strip_assistant_reasoning(text),
                'tool_calls': call_values,
                'usage': asdict(usage),
                'finish_reason': finish_reason,
                'truncated': truncated,
                'error': (
                    {
                        'type': type(error).__name__,
                        'reason': getattr(error, 'reason', 'model_error'),
                        'tool_name': getattr(error, 'tool_name', None),
                    }
                    if error is not None
                    else None
                ),
                'partial_tool_calls': list((partial_calls or {}).values()),
            },
        )

    def observe(self, event: ConversationEvent, *, validated: bool = False) -> None:
        if isinstance(event, ToolExecutionCompleted):
            self._flush_tool()
            self.pending_tool = event
        elif isinstance(event, WorkspaceChanged):
            before = self.revision
            self.revision = event.revision
            self._flush_tool(before=before, after=event.revision, paths=event.paths)
        elif isinstance(event, VerificationCompleted):
            self._flush_tool()
            evidence = event.evidence
            self.append(
                'verification',
                {
                    'command': evidence.command,
                    'cwd': evidence.cwd,
                    'exit_code': evidence.exit_code,
                    'timed_out': evidence.timed_out,
                    'duration_seconds': evidence.duration_seconds,
                    'workspace_revision': evidence.workspace_revision,
                    'passed': evidence.success,
                },
            )
        elif isinstance(event, TurnCompleted):
            self._flush_tool()
            result = event.result
            gate_accepted = result.status == 'completed'
            self.append(
                'outcome',
                {
                    'status': result.status,
                    'success': gate_accepted and validated,
                    'validated': validated,
                    'completion_gate_accepted': gate_accepted,
                    'completion_reasons': list(result.completion_reasons),
                    'final_text': result.text,
                    'final_revision': self.revision,
                    'changed_paths': list(result.changed_paths),
                    'usage': asdict(result.usage),
                    'model_calls': result.model_calls,
                    'tool_calls': len(result.tool_calls),
                },
            )
            self.finished = True

    def close(self) -> None:
        self._flush_tool()

    def _flush_tool(
        self,
        *,
        before: int | None = None,
        after: int | None = None,
        paths: Iterable[str] = (),
    ) -> None:
        event = self.pending_tool
        if event is None:
            return
        self.pending_tool = None
        call = event.tool_call
        result = event.result
        metadata = dict(result.metadata)
        error_code = result.error.code if result.error is not None else None
        rejected = error_code in {
            'invalid_arguments',
            'outside_task_scope',
            'permission_denied',
            'repeated_tool_call',
            'tool_not_available_in_phase',
            'unknown_tool',
        } or (isinstance(error_code, str) and error_code.startswith('not_executed_after_'))
        revision_before = self.revision if before is None else before
        revision_after = revision_before if after is None else after
        self.append(
            'tool_result',
            {
                'request_id': self.call_to_request.get(call.id, self.latest_request_id),
                'tool_call_id': call.id,
                'name': call.name,
                'arguments': call.arguments,
                'success': result.success,
                'result': {
                    'summary': result.summary,
                    'content': result.content,
                    'error': asdict(result.error) if result.error is not None else None,
                },
                'revision_before': revision_before,
                'revision_after': revision_after,
                'changed_paths': list(paths),
                'executed': bool(metadata.get('executed', not rejected)),
                'execution_verified': bool(
                    metadata.get('sandbox_verified', self.execution_verified)
                ),
                'permission_decision': metadata.get(
                    'permission_decision',
                    'deny' if error_code == 'permission_denied' else 'not_required',
                ),
                'permission_compliant': bool(metadata.get('permission_compliant', True)),
                'replayed': bool(metadata.get('replayed', False)),
                'idempotent': metadata.get('idempotent'),
            },
        )

    def append(self, record_type: str, payload: Mapping[str, Any]) -> None:
        record = {
            'schema_version': TRACE_VERSION,
            'episode_id': self.episode_id,
            'sequence': self.sequence,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'record_type': record_type,
            'payload': _json_safe(payload),
        }
        with self.path.open('a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write('\n')
        self.sequence += 1


class RecordingModelClient:
    '''Transparent wrapper; use it explicitly rather than patching a factory.'''

    provider = 'qwen-extension-recording'

    def __init__(
        self,
        client: ModelClient,
        recorder: TraceRecorder,
        *,
        role: str = 'main',
    ) -> None:
        self.client = client
        self.recorder = recorder
        self.role = role

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        request_id = self.recorder.model_request(
            role=self.role,
            system=system or '',
            messages=messages,
            tools=tools or [],
            model=str(getattr(self.client, 'model', 'unknown')),
        )
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        partial: dict[int, dict[str, Any]] = {}
        usage = TokenUsage(0, 0)
        finish_reason: str | None = None
        try:
            async for event in self.client.stream(messages, tools, system):
                if isinstance(event, ModelTextDelta):
                    text_parts.append(event.text)
                elif isinstance(event, ModelToolCallStarted):
                    partial[event.index] = {
                        'index': event.index,
                        'id': event.id,
                        'name': event.name,
                        'raw_arguments': '',
                    }
                elif isinstance(event, ModelToolCallArgumentsDelta):
                    partial.setdefault(
                        event.index,
                        {'index': event.index, 'id': None, 'name': None, 'raw_arguments': ''},
                    )['raw_arguments'] += event.partial_json
                elif isinstance(event, ModelToolCallCompleted):
                    calls.append(event.tool_call)
                    partial.pop(event.tool_call.index, None)
                elif isinstance(event, ModelUsageUpdate):
                    usage = event.usage
                elif isinstance(event, ModelResponseCompleted):
                    finish_reason = event.stop_reason
                yield event
        except Exception as error:
            self.recorder.model_response(
                request_id=request_id,
                text=''.join(text_parts),
                calls=calls,
                usage=usage,
                finish_reason='max_tokens' if isinstance(error, ModelOutputTruncatedError) else 'error',
                truncated=isinstance(error, ModelOutputTruncatedError),
                error=error,
                partial_calls=partial,
            )
            raise
        else:
            self.recorder.model_response(
                request_id=request_id,
                text=''.join(text_parts),
                calls=calls,
                usage=usage,
                finish_reason=finish_reason,
            )


OutcomeValidator = Callable[[Any], bool | Awaitable[bool]]


@dataclass(slots=True)
class RecordedConversation:
    '''Explicit opt-in composition of Conversation, Qwen and recording.'''

    conversation: Conversation
    recorder: TraceRecorder
    source: EpisodeSource
    outcome_validator: OutcomeValidator | None = None
    _started: bool = False

    async def stream(self, prompt: str) -> AsyncIterator[ConversationEvent]:
        if self._started:
            raise RuntimeError('RecordedConversation captures one episode only')
        self._started = True
        self.recorder.start(self.source)
        try:
            async for event in self.conversation.stream(prompt):
                validated = False
                if isinstance(event, TurnCompleted) and self.outcome_validator is not None:
                    value = self.outcome_validator(event.result)
                    validated = bool(await value) if inspect.isawaitable(value) else bool(value)
                self.recorder.observe(event, validated=validated)
                yield event
        finally:
            self.recorder.close()


@dataclass(frozen=True, slots=True)
class RolloutTask:
    '''One reproducible task used by the optional GRPO environment.'''

    task_id: str
    prompt: str
    source_revision: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RewardEvidence:
    '''Auditable reward components; dense shaping is capped separately.'''

    completion: float = 0.0
    safety: float = 0.0
    false_finish: float = 0.0
    schema: float = 0.0
    progress: float = 0.0
    recovery: float = 0.0
    reason: str = ''

    @property
    def total(self) -> float:
        terminal = self.completion + self.safety + self.false_finish
        dense = max(-0.25, min(0.25, self.schema + self.progress + self.recovery))
        return terminal + dense


@dataclass(frozen=True, slots=True)
class RolloutTransition:
    observation: Mapping[str, Any]
    done: bool
    reward: float
    evidence: RewardEvidence


RolloutToolExecutor = Callable[
    [Path, str, Mapping[str, Any], Mapping[str, Any]],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]
RolloutGate = Callable[
    [Path, str, RolloutTask],
    bool | Mapping[str, Any] | Awaitable[bool | Mapping[str, Any]],
]
PermissionChecker = Callable[[str, Mapping[str, Any]], bool]


class ForgeCodeRolloutEnvironment:
    '''Opt-in reset/step environment with one detached Git worktree per rollout.

    Tool execution and the Completion Gate are injected, which keeps policy and
    sandbox decisions outside this extension.  Duplicate non-idempotent calls
    are rejected instead of replayed.  ``close`` removes only the worktree that
    this instance created beneath its private temporary directory.
    '''

    def __init__(
        self,
        repository: Path,
        *,
        tools: Iterable[Mapping[str, Any]],
        tool_executor: RolloutToolExecutor,
        completion_gate: RolloutGate,
        permission_checker: PermissionChecker | None = None,
        mock_mcp: Mapping[str, Callable[[Mapping[str, Any]], Any] | Any] | None = None,
        max_decisions: int = 6,
        max_tool_calls: int = 10,
    ) -> None:
        self.repository = repository.resolve()
        if not (self.repository / '.git').exists():
            raise ValueError(f'{self.repository} is not a Git worktree')
        if max_decisions < 1 or max_tool_calls < 1:
            raise ValueError('rollout limits must be positive')
        self.tool_executor = tool_executor
        self.completion_gate = completion_gate
        self.permission_checker = permission_checker or (lambda _name, _args: True)
        self.mock_mcp = dict(mock_mcp or {})
        self.max_decisions = max_decisions
        self.max_tool_calls = max_tool_calls
        self.tool_schemas = {
            _tool_name(tool): (
                tool.get('function', {}).get('parameters', {})
                if tool.get('type') == 'function'
                else tool.get('input_schema', {})
            )
            for tool in tools
        }
        self._temp_root: Path | None = None
        self.workspace: Path | None = None
        self.task: RolloutTask | None = None
        self.decisions = 0
        self.tool_calls = 0
        self._seen_calls: set[str] = set()
        self._last_failed = False

    def reset(self, task: RolloutTask) -> Mapping[str, Any]:
        self.close()
        self._temp_root = Path(tempfile.mkdtemp(prefix='forgecode-rollout-')).resolve()
        self.workspace = self._temp_root / 'workspace'
        result = subprocess.run(
            [
                'git',
                '-C',
                str(self.repository),
                'worktree',
                'add',
                '--detach',
                str(self.workspace),
                task.source_revision,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            shutil.rmtree(self._temp_root, ignore_errors=True)
            self._temp_root = None
            self.workspace = None
            raise RuntimeError(f'cannot create rollout worktree: {result.stderr.strip()}')
        self.task = task
        resolved = self._git('rev-parse', 'HEAD').strip()
        expected = self._git_in_source('rev-parse', task.source_revision).strip()
        if resolved != expected:
            self.close()
            raise RuntimeError('rollout revision does not match requested source revision')
        self.decisions = 0
        self.tool_calls = 0
        self._seen_calls.clear()
        self._last_failed = False
        return {
            'task_id': task.task_id,
            'prompt': task.prompt,
            'workspace_revision': resolved,
            'tools': sorted(self.tool_schemas),
            'mock_mcp': sorted(self.mock_mcp),
        }

    async def step(self, action: Mapping[str, Any]) -> RolloutTransition:
        workspace, task = self._active()
        self.decisions += 1
        if self.decisions > self.max_decisions:
            evidence = RewardEvidence(false_finish=-0.8, reason='decision_limit')
            return RolloutTransition({'error': 'decision_limit'}, True, evidence.total, evidence)
        action_type = str(action.get('type', ''))
        if action_type == 'finish':
            answer = str(action.get('content', ''))
            gate_value = self.completion_gate(workspace, answer, task)
            gate_value = await gate_value if inspect.isawaitable(gate_value) else gate_value
            gate = dict(gate_value) if isinstance(gate_value, Mapping) else {'accepted': bool(gate_value)}
            accepted = bool(gate.get('accepted'))
            evidence = RewardEvidence(
                completion=1.0 if accepted else 0.0,
                false_finish=0.0 if accepted else -0.8,
                reason='completion_gate_accepted' if accepted else 'false_finish',
            )
            return RolloutTransition(
                {'completion_gate': _json_safe(gate), 'diff': self._git('diff', '--binary')},
                True,
                evidence.total,
                evidence,
            )
        if action_type != 'tool_call':
            evidence = RewardEvidence(safety=-1.0, reason='invalid_action')
            return RolloutTransition({'error': 'invalid_action'}, True, evidence.total, evidence)

        name = str(action.get('name', ''))
        arguments = action.get('arguments', {})
        call_id = str(action.get('id', ''))
        if not isinstance(arguments, Mapping):
            evidence = RewardEvidence(schema=-0.25, reason='arguments_not_object')
            return RolloutTransition({'error': evidence.reason}, False, evidence.total, evidence)
        if name not in self.tool_schemas:
            evidence = RewardEvidence(safety=-1.0, reason='unknown_tool')
            return RolloutTransition({'error': 'unknown_tool'}, True, evidence.total, evidence)
        schema_errors = validate_json_schema(dict(arguments), self.tool_schemas[name])
        if schema_errors:
            evidence = RewardEvidence(schema=-0.25, reason='schema_error')
            self._last_failed = True
            return RolloutTransition(
                {'error': 'schema_error', 'details': schema_errors},
                False,
                evidence.total,
                evidence,
            )
        if not self.permission_checker(name, arguments):
            evidence = RewardEvidence(safety=-1.0, reason='permission_denied')
            return RolloutTransition({'error': 'permission_denied'}, True, evidence.total, evidence)
        if self.tool_calls >= self.max_tool_calls:
            evidence = RewardEvidence(false_finish=-0.8, reason='tool_call_limit')
            return RolloutTransition({'error': 'tool_call_limit'}, True, evidence.total, evidence)
        idempotent = bool(action.get('idempotent', False))
        fingerprint = call_id or hashlib.sha256(
            json.dumps([name, arguments], sort_keys=True, default=str).encode()
        ).hexdigest()
        if fingerprint in self._seen_calls and not idempotent:
            evidence = RewardEvidence(recovery=-0.25, reason='non_idempotent_replay')
            return RolloutTransition({'error': evidence.reason}, False, evidence.total, evidence)
        self._seen_calls.add(fingerprint)
        self.tool_calls += 1
        before = self._workspace_fingerprint()
        if name in self.mock_mcp:
            mock = self.mock_mcp[name]
            result = mock(arguments) if callable(mock) else mock
            result = await result if inspect.isawaitable(result) else result
            output = dict(result) if isinstance(result, Mapping) else {'content': result}
            output['mocked'] = True
        else:
            value = self.tool_executor(workspace, name, arguments, task.metadata)
            value = await value if inspect.isawaitable(value) else value
            output = dict(value)
        after = self._workspace_fingerprint()
        changed = before != after
        succeeded = bool(output.get('success', True))
        recovered = self._last_failed and succeeded
        self._last_failed = not succeeded
        evidence = RewardEvidence(
            schema=0.02,
            progress=0.12 if changed and succeeded else 0.0,
            recovery=0.08 if recovered else 0.0,
            reason='tool_result',
        )
        observation = {
            'tool_call_id': call_id,
            'name': name,
            'result': _json_safe(output),
            'workspace_changed': changed,
            'workspace_revision': after,
            'diff': self._git('diff', '--binary'),
        }
        return RolloutTransition(observation, False, evidence.total, evidence)

    def close(self) -> None:
        workspace = self.workspace
        temp_root = self._temp_root
        self.workspace = None
        self._temp_root = None
        self.task = None
        if workspace is not None and workspace.exists():
            subprocess.run(
                ['git', '-C', str(self.repository), 'worktree', 'remove', '--force', str(workspace)],
                capture_output=True,
                check=False,
            )
        if temp_root is not None and temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)

    def __enter__(self) -> 'ForgeCodeRolloutEnvironment':
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def _active(self) -> tuple[Path, RolloutTask]:
        if self.workspace is None or self.task is None:
            raise RuntimeError('reset must be called before step')
        return self.workspace, self.task

    def _git(self, *arguments: str) -> str:
        workspace, _task = self._active()
        result = subprocess.run(
            ['git', '-C', str(workspace), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or 'git command failed')
        return result.stdout

    def _git_in_source(self, *arguments: str) -> str:
        result = subprocess.run(
            ['git', '-C', str(self.repository), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or 'git command failed')
        return result.stdout

    def _workspace_fingerprint(self) -> str:
        status = self._git('status', '--porcelain=v1', '--untracked-files=all')
        diff = self._git('diff', '--binary')
        return hashlib.sha256((status + '\0' + diff).encode()).hexdigest()


def create_recorded_conversation(
    *,
    endpoint: QwenEndpoint,
    recorder: TraceRecorder,
    source: EpisodeSource,
    conversation_kwargs: Mapping[str, Any] | None = None,
    outcome_validator: OutcomeValidator | None = None,
    sdk_client: Any | None = None,
) -> RecordedConversation:
    '''Create an opt-in session without changing CLI or ForgeConfig.'''
    qwen = QwenModelClient(endpoint, sdk_client=sdk_client)
    wrapped = RecordingModelClient(qwen, recorder, role=endpoint.role)
    kwargs = dict(conversation_kwargs or {})
    if 'client' in kwargs:
        raise ValueError('conversation_kwargs must not override client')
    conversation = Conversation(client=cast(ModelClient, wrapped), **kwargs)
    return RecordedConversation(
        conversation=conversation,
        recorder=recorder,
        source=source,
        outcome_validator=outcome_validator,
    )


def load_trace(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding='utf-8') as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise TraceRejected([f'{path}:{line_number}: invalid JSON']) from error
            if record.get('schema_version') != TRACE_VERSION:
                raise TraceRejected([f'{path}:{line_number}: unsupported schema'])
            records.append(record)
    return records


def clean_episode(
    records: Iterable[dict[str, Any]],
    *,
    allowed_licenses: frozenset[str] = ALLOWED_LICENSES,
) -> list[dict[str, Any]]:
    '''Validate provenance, secrets, schemas, permissions, execution and gate.'''
    values = sorted(records, key=lambda item: int(item.get('sequence', -1)))
    reasons: list[str] = []
    if not values or values[0].get('record_type') != 'episode_started':
        reasons.append('first record must be episode_started')
    if [item.get('sequence') for item in values] != list(range(len(values))):
        reasons.append('sequence numbers must be contiguous and start at zero')
    starts = [item['payload'] for item in values if item.get('record_type') == 'episode_started']
    outcomes = [item['payload'] for item in values if item.get('record_type') == 'outcome']
    if len(starts) != 1:
        reasons.append('episode must contain exactly one source record')
    if len(outcomes) != 1:
        reasons.append('episode must contain exactly one outcome')
    if starts:
        source = starts[0]
        if not source.get('source_is_public'):
            reasons.append('source repository is not public')
        if source.get('license_id') not in allowed_licenses:
            reasons.append(f'license {source.get("license_id")!r} is not approved')
        if source.get('split') == 'train' and _is_aider(source.get('benchmark')):
            reasons.append('Aider Polyglot is test-only')
    serialized = json.dumps(values, ensure_ascii=False)
    if any(pattern.search(serialized) for pattern in SECRET_PATTERNS):
        reasons.append('trace contains an unredacted secret pattern')

    requests: dict[str, dict[str, Any]] = {}
    calls: dict[str, tuple[str, str]] = {}
    results: set[str] = set()
    for record in values:
        payload = record.get('payload', {})
        kind = record.get('record_type')
        if kind == 'model_request':
            request_id = str(payload.get('request_id', ''))
            requests[request_id] = payload
            tool_map = {
                item['function']['name']: item['function']['parameters']
                for item in payload.get('tools', [])
                if isinstance(item, dict) and isinstance(item.get('function'), dict)
            }
            payload['_tool_map'] = tool_map
        elif kind == 'model_response':
            request_id = str(payload.get('request_id', ''))
            request = requests.get(request_id)
            if request is None:
                reasons.append(f'response references unknown request {request_id}')
                continue
            for call in payload.get('tool_calls', []):
                call_id = str(call.get('id', ''))
                name = str(call.get('name', ''))
                if call_id in calls:
                    reasons.append(f'duplicate tool call {call_id}')
                calls[call_id] = (request_id, name)
                schema = request['_tool_map'].get(name)
                if schema is None:
                    reasons.append(f'unknown tool {name!r}')
                else:
                    reasons.extend(
                        f'{call_id}: {problem}'
                        for problem in validate_json_schema(call.get('arguments'), schema)
                    )
        elif kind == 'tool_result':
            call_id = str(payload.get('tool_call_id', ''))
            results.add(call_id)
            if calls.get(call_id) != (
                payload.get('request_id'),
                payload.get('name'),
            ):
                reasons.append(f'tool result {call_id} is not bound to its request')
            if not payload.get('execution_verified'):
                reasons.append(f'{call_id}: execution was not sandbox verified')
            if payload.get('permission_decision') == 'deny' and payload.get('executed'):
                reasons.append(f'{call_id}: denied call was executed')
            if payload.get('replayed') and payload.get('idempotent') is not True:
                reasons.append(f'{call_id}: non-idempotent call was replayed')
    missing = calls.keys() - results
    if missing:
        reasons.append(f'missing tool results: {sorted(missing)!r}')
    if outcomes:
        outcome = outcomes[0]
        if not outcome.get('validated'):
            reasons.append('outcome was not independently validated')
        if outcome.get('success') and not outcome.get('completion_gate_accepted'):
            reasons.append('successful outcome bypassed Completion Gate')
    for request in requests.values():
        request.pop('_tool_map', None)
    if reasons:
        raise TraceRejected(reasons)
    return values


TokenCounter = Callable[[str], int]


def qwen_token_counter(model: str = DEFAULT_MODEL) -> TokenCounter:
    '''Load the exact Qwen tokenizer only when dataset export is requested.'''
    try:
        from transformers import AutoTokenizer
    except ImportError as error:
        raise RuntimeError(
            'Exact SFT slicing requires `pip install transformers`.'
        ) from error
    tokenizer = AutoTokenizer.from_pretrained(model, trust_remote_code=False)
    return lambda text: len(tokenizer.encode(text, add_special_tokens=False))


def build_sft_rows(
    records: Iterable[dict[str, Any]],
    *,
    token_counter: TokenCounter,
    max_tokens: int = 8_192,
) -> list[dict[str, Any]]:
    '''Build next-action rows without ever truncating system/tools/latest error.'''
    values = clean_episode(records)
    start = next(item['payload'] for item in values if item['record_type'] == 'episode_started')
    responses = {
        item['payload']['request_id']: item['payload']
        for item in values
        if item['record_type'] == 'model_response'
    }
    rows: list[dict[str, Any]] = []
    for record in values:
        if record['record_type'] != 'model_request':
            continue
        request = record['payload']
        response = responses.get(request['request_id'])
        if response is None or response.get('truncated') or response.get('error'):
            continue
        context = [
            {'role': 'system', 'content': request['system'], 'loss': False},
            *[
                {**message, 'loss': False}
                for message in request.get('messages', [])
            ],
        ]
        target = _swift_target(response)
        tools = request.get('tools', [])
        context = _fit_without_mutating_pinned(
            context,
            target,
            tools,
            token_counter=token_counter,
            max_tokens=max_tokens,
        )
        messages = _swift_messages([*context, *target])
        rows.append(
            {
                'schema_version': DATASET_VERSION,
                'messages': messages,
                'tools': json.dumps(tools, ensure_ascii=False, sort_keys=True),
                'metadata': {
                    'episode_id': record['episode_id'],
                    'request_id': request['request_id'],
                    'task_id': start['task_id'],
                    'repository': start['repository'],
                    'source_revision': start['source_revision'],
                    'task_template': start['task_template'],
                    'split': start['split'],
                    'behavior': start.get('behavior', 'tool_use'),
                },
            }
        )
    return rows


def write_sft_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write('\n')


def build_dpo_row(
    *,
    prompt_messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    chosen: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    chosen_score: tuple[int, int, float, int, float],
    rejected_score: tuple[int, int, float, int, float],
) -> dict[str, Any]:
    '''Create one near-miss preference row using safety-first ranking.'''
    if chosen_score <= rejected_score:
        raise ValueError('chosen must outrank rejected')
    return {
        'messages': _swift_messages([*prompt_messages, *chosen]),
        'rejected_response': _swift_messages(rejected),
        'tools': json.dumps(tools, ensure_ascii=False, sort_keys=True),
    }


def validate_split_manifest(
    assignments: Iterable[Mapping[str, Any]],
    *,
    require_aider_225: bool = True,
) -> None:
    '''Keep repositories in one split and reserve all Aider tasks for test.'''
    by_repository: dict[str, set[str]] = defaultdict(set)
    aider_ids: set[str] = set()
    reasons: list[str] = []
    for item in assignments:
        repository = str(item.get('repository', ''))
        split = str(item.get('split', ''))
        by_repository[repository].add(split)
        if _is_aider(item.get('benchmark')) or _is_aider(item.get('task_id')):
            aider_ids.add(str(item.get('task_id')))
            if split != 'test':
                reasons.append(f'{item.get("task_id")}: Aider must be test-only')
    leaked = sorted(repo for repo, splits in by_repository.items() if len(splits) > 1)
    if leaked:
        reasons.append(f'repositories cross splits: {leaked!r}')
    if require_aider_225 and len(aider_ids) != 225:
        reasons.append(f'Aider holdout contains {len(aider_ids)} tasks, expected 225')
    if reasons:
        raise TraceRejected(reasons)


TRAINING_RECIPES: dict[str, dict[str, Any]] = {
    'sft': {
        'model': DEFAULT_MODEL,
        'model_type': 'qwen3_5',
        'template': 'qwen3_5',
        'agent_template': 'qwen3_5',
        'tuner_type': 'lora',
        'target_modules': 'all-linear',
        'lora_rank': 32,
        'lora_alpha': 64,
        'quant_method': 'bnb',
        'quant_bits': 4,
        'bnb_4bit_quant_type': 'nf4',
        'torch_dtype': 'bfloat16',
        'freeze_vit': True,
        'freeze_aligner': True,
        'gradient_checkpointing': True,
        'max_length': 8192,
        'per_device_train_batch_size': 1,
        'gradient_accumulation_steps': 16,
        'learning_rate': 1e-4,
        'num_train_epochs': 2,
        'enable_thinking': False,
    },
    'dpo': {
        'rlhf_type': 'dpo',
        'template': 'qwen3_5',
        'agent_template': 'qwen3_5',
        'beta': 0.1,
        'learning_rate': 5e-6,
        'num_train_epochs': 1,
        'max_length': 8192,
        'enable_thinking': False,
    },
    'grpo': {
        'rlhf_type': 'grpo',
        'template': 'qwen3_5',
        'agent_template': 'qwen3_5',
        'num_generations': 4,
        'max_turns': 6,
        'vllm_max_model_len': 6144,
        'max_steps': 350,
        'enable_thinking': False,
    },
}

VLLM_SERVE = (
    'vllm serve {model} --language-model-only --enable-auto-tool-choice '
    '--tool-call-parser qwen3_coder --default-chat-template-kwargs '
    "'{\"enable_thinking\": false}'"
)
SGLANG_SERVE = (
    'python -m sglang.launch_server --model-path {model} --language-only '
    '--tool-call-parser qwen3_coder'
)


def write_training_assets(output_dir: Path) -> None:
    '''Materialize optional ms-swift recipes; generated files are not core code.'''
    try:
        import yaml
    except ImportError as error:
        raise RuntimeError('Recipe export requires `pip install pyyaml`.') from error
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, recipe in TRAINING_RECIPES.items():
        (output_dir / f'qwen35_4b_{name}.yaml').write_text(
            yaml.safe_dump(recipe, sort_keys=False),
            encoding='utf-8',
        )
    (output_dir / 'serve_vllm.txt').write_text(
        VLLM_SERVE.format(model=DEFAULT_MODEL) + '\n', encoding='utf-8'
    )
    (output_dir / 'serve_sglang.txt').write_text(
        SGLANG_SERVE.format(model=DEFAULT_MODEL) + '\n', encoding='utf-8'
    )


def preflight() -> dict[str, Any]:
    '''Return non-mutating WSL/CUDA/dependency evidence for this extension.'''
    checks: dict[str, Any] = {
        'wsl': 'microsoft' in os.uname().release.casefold() if hasattr(os, 'uname') else False,
        'nvidia_smi': shutil.which('nvidia-smi') is not None,
        'python': sys.version.split()[0],
    }
    if checks['nvidia_smi']:
        query = subprocess.run(
            [
                cast(str, shutil.which('nvidia-smi')),
                '--query-gpu=name,memory.total',
                '--format=csv,noheader,nounits',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        checks['gpu'] = query.stdout.strip()
    for package in ('torch', 'transformers', 'swift', 'openai'):
        try:
            module = __import__(package)
        except (ImportError, OSError):
            checks[package] = None
        else:
            checks[package] = getattr(module, '__version__', 'installed')
    if checks.get('torch'):
        import torch

        checks['cuda_available'] = bool(torch.cuda.is_available())
        checks['bf16_supported'] = bool(
            torch.cuda.is_available() and torch.cuda.is_bf16_supported()
        )
    return checks


def validate_json_schema(value: Any, schema: Mapping[str, Any], path: str = '$') -> list[str]:
    '''Validate the JSON Schema subset emitted by ForgeCode/Pydantic tools.'''
    errors: list[str] = []
    if 'const' in schema and value != schema['const']:
        return [f'{path} must equal {schema["const"]!r}']
    if 'enum' in schema and value not in schema['enum']:
        return [f'{path} must be one of {schema["enum"]!r}']
    if 'oneOf' in schema:
        matches = sum(not validate_json_schema(value, branch, path) for branch in schema['oneOf'])
        if matches != 1:
            errors.append(f'{path} must match exactly one oneOf branch')
    if 'anyOf' in schema and not any(
        not validate_json_schema(value, branch, path) for branch in schema['anyOf']
    ):
        errors.append(f'{path} does not match any anyOf branch')
    expected = schema.get('type')
    expected_values = expected if isinstance(expected, list) else [expected] if expected else []
    if expected_values and not any(_matches_type(value, item) for item in expected_values):
        return [f'{path} has the wrong type']
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value:
                errors.append(f'{path}.{key} is required')
        properties = schema.get('properties', {})
        for key, item in value.items():
            if key in properties:
                errors.extend(validate_json_schema(item, properties[key], f'{path}.{key}'))
            elif schema.get('additionalProperties') is False:
                errors.append(f'{path}.{key} is not allowed')
    elif isinstance(value, list):
        if isinstance(schema.get('items'), Mapping):
            for index, item in enumerate(value):
                errors.extend(validate_json_schema(item, schema['items'], f'{path}[{index}]'))
        if len(value) < int(schema.get('minItems', 0)):
            errors.append(f'{path} has too few items')
        if 'maxItems' in schema and len(value) > int(schema['maxItems']):
            errors.append(f'{path} has too many items')
    elif isinstance(value, str):
        if len(value) < int(schema.get('minLength', 0)):
            errors.append(f'{path} is too short')
        if 'maxLength' in schema and len(value) > int(schema['maxLength']):
            errors.append(f'{path} is too long')
        if 'pattern' in schema and re.search(str(schema['pattern']), value) is None:
            errors.append(f'{path} does not match pattern')
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if 'minimum' in schema and value < schema['minimum']:
            errors.append(f'{path} is below minimum')
        if 'maximum' in schema and value > schema['maximum']:
            errors.append(f'{path} is above maximum')
    return errors


def canonical_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in to_openai_messages(messages):
        value = dict(message)
        if value.get('role') == 'assistant':
            value = _strip_assistant_value(value)
        result.append(value)
    return result


def _fit_without_mutating_pinned(
    context: list[dict[str, Any]],
    target: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    token_counter: TokenCounter,
    max_tokens: int,
) -> list[dict[str, Any]]:
    messages = list(context)

    def cost(values: list[dict[str, Any]]) -> int:
        return token_counter(
            json.dumps({'messages': [*values, *target], 'tools': tools}, ensure_ascii=False)
        )

    latest_error = next(
        (
            message
            for message in reversed(messages[1:])
            if message.get('role') in {'tool', 'tool_response'}
            and any(
                marker in str(message.get('content', '')).casefold()
                for marker in ('error', 'failed', 'denied', 'conflict', 'timed out')
            )
        ),
        None,
    )
    while cost(messages) > max_tokens:
        removable = next(
            (
                index
                for index in range(1, len(messages) - 1)
                if messages[index] is not latest_error
            ),
            None,
        )
        if removable is None:
            break
        messages.pop(removable)
    if cost(messages) > max_tokens:
        raise TraceRejected(
            ['system/tools/latest error/target exceed the exact token budget']
        )
    return messages


def _swift_target(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    target: list[dict[str, Any]] = []
    text = _strip_assistant_reasoning(str(response.get('text', '')))
    if text:
        target.append({'role': 'assistant', 'content': text, 'loss': True})
    for call in response.get('tool_calls', []):
        target.append(
            {
                'role': 'tool_call',
                'content': json.dumps(
                    {'name': call['name'], 'arguments': call['arguments']},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                'loss': True,
            }
        )
    if not target:
        raise TraceRejected(['model response has no visible target'])
    return target


def _swift_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        role = message.get('role')
        if role == 'tool':
            role = 'tool_response'
        if role == 'assistant' and message.get('tool_calls'):
            if message.get('content'):
                result.append(
                    {'role': 'assistant', 'content': message['content'], 'loss': bool(message.get('loss'))}
                )
            for call in message['tool_calls']:
                function = call['function']
                arguments = function.get('arguments', '{}')
                try:
                    arguments = json.loads(arguments)
                except (TypeError, json.JSONDecodeError):
                    pass
                result.append(
                    {
                        'role': 'tool_call',
                        'content': json.dumps(
                            {'name': function.get('name'), 'arguments': arguments},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        'loss': bool(message.get('loss')),
                    }
                )
            continue
        result.append(
            {
                'role': role,
                'content': message.get('content', ''),
                'loss': bool(message.get('loss')),
            }
        )
    return result


def _usage(raw: Any) -> TokenUsage:
    prompt = int(_get(raw, 'prompt_tokens', _get(raw, 'input_tokens', 0)) or 0)
    completion = int(
        _get(raw, 'completion_tokens', _get(raw, 'output_tokens', 0)) or 0
    )
    details = _get(raw, 'prompt_tokens_details')
    cached = min(prompt, max(0, int(_get(details, 'cached_tokens', 0) or 0)))
    return TokenUsage(
        input_tokens=max(0, prompt - cached),
        output_tokens=max(0, completion),
        cache_read_input_tokens=cached,
    )


def _classify_openai_error(error: Exception) -> tuple[str, bool]:
    name = type(error).__name__.casefold()
    status = getattr(error, 'status_code', None)
    if 'timeout' in name:
        return 'timeout', True
    if 'connection' in name:
        return 'connection_error', True
    if 'ratelimit' in name or status == 429:
        return 'rate_limit', True
    if status is not None and int(status) >= 500:
        return 'server_error', True
    return f'http_{status}' if status is not None else 'provider_error', False


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _tool_name(tool: Mapping[str, Any]) -> str:
    function = tool.get('function')
    if tool.get('type') == 'function' and isinstance(function, Mapping):
        return str(function.get('name', ''))
    return str(tool.get('name', ''))


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ''
    return json.dumps(value, ensure_ascii=False, default=str)


def _strip_assistant_reasoning(text: str) -> str:
    return THINK_BLOCK.sub('', text).strip()


def _strip_assistant_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_assistant_value(item)
            for key, item in value.items()
            if str(key).casefold() not in HIDDEN_KEYS
        }
    if isinstance(value, list):
        return [_strip_assistant_value(item) for item in value]
    if isinstance(value, str):
        return _strip_assistant_reasoning(value)
    return value


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _matches_type(value: Any, expected: str) -> bool:
    checks: dict[str, Callable[[Any], bool]] = {
        'object': lambda item: isinstance(item, dict),
        'array': lambda item: isinstance(item, list),
        'string': lambda item: isinstance(item, str),
        'integer': lambda item: isinstance(item, int) and not isinstance(item, bool),
        'number': lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        'boolean': lambda item: isinstance(item, bool),
        'null': lambda item: item is None,
    }
    return checks.get(expected, lambda item: True)(value)


def _is_aider(value: Any) -> bool:
    normalized = re.sub(r'[^a-z0-9]+', '', str(value or '').casefold())
    return normalized.startswith('aiderpolyglot')


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest='command', required=True)
    subcommands.add_parser('preflight')
    recipes = subcommands.add_parser('write-recipes')
    recipes.add_argument('output', type=Path)
    build = subcommands.add_parser('build-sft')
    build.add_argument('trace', type=Path)
    build.add_argument('output', type=Path)
    build.add_argument('--model', default=DEFAULT_MODEL)
    arguments = parser.parse_args()
    if arguments.command == 'preflight':
        print(json.dumps(preflight(), ensure_ascii=False, indent=2))
        return 0
    if arguments.command == 'write-recipes':
        write_training_assets(arguments.output)
        return 0
    if arguments.command == 'build-sft':
        rows = build_sft_rows(
            load_trace(arguments.trace),
            token_counter=qwen_token_counter(arguments.model),
        )
        write_sft_jsonl(arguments.output, rows)
        return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(_cli())


__all__ = [
    'ALLOWED_LICENSES',
    'DATASET_VERSION',
    'DEFAULT_BASE_URL',
    'DEFAULT_MODEL',
    'EpisodeSource',
    'ExtensionConfigurationError',
    'ForgeCodeRolloutEnvironment',
    'QwenEndpoint',
    'QwenModelClient',
    'RecordedConversation',
    'RecordingModelClient',
    'RewardEvidence',
    'RolloutTask',
    'RolloutTransition',
    'TRACE_VERSION',
    'TRAINING_RECIPES',
    'TraceRecorder',
    'TraceRejected',
    'build_dpo_row',
    'build_sft_rows',
    'clean_episode',
    'create_recorded_conversation',
    'load_trace',
    'preflight',
    'qwen_token_counter',
    'qwen_role_endpoints',
    'to_openai_messages',
    'to_openai_tools',
    'validate_json_schema',
    'validate_split_manifest',
    'write_sft_jsonl',
    'write_training_assets',
]
