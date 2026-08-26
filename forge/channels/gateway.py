'''Conversation routing, durable deduplication, and chat approvals.'''

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from forge.channels.base import ChannelAdapter
from forge.channels.config import ChannelConfig
from forge.channels.models import ApprovalAction, InboundMessage, OutboundMessage
from forge.permissions.policy import ApprovalResponse, PermissionRequest
from forge.runtime.state import (
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    ModelTextDelta,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
)


_SENSITIVE_LOG_KEYS = ('secret', 'token', 'api_key', 'password', 'authorization')


def _safe_log_value(value: Any, *, key: str = '') -> Any:
    if any(marker in key.lower() for marker in _SENSITIVE_LOG_KEYS):
        return '***'
    if isinstance(value, dict):
        return {
            str(item_key): _safe_log_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_log_value(item) for item in value]
    return value


def _log_preview(value: Any, *, limit: int = 500) -> str:
    if isinstance(value, str):
        rendered = value
    else:
        try:
            rendered = json.dumps(
                _safe_log_value(value),
                ensure_ascii=False,
                default=str,
            )
        except (TypeError, ValueError):
            rendered = str(value)
    rendered = rendered.replace('\r', '\\r').replace('\n', '\\n')
    return rendered if len(rendered) <= limit else rendered[:limit] + '…'


@dataclass(slots=True)
class PendingApproval:
    approval_id: str
    arguments_hash: str
    requester_id: str
    future: asyncio.Future[ApprovalResponse]


class ApprovalBroker:
    '''Bind one exact write request to its originating chat user.'''

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}

    async def request(
        self,
        adapter: ChannelAdapter,
        message: InboundMessage,
        request: PermissionRequest,
        *,
        timeout_seconds: float,
    ) -> ApprovalResponse:
        arguments_hash = request.arguments_hash or hashlib.sha256(
            f'{request.signature}\x1f{request.preview}'.encode('utf-8')
        ).hexdigest()
        approval_id = uuid4().hex
        future: asyncio.Future[ApprovalResponse] = (
            asyncio.get_running_loop().create_future()
        )
        pending = PendingApproval(
            approval_id,
            arguments_hash,
            message.sender_id,
            future,
        )
        self._pending[approval_id] = pending
        try:
            await adapter.request_approval(
                chat_id=message.chat_id,
                requester_id=message.sender_id,
                approval_id=approval_id,
                arguments_hash=arguments_hash,
                request=request,
            )
            try:
                return await asyncio.wait_for(future, timeout_seconds)
            except TimeoutError:
                return ApprovalResponse('deny', 'Chat approval timed out.')
        finally:
            self._pending.pop(approval_id, None)

    async def resolve(self, action: ApprovalAction) -> bool:
        pending = self._pending.get(action.approval_id)
        if pending is None or pending.future.done():
            return False
        if action.actor_id != pending.requester_id:
            return False
        if action.arguments_hash != pending.arguments_hash:
            return False
        if action.decision not in {'approve', 'deny'}:
            return False
        pending.future.set_result(
            ApprovalResponse(
                'allow_once' if action.decision == 'approve' else 'deny',
                'Approved in chat.' if action.decision == 'approve' else 'Denied in chat.',
            )
        )
        return True

    def close(self) -> None:
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_result(
                    ApprovalResponse('deny', 'Gateway stopped.')
                )
        self._pending.clear()


class DurableEventDeduplicator:
    '''At-most-once reservation of platform event IDs across restarts.'''

    def __init__(self, path: Path, *, maximum_entries: int = 20_000) -> None:
        self.path = path
        self.maximum_entries = maximum_entries
        self._digests: set[str] = set()
        self._order: list[str] = []
        if path.is_file():
            try:
                lines = path.read_text(encoding='utf-8').splitlines()
                self._order = list(dict.fromkeys(lines))[-maximum_entries:]
                self._digests.update(self._order)
            except OSError:
                pass

    def reserve(self, message: InboundMessage) -> bool:
        digest = hashlib.sha256(
            f'{message.platform}\x1f{message.tenant_id}\x1f{message.message_id}'.encode(
                'utf-8'
            )
        ).hexdigest()
        if digest in self._digests:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='ascii', newline='\n') as stream:
            stream.write(digest + '\n')
        self._digests.add(digest)
        self._order.append(digest)
        if len(self._digests) > self.maximum_entries:
            retained = self._order[-self.maximum_entries :]
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text('\n'.join(retained) + '\n', encoding='ascii')
            temporary.replace(self.path)
            self._digests = set(retained)
            self._order = retained
        return True


class ChannelSessionIndex:
    '''Durably associate a normalized chat with one ForgeCode session.'''

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mapping: dict[str, str] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(raw, dict):
                    self._mapping = {
                        str(key): str(value) for key, value in raw.items()
                    }
            except (OSError, json.JSONDecodeError):
                pass

    def get(self, key: str) -> str | None:
        return self._mapping.get(key)

    def set(self, key: str, session_id: str) -> None:
        self._mapping[key] = session_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(
            json.dumps(self._mapping, ensure_ascii=False, sort_keys=True),
            encoding='utf-8',
        )
        temporary.replace(self.path)


RuntimeFactory = Callable[[str | None], tuple[Any, Any, Any]]
ProgressReporter = Callable[[str], None]


class ChannelGateway:
    '''Route allowed channel messages into isolated Conversation runtimes.'''

    def __init__(
        self,
        *,
        adapter: ChannelAdapter,
        config: ChannelConfig,
        runtime_factory: RuntimeFactory,
        state_directory: Path,
        progress: ProgressReporter | None = None,
    ) -> None:
        self.adapter = adapter
        self.config = config
        self.runtime_factory = runtime_factory
        self.progress = progress
        self.approvals = ApprovalBroker()
        self.deduplicator = DurableEventDeduplicator(
            state_directory / 'channel-events.log'
        )
        self.sessions = ChannelSessionIndex(
            state_directory / 'channel-sessions.json'
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self._runtimes: dict[str, Any] = {}

    async def run(self) -> None:
        self._report('[Feishu] waiting for messages')
        await self.adapter.start(self.handle_message, self.approvals.resolve)

    async def close(self) -> None:
        self._report('[Gateway] stopping')
        self.approvals.close()
        await self.adapter.stop()
        runtime_closures = []
        for runtime in self._runtimes.values():
            close = getattr(runtime, 'runtime_close', None)
            if close is not None:
                runtime_closures.append(close(reason='gateway_stop'))
        self._runtimes.clear()
        if runtime_closures:
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*runtime_closures, return_exceptions=True),
                    timeout=5.0,
                )
                for result in results:
                    if isinstance(result, BaseException):
                        self._report(
                            f'[Gateway] runtime cleanup failed '
                            f'type={type(result).__name__}'
                        )
            except TimeoutError:
                self._report('[Gateway] runtime cleanup timed out after 5 seconds')
        self._report('[Gateway] stopped')

    async def handle_message(self, message: InboundMessage) -> None:
        if not self.config.accepts(message):
            self._report(
                f'[Feishu] ignored unauthorized message={message.message_id or "missing"}'
            )
            return
        if not message.message_id:
            self._report('[Feishu] ignored message without message_id')
            return
        if not self.deduplicator.reserve(message):
            self._report(f'[Feishu] ignored duplicate message={message.message_id}')
            return
        topic = message.thread_id or 'main'
        self._report(
            f'[Feishu] received message={message.message_id} '
            f'chat={message.chat_id} topic={topic} sender={message.sender_id} '
            f'text={_log_preview(message.text, limit=300)}'
        )
        key = message.session_key
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            runtime = self._runtimes.get(key)
            if runtime is None:
                identifier = self.sessions.get(key)
                runtime, journal, _ = self.runtime_factory(identifier)
                self._runtimes[key] = runtime
                self.sessions.set(key, journal.session_id)
                self._report(
                    f'[Session] {"resumed" if identifier else "created"} '
                    f'session={journal.session_id} topic={topic}'
                )
                start = getattr(runtime, 'session_start', None)
                if start is not None:
                    await start(source=f'channel:{message.platform}')
            manager = getattr(runtime, 'permission_manager', None)
            if manager is not None:
                async def approve(request: PermissionRequest) -> ApprovalResponse:
                    return await self.approvals.request(
                        self.adapter,
                        message,
                        request,
                        timeout_seconds=self.config.approval_timeout_seconds,
                    )

                manager.approval_handler = approve

            response_parts: list[str] = []
            result_text = ''
            try:
                async for event in runtime.stream(message.text):
                    record = getattr(runtime, 'record_session_event', None)
                    if record is not None:
                        record(event)
                    if isinstance(event, ModelTextDelta):
                        response_parts.append(event.text)
                    elif isinstance(event, ModelCallStarted):
                        self._report(
                            f'[Model] call started iteration={event.iteration}'
                        )
                    elif isinstance(event, ModelCallCompleted):
                        self._report(
                            f'[Model] call completed iteration={event.iteration}'
                        )
                    elif isinstance(event, ModelCallFailed):
                        self._report(
                            f'[Model] call failed iteration={event.iteration} '
                            f'retryable={event.retryable} '
                            f'reason={_log_preview(event.reason)}'
                        )
                    elif isinstance(event, ToolExecutionStarted):
                        self._report(
                            f'[Tool] started name={event.tool_call.name} '
                            f'id={event.tool_call.id} '
                            f'arguments={_log_preview(event.tool_call.arguments)}'
                        )
                    elif isinstance(event, ToolExecutionCompleted):
                        error_code = (
                            event.result.error.code
                            if event.result.error is not None
                            else '-'
                        )
                        self._report(
                            f'[Tool] completed name={event.tool_call.name} '
                            f'id={event.tool_call.id} '
                            f'success={event.result.success} error={error_code} '
                            f'summary={_log_preview(event.result.summary)} '
                            f'content={_log_preview(event.result.content)}'
                        )
                    elif isinstance(event, TurnCompleted):
                        result_text = event.result.text
                        self._report(
                            f'[Turn] completed status={event.result.status} '
                            f'model_calls={event.result.model_calls} '
                            f'tokens={event.result.usage.total_tokens} '
                            f'changed_paths={_log_preview(event.result.changed_paths)}'
                        )
            except Exception as error:
                recorder = getattr(runtime, 'record_session_error', None)
                if recorder is not None:
                    recorder(error)
                result_text = (
                    'ForgeCode failed to process this request: '
                    f'{type(error).__name__}'
                )
                self._report(
                    f'[ForgeCode] request failed type={type(error).__name__}'
                )
            text = result_text or ''.join(response_parts).strip()
            if text:
                await self.adapter.send(
                    OutboundMessage(
                        chat_id=message.chat_id,
                        text=text,
                        reply_to_message_id=message.message_id,
                        thread_id=message.thread_id,
                    )
                )
                self._report(
                    f'[Feishu] reply sent message={message.message_id} '
                    f'topic={topic} characters={len(text)} '
                    f'text={_log_preview(text, limit=300)}'
                )

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)
