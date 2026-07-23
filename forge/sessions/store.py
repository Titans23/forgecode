'''Crash-tolerant append-only session persistence for ForgeCode.'''

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from forge.runtime.state import TurnResult
from forge.tasks.state import ActiveTask


SESSION_ID_PATTERN = re.compile(r'session-[0-9a-f]{24}')
SCHEMA_VERSION = 1
DEFAULT_INLINE_PAYLOAD_BYTES = 256_000


class SessionError(RuntimeError):
    '''Base error for persisted ForgeCode sessions.'''


class SessionNotFoundError(SessionError):
    '''Raised when a requested session does not exist in this project.'''


class SessionCorruptError(SessionError):
    '''Raised when durable session history is internally inconsistent.'''


@dataclass(frozen=True, slots=True)
class SessionInfo:
    session_id: str
    project_root: Path
    path: Path
    created_at: str
    updated_at: str
    model: str
    name: str | None
    title: str
    status: str
    sequence: int


@dataclass(frozen=True, slots=True)
class SessionState:
    info: SessionInfo
    messages: tuple[dict[str, Any], ...]
    active_task: ActiveTask | None
    indeterminate_tools: tuple[dict[str, Any], ...]


class SessionJournal:
    '''Append exact resumable events for one session.'''

    def __init__(
        self,
        path: Path,
        *,
        session_id: str,
        project_root: Path,
        sequence: int = 0,
        parent_uuid: str | None = None,
        inline_payload_bytes: int = DEFAULT_INLINE_PAYLOAD_BYTES,
    ) -> None:
        self.path = path
        self.session_id = session_id
        self.project_root = project_root.resolve()
        self.sequence = sequence
        self.parent_uuid = parent_uuid
        self.inline_payload_bytes = inline_payload_bytes
        self.artifact_directory = path.parent / 'artifacts' / session_id

    def record_user_message(
        self,
        message: dict[str, Any],
        task: ActiveTask | None,
    ) -> None:
        self.append('user_message', {'message': message})
        self.record_task_state(task)

    def record_assistant_message(self, message: dict[str, Any]) -> None:
        self.append('assistant_message', {'message': message})
        content = message.get('content')
        if not isinstance(content, list):
            return
        for index, block in enumerate(content):
            if not isinstance(block, dict) or block.get('type') != 'tool_use':
                continue
            self.append(
                'tool_requested',
                {
                    'tool_call_id': str(block.get('id', '')),
                    'index': index,
                    'name': str(block.get('name', '')),
                    'arguments': block.get('input', {}),
                },
            )

    def record_tool_result_message(
        self,
        message: dict[str, Any],
        task: ActiveTask | None,
    ) -> None:
        self.append('tool_result_message', {'message': message})
        self.record_task_state(task)

    def record_context_compacted(
        self,
        messages: list[dict[str, Any]],
    ) -> None:
        self.append('context_compacted', {'messages': messages})

    def record_tool_started(
        self,
        tool_call_id: str,
        name: str,
        arguments: dict[str, Any],
    ) -> None:
        self.append(
            'tool_started',
            {
                'tool_call_id': tool_call_id,
                'name': name,
                'arguments': arguments,
            },
        )

    def record_tool_completed(
        self,
        tool_call_id: str,
        name: str,
        success: bool,
    ) -> None:
        self.append(
            'tool_completed',
            {
                'tool_call_id': tool_call_id,
                'name': name,
                'success': success,
            },
        )

    def record_task_state(self, task: ActiveTask | None) -> None:
        self.append(
            'task_state',
            {'task': task.as_dict() if task is not None else None},
        )

    def record_turn_completed(
        self,
        messages: list[dict[str, Any]],
        task: ActiveTask | None,
        result: TurnResult,
    ) -> None:
        # This checkpoint preserves internal recovery feedback that is not
        # represented by the public user/assistant/tool-result events.
        self.append('message_checkpoint', {'messages': messages})
        self.record_task_state(task)
        self.append(
            'turn_completed',
            {
                'status': result.status,
                'text': result.text,
                'usage': asdict(result.usage),
                'changed_paths': result.changed_paths,
                'verification': (
                    asdict(result.verification)
                    if result.verification is not None
                    else None
                ),
                'completion_reasons': result.completion_reasons,
            },
        )

    def record_error(self, error: Exception) -> None:
        self.append(
            'turn_error',
            {
                'error_type': type(error).__name__,
                'message': str(error),
            },
        )

    def record_resumed(self) -> None:
        self.append('session_resumed', {'cwd': str(self.project_root)})

    def record_checkpoint_created(
        self,
        checkpoint_id: str,
        messages: list[dict[str, Any]],
        task: ActiveTask | None,
    ) -> None:
        self.append(
            'checkpoint_created',
            {
                'checkpoint_id': checkpoint_id,
                'messages': messages,
                'task': task.as_dict() if task is not None else None,
            },
        )

    def rename(self, name: str) -> str:
        cleaned = clean_session_name(name)
        if cleaned is None:
            raise ValueError('Session name must not be empty.')
        self.append('session_renamed', {'name': cleaned})
        return cleaned

    def record_stopped(self) -> None:
        self.append('session_stopped', {})

    def record_hook_execution(
        self,
        execution: dict[str, Any],
        *,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        paths: tuple[str, ...] = (),
    ) -> None:
        '''Append one sanitized lifecycle hook audit event.'''
        self.append(
            'hook_execution',
            {
                'execution': execution,
                'tool_name': tool_name,
                'tool_call_id': tool_call_id,
                'paths': paths,
            },
        )

    def append(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event_uuid = str(uuid4())
        self.sequence += 1
        encoded_payload = json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
            separators=(',', ':'),
        ).encode('utf-8')
        record: dict[str, Any] = {
            'schema_version': SCHEMA_VERSION,
            'uuid': event_uuid,
            'parent_uuid': self.parent_uuid,
            'session_id': self.session_id,
            'sequence': self.sequence,
            'timestamp': now_iso(),
            'type': event_type,
        }
        if len(encoded_payload) > self.inline_payload_bytes:
            record['payload_ref'] = self._write_artifact(
                event_uuid,
                encoded_payload,
            )
        else:
            record['payload'] = payload
        serialized = json.dumps(
            record,
            ensure_ascii=False,
            default=str,
            separators=(',', ':'),
        )
        with self.path.open('a', encoding='utf-8', newline='\n') as file:
            file.write(serialized)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        self.parent_uuid = event_uuid
        return record

    def _write_artifact(
        self,
        event_uuid: str,
        content: bytes,
    ) -> dict[str, str]:
        self.artifact_directory.mkdir(parents=True, exist_ok=True)
        path = self.artifact_directory / f'{event_uuid}.json'
        temporary = path.with_suffix('.json.tmp')
        temporary.write_bytes(content)
        temporary.replace(path)
        return {
            'path': path.relative_to(self.path.parent).as_posix(),
            'sha256': hashlib.sha256(content).hexdigest(),
        }


class SessionStore:
    '''Locate, create, load, and list project-scoped sessions.'''

    def __init__(
        self,
        project_root: Path,
        *,
        data_root: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        configured_root = os.environ.get('FORGE_DATA_DIR')
        self.data_root = (
            data_root.resolve()
            if data_root is not None
            else Path(configured_root).expanduser().resolve()
            if configured_root
            else (Path.home() / '.forge').resolve()
        )
        self.project_key = project_storage_key(self.project_root)
        self.directory = (
            self.data_root / 'projects' / self.project_key / 'sessions'
        )
        self.index_path = self.directory.parent / 'index.json'

    def create(
        self,
        *,
        model: str,
        name: str | None = None,
    ) -> SessionJournal:
        if self.index_path.exists():
            self.index_path.unlink()
        session_id = f'session-{uuid4().hex[:24]}'
        path = self.directory / f'{session_id}.jsonl'
        journal = SessionJournal(
            path,
            session_id=session_id,
            project_root=self.project_root,
        )
        journal.append(
            'session_started',
            {
                'cwd': str(self.project_root),
                'model': model,
                'name': clean_session_name(name),
                'project_key': self.project_key,
            },
        )
        return journal

    def fork(
        self,
        source: SessionState,
        *,
        messages: list[dict[str, Any]],
        task: ActiveTask | None,
        model: str,
        name: str | None = None,
    ) -> SessionJournal:
        journal = self.create(model=model, name=name)
        journal.append(
            'session_forked',
            {'source_session_id': source.info.session_id},
        )
        journal.append('message_checkpoint', {'messages': messages})
        journal.record_task_state(task)
        return journal

    def open(
        self,
        identifier: str | None = None,
    ) -> tuple[SessionState, SessionJournal]:
        state = (
            self.latest()
            if identifier is None
            else self.load(identifier)
        )
        records = self._read_records(state.info.path)
        last = records[-1]
        journal = SessionJournal(
            state.info.path,
            session_id=state.info.session_id,
            project_root=self.project_root,
            sequence=int(last['sequence']),
            parent_uuid=str(last['uuid']),
        )
        return state, journal

    def checkpoint_state(
        self,
        session_id: str,
        checkpoint_id: str,
    ) -> tuple[list[dict[str, Any]], ActiveTask | None]:
        state = self.load(session_id)
        for record in reversed(self._read_records(state.info.path)):
            if record.get('type') != 'checkpoint_created':
                continue
            payload = self._payload(record, state.info.path)
            if payload.get('checkpoint_id') != checkpoint_id:
                continue
            messages = payload.get('messages')
            if not isinstance(messages, list) or not all(
                isinstance(item, dict) for item in messages
            ):
                raise SessionCorruptError(
                    f'Invalid checkpoint conversation: {checkpoint_id}'
                )
            task_payload = payload.get('task')
            task = (
                ActiveTask.from_dict(task_payload)
                if isinstance(task_payload, dict)
                else None
            )
            return [dict(item) for item in messages], task
        raise SessionNotFoundError(
            f'Conversation checkpoint not found: {checkpoint_id}'
        )

    def history(self, identifier: str) -> tuple[dict[str, Any], ...]:
        state = self.load(identifier)
        history: list[dict[str, Any]] = []
        for record in self._read_records(state.info.path):
            payload = self._payload(record, state.info.path)
            history.append(
                {
                'sequence': int(record['sequence']),
                'timestamp': str(record['timestamp']),
                'type': str(record['type']),
                    'summary': event_summary(
                        str(record['type']),
                        payload,
                    ),
                }
            )
        return tuple(history)

    def latest(self) -> SessionState:
        sessions = self.list()
        if not sessions:
            raise SessionNotFoundError(
                f'No saved ForgeCode session for {self.project_root}'
            )
        return self.load(sessions[0].session_id)

    def load(self, identifier: str) -> SessionState:
        clean_identifier = identifier.strip()
        if not clean_identifier:
            raise SessionNotFoundError('Session identifier must not be empty.')
        direct_path = self.directory / f'{clean_identifier}.jsonl'
        if direct_path.is_file() and SESSION_ID_PATTERN.fullmatch(
            clean_identifier
        ):
            return self._build_state(direct_path)
        matches = [
            info
            for info in self.list()
            if info.name == clean_identifier
        ]
        if not matches:
            raise SessionNotFoundError(
                f'Session not found in this project: {clean_identifier}'
            )
        if len(matches) > 1:
            raise SessionError(
                f'Multiple sessions are named {clean_identifier!r}; '
                'resume one by session ID.'
            )
        return self._build_state(matches[0].path)

    def list(self) -> tuple[SessionInfo, ...]:
        if not self.directory.exists():
            return ()
        cached = self._read_index()
        if cached is not None:
            return cached
        sessions: list[SessionInfo] = []
        for path in self.directory.glob('session-*.jsonl'):
            try:
                sessions.append(self._build_state(path).info)
            except SessionError:
                continue
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        result = tuple(sessions)
        self._write_index(result)
        return result

    def _read_index(self) -> tuple[SessionInfo, ...] | None:
        if not self.index_path.is_file():
            return None
        try:
            payload = json.loads(self.index_path.read_text(encoding='utf-8'))
            entries = payload['sessions']
            if not isinstance(entries, list):
                return None
            sessions: list[SessionInfo] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    return None
                path = (
                    self.directory
                    / (str(entry['session_id']) + '.jsonl')
                )
                if (
                    not path.is_file()
                    or path.stat().st_mtime_ns != entry.get('mtime_ns')
                ):
                    return None
                sessions.append(
                    SessionInfo(
                        session_id=str(entry['session_id']),
                        project_root=self.project_root,
                        path=path,
                        created_at=str(entry['created_at']),
                        updated_at=str(entry['updated_at']),
                        model=str(entry.get('model', '')),
                        name=optional_string(entry.get('name')),
                        title=str(
                            entry.get('title')
                            or entry.get('session_id', '')
                        ),
                        status=str(entry.get('status', 'active')),
                        sequence=int(entry['sequence']),
                    )
                )
            return tuple(sessions)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write_index(self, sessions: tuple[SessionInfo, ...]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'schema_version': SCHEMA_VERSION,
            'project_root': str(self.project_root),
            'sessions': [
                {
                    'session_id': info.session_id,
                    'created_at': info.created_at,
                    'updated_at': info.updated_at,
                    'model': info.model,
                    'name': info.name,
                    'title': info.title,
                    'status': info.status,
                    'sequence': info.sequence,
                    'mtime_ns': info.path.stat().st_mtime_ns,
                }
                for info in sessions
            ],
        }
        temporary = self.index_path.with_suffix('.json.tmp')
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        temporary.replace(self.index_path)

    def _build_state(self, path: Path) -> SessionState:
        records = self._read_records(path)
        if not records:
            raise SessionCorruptError(f'Empty session file: {path}')
        first = records[0]
        if first.get('type') != 'session_started':
            raise SessionCorruptError(
                f'Session does not start with session_started: {path}'
            )
        first_payload = self._payload(first, path)
        expected_root = Path(str(first_payload.get('cwd', ''))).resolve()
        if expected_root != self.project_root:
            raise SessionError(
                'The session belongs to a different project directory: '
                f'{expected_root}'
            )

        messages: list[dict[str, Any]] = []
        pending_assistant: dict[str, Any] | None = None
        task: ActiveTask | None = None
        started_tools: dict[str, dict[str, Any]] = {}
        completed_tools: set[str] = set()
        name = optional_string(first_payload.get('name'))
        first_prompt = ''
        status = 'active'

        for record in records[1:]:
            payload = self._payload(record, path)
            event_type = record['type']
            if event_type == 'user_message':
                message = message_from(payload)
                messages.append(message)
                if not first_prompt:
                    first_prompt = message_title(message)
            elif event_type == 'assistant_message':
                message = message_from(payload)
                if assistant_has_tools(message):
                    pending_assistant = message
                else:
                    messages.append(message)
            elif event_type == 'tool_result_message':
                message = message_from(payload)
                if pending_assistant is not None and tool_pair_matches(
                    pending_assistant,
                    message,
                ):
                    messages.extend((pending_assistant, message))
                pending_assistant = None
            elif event_type in {'context_compacted', 'message_checkpoint'}:
                candidate = payload.get('messages')
                if isinstance(candidate, list) and all(
                    isinstance(item, dict) for item in candidate
                ):
                    messages = [dict(item) for item in candidate]
                    if not first_prompt:
                        first_prompt = first_message_title(messages)
                    pending_assistant = None
            elif event_type == 'task_state':
                task_payload = payload.get('task')
                task = (
                    ActiveTask.from_dict(task_payload)
                    if isinstance(task_payload, dict)
                    else None
                )
            elif event_type == 'tool_started':
                tool_id = str(payload.get('tool_call_id', ''))
                if tool_id:
                    started_tools[tool_id] = payload
            elif event_type == 'tool_completed':
                completed_tools.add(str(payload.get('tool_call_id', '')))
            elif event_type == 'session_renamed':
                name = optional_string(payload.get('name'))
            elif event_type == 'turn_completed':
                status = str(payload.get('status', 'completed'))
            elif event_type == 'session_stopped':
                status = 'stopped'
            elif event_type == 'session_resumed':
                status = 'active'
            elif event_type == 'conversation_rewound':
                candidate = payload.get('messages')
                if isinstance(candidate, list) and all(
                    isinstance(item, dict) for item in candidate
                ):
                    messages = [dict(item) for item in candidate]
                    if not first_prompt:
                        first_prompt = first_message_title(messages)
                    pending_assistant = None
                task_payload = payload.get('task')
                task = (
                    ActiveTask.from_dict(task_payload)
                    if isinstance(task_payload, dict)
                    else None
                )

        info = SessionInfo(
            session_id=str(first['session_id']),
            project_root=self.project_root,
            path=path,
            created_at=str(first['timestamp']),
            updated_at=str(records[-1]['timestamp']),
            model=str(first_payload.get('model', '')),
            name=name,
            title=name or first_prompt or str(first['session_id']),
            status=status,
            sequence=int(records[-1]['sequence']),
        )
        return SessionState(
            info=info,
            messages=tuple(messages),
            active_task=task,
            indeterminate_tools=tuple(
                started_tools[key]
                for key in started_tools.keys() - completed_tools
            ),
        )

    def _read_records(self, path: Path) -> list[dict[str, Any]]:
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except OSError as error:
            raise SessionError(f'Cannot read session {path}: {error}') from error
        records: list[dict[str, Any]] = []
        previous_uuid: str | None = None
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                if index == len(lines) - 1:
                    break
                raise SessionCorruptError(
                    f'Invalid JSON at {path}:{index + 1}'
                ) from error
            if not isinstance(record, dict):
                raise SessionCorruptError(
                    f'Invalid event at {path}:{index + 1}'
                )
            expected_sequence = len(records) + 1
            if record.get('sequence') != expected_sequence:
                raise SessionCorruptError(
                    f'Invalid sequence at {path}:{index + 1}'
                )
            if record.get('parent_uuid') != previous_uuid:
                raise SessionCorruptError(
                    f'Broken event chain at {path}:{index + 1}'
                )
            previous_uuid = str(record.get('uuid', ''))
            records.append(record)
        return records

    @staticmethod
    def _payload(record: dict[str, Any], path: Path) -> dict[str, Any]:
        payload = record.get('payload')
        if isinstance(payload, dict):
            return payload
        reference = record.get('payload_ref')
        if not isinstance(reference, dict):
            return {}
        relative = Path(str(reference.get('path', '')))
        if relative.is_absolute() or '..' in relative.parts:
            raise SessionCorruptError('Unsafe session artifact path.')
        artifact = path.parent / relative
        try:
            content = artifact.read_bytes()
        except OSError as error:
            raise SessionCorruptError(
                f'Cannot read session artifact: {artifact}'
            ) from error
        expected_hash = str(reference.get('sha256', ''))
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise SessionCorruptError(
                f'Session artifact hash mismatch: {artifact}'
            )
        decoded = json.loads(content.decode('utf-8'))
        if not isinstance(decoded, dict):
            raise SessionCorruptError(
                f'Invalid session artifact payload: {artifact}'
            )
        return decoded


def project_storage_key(root: Path) -> str:
    normalized = os.path.normcase(str(root.resolve()))
    digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:16]
    slug = re.sub(r'[^a-zA-Z0-9._-]+', '-', root.name).strip('-') or 'project'
    return f'{slug[:48]}-{digest}'


def clean_session_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 100:
        raise ValueError('Session names are limited to 100 characters.')
    return cleaned


def optional_string(value: Any) -> str | None:
    return str(value) if value not in {None, ''} else None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def message_from(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get('message')
    if not isinstance(message, dict):
        raise SessionCorruptError('Session message payload is invalid.')
    return dict(message)


def assistant_has_tools(message: dict[str, Any]) -> bool:
    content = message.get('content')
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get('type') == 'tool_use'
        for block in content
    )


def message_title(message: dict[str, Any]) -> str:
    if message.get('role') != 'user':
        return ''
    content = message.get('content')
    if not isinstance(content, str):
        return ''
    normalized = ' '.join(content.split())
    if not normalized:
        return ''
    return normalized[:72] + ('…' if len(normalized) > 72 else '')


def first_message_title(messages: list[dict[str, Any]]) -> str:
    return next(
        (
            title
            for message in messages
            if (title := message_title(message))
        ),
        '',
    )


def tool_pair_matches(
    assistant: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    assistant_content = assistant.get('content')
    result_content = result.get('content')
    if not isinstance(assistant_content, list) or not isinstance(
        result_content,
        list,
    ):
        return False
    requested = {
        str(block.get('id'))
        for block in assistant_content
        if isinstance(block, dict) and block.get('type') == 'tool_use'
    }
    completed = {
        str(block.get('tool_use_id'))
        for block in result_content
        if isinstance(block, dict) and block.get('type') == 'tool_result'
    }
    return bool(requested) and requested == completed


def event_summary(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == 'user_message':
        message = payload.get('message')
        content = message.get('content') if isinstance(message, dict) else ''
        rendered = str(content).replace('\n', ' ').strip()
        return rendered[:120] + ('…' if len(rendered) > 120 else '')
    if event_type == 'task_state':
        task = payload.get('task')
        if not isinstance(task, dict):
            return 'no active task'
        return '{} [{}]: {}'.format(
            task.get('id', 'task'),
            task.get('status', 'unknown'),
            task.get('goal', ''),
        )
    if event_type == 'turn_completed':
        parts = ['status={}'.format(payload.get('status', 'unknown'))]
        changed = payload.get('changed_paths')
        if isinstance(changed, list) and changed:
            parts.append('changed=' + ','.join(str(item) for item in changed))
        verification = payload.get('verification')
        if isinstance(verification, dict):
            parts.append(
                'verify={} exit={}'.format(
                    verification.get('command', ''),
                    verification.get('exit_code', '?'),
                )
            )
        return '; '.join(parts)
    if event_type in {'checkpoint_created', 'conversation_rewound'}:
        return str(payload.get('checkpoint_id', ''))
    if event_type in {'context_compacted', 'message_checkpoint'}:
        messages = payload.get('messages')
        count = len(messages) if isinstance(messages, list) else 0
        return '{} effective message(s)'.format(count)
    if event_type == 'tool_started':
        return '{} ({})'.format(
            payload.get('name', 'tool'),
            payload.get('tool_call_id', ''),
        )
    if event_type == 'tool_completed':
        return '{} success={}'.format(
            payload.get('name', 'tool'),
            payload.get('success', False),
        )
    if event_type == 'session_forked':
        return 'source={}'.format(payload.get('source_session_id', ''))
    if event_type == 'session_renamed':
        return str(payload.get('name', ''))
    return ''
