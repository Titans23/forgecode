'''Deterministic, zero-model-cost conversation compaction.'''

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path
from typing import Any

from forge.runtime.state import (
    ModelTextDelta,
    ModelToolCallCompleted,
    ModelUsageUpdate,
    TokenUsage,
)


COMPACTABLE_TOOL_NAMES = frozenset(
    {
        'read_file',
        'list_directory',
        'grep',
        'find_files',
        'run_command',
        'verify',
        'git_status',
        'git_diff',
        'write_file',
        'write_file_chunk',
        'replace_text',
        'apply_patch',
        'create_directory',
    }
)


@dataclass(frozen=True, slots=True)
class CompactionConfig:
    '''Limits for cheap and full-history context compaction.'''

    tool_result_total_budget: int = 200_000
    tool_result_inline_limit: int = 30_000
    keep_recent_tool_results: int = 3
    old_tool_result_limit: int = 120
    message_limit: int = 12
    keep_first_messages: int = 0
    keep_recent_messages: int = 12
    keep_file_evidence_units: int = 4
    file_evidence_character_budget: int = 100_000
    auto_compact_characters: int = 120_000
    auto_compact_ratio: float = 0.8
    summary_keep_recent_messages: int = 6
    post_compact_max_files: int = 5
    post_compact_file_budget: int = 100_000
    max_summary_failures: int = 3


@dataclass(frozen=True, slots=True)
class CheapCompactionResult:
    messages: list[dict[str, Any]]
    artifacts: tuple[str, ...] = ()
    removed_messages: int = 0
    shortened_tool_results: int = 0


@dataclass(frozen=True, slots=True)
class TaskSummary:
    '''Structured state that must survive full-history compaction.'''

    goal: str
    constraints: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()
    modified_files: tuple[str, ...] = ()
    failed_attempts: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    next_action: str = ''

    @classmethod
    def from_json(cls, text: str) -> TaskSummary:
        payload = extract_json_object(text)
        data = json.loads(payload)
        if not isinstance(data, dict) or not str(data.get('goal', '')).strip():
            raise ValueError('Summary JSON must contain a non-empty goal.')

        def strings(name: str) -> tuple[str, ...]:
            value = data.get(name, [])
            if not isinstance(value, list):
                raise ValueError(f'Summary field {name} must be a list.')
            return tuple(str(item) for item in value if str(item).strip())

        return cls(
            goal=str(data['goal']).strip(),
            constraints=strings('constraints'),
            findings=strings('findings'),
            modified_files=strings('modified_files'),
            failed_attempts=strings('failed_attempts'),
            verification=strings('verification'),
            open_questions=strings('open_questions'),
            next_action=str(data.get('next_action', '')).strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            'goal': self.goal,
            'constraints': list(self.constraints),
            'findings': list(self.findings),
            'modified_files': list(self.modified_files),
            'failed_attempts': list(self.failed_attempts),
            'verification': list(self.verification),
            'open_questions': list(self.open_questions),
            'next_action': self.next_action,
        }


@dataclass(frozen=True, slots=True)
class FullCompactionResult:
    messages: list[dict[str, Any]]
    summary: TaskSummary
    usage: TokenUsage


async def summarize_history(
    client: Any,
    messages: list[dict[str, Any]],
    *,
    keep_recent_messages: int = 6,
    scope_hints: tuple[str, ...] = (),
    task_goal: str = '',
    restore_max_files: int = 5,
    restore_character_budget: int = 100_000,
    restoration_messages: list[dict[str, Any]] | None = None,
) -> FullCompactionResult:
    '''Ask the configured model only for a structured continuity summary.'''
    transcript = json.dumps(messages, ensure_ascii=False, default=str)
    prompt = (
        'Summarize this ForgeCode task history as one JSON object. '
        'Return JSON only, with fields goal, constraints, findings, '
        'modified_files, failed_attempts, verification, open_questions, '
        'and next_action. Every field except goal and next_action is a list '
        'of strings. Preserve user restrictions and failed approaches.\n\n'
        f'AUTHORITATIVE ACTIVE GOAL:\n{task_goal or "not provided"}\n'
        f'AUTHORITATIVE WRITE SCOPE:\n'
        f'{json.dumps(scope_hints, ensure_ascii=False)}\n\n'
        f'HISTORY:\n{transcript}'
    )
    text_parts: list[str] = []
    usage: TokenUsage | None = None
    async for event in client.stream(
        messages=[{'role': 'user', 'content': prompt}],
        tools=None,
        system=(
            'You compress coding-agent history. Do not call tools. '
            'Do not invent facts. Return valid JSON only.'
        ),
    ):
        if isinstance(event, ModelTextDelta):
            text_parts.append(event.text)
        elif isinstance(event, ModelUsageUpdate):
            usage = event.usage
        elif isinstance(event, ModelToolCallCompleted):
            raise ValueError('Summary request unexpectedly called a tool.')
    if usage is None:
        raise ValueError('Summary response did not contain token usage.')
    summary = TaskSummary.from_json(''.join(text_parts))
    if task_goal:
        scope_constraints = tuple(
            f'Active write scope: {scope}' for scope in scope_hints
        )
        summary = TaskSummary(
            goal=task_goal,
            constraints=tuple(
                dict.fromkeys((*summary.constraints, *scope_constraints))
            ),
            findings=summary.findings,
            modified_files=summary.modified_files,
            failed_attempts=summary.failed_attempts,
            verification=summary.verification,
            open_questions=summary.open_questions,
            next_action=summary.next_action,
        )
    units = atomic_message_units(restoration_messages or messages)
    recent_units = take_units_from_end(units, keep_recent_messages)
    recent_ids = {id(unit) for unit in recent_units}
    restored_units = select_file_evidence_units(
        units,
        excluded_ids=recent_ids,
        maximum=restore_max_files,
        character_budget=restore_character_budget,
        scope_hints=scope_hints,
    )
    summary_text = json.dumps(
        summary.as_dict(),
        ensure_ascii=False,
        indent=2,
    )
    compacted = [
        {
            'role': 'user',
            'content': (
                '[ForgeCode structured task summary]\n' + summary_text
            ),
        },
        {
            'role': 'assistant',
            'content': 'I will continue from the structured task summary.',
        },
        *(
            [
                {
                    'role': 'user',
                    'content': (
                        '[ForgeCode post-compact file restoration: recent '
                        'task-scoped file evidence follows.]'
                    ),
                }
            ]
            if restored_units
            else []
        ),
        *(message for unit in restored_units for message in unit),
        *(message for unit in recent_units for message in unit),
    ]
    return FullCompactionResult(compacted, summary, usage)


def extract_json_object(text: str) -> str:
    '''Extract one JSON object from plain or fenced model output.'''
    start = text.find('{')
    end = text.rfind('}')
    if start < 0 or end < start:
        raise ValueError('Summary response did not contain a JSON object.')
    return text[start:end + 1]


def cheap_compact(
    messages: list[dict[str, Any]],
    artifact_dir: Path,
    config: CompactionConfig | None = None,
    *,
    scope_hints: tuple[str, ...] = (),
) -> CheapCompactionResult:
    '''Apply cheap compaction without mutating committed conversation history.'''
    resolved = config or CompactionConfig()
    compacted = deepcopy(messages)
    artifacts = persist_large_tool_results(
        compacted,
        artifact_dir,
        resolved,
    )
    before = len(compacted)
    compacted = snip_middle_messages(
        compacted,
        resolved,
        scope_hints=scope_hints,
    )
    removed = before - len(compacted)
    shortened = shorten_old_tool_results(compacted, resolved)
    return CheapCompactionResult(
        messages=compacted,
        artifacts=tuple(artifacts),
        removed_messages=max(0, removed),
        shortened_tool_results=shortened,
    )


def persist_large_tool_results(
    messages: list[dict[str, Any]],
    artifact_dir: Path,
    config: CompactionConfig,
) -> list[str]:
    '''Persist oversized outputs and replace them with bounded references.'''
    blocks = list(iter_tool_result_blocks(messages))
    total = sum(len(str(block.get('content', ''))) for block in blocks)
    candidates = [
        block
        for block in blocks
        if len(str(block.get('content', '')))
        > config.tool_result_inline_limit
    ]
    if total > config.tool_result_total_budget:
        candidates = sorted(
            blocks,
            key=lambda block: len(str(block.get('content', ''))),
            reverse=True,
        )

    written: list[str] = []
    for block in candidates:
        content = str(block.get('content', ''))
        if (
            len(content) <= config.tool_result_inline_limit
            and total <= config.tool_result_total_budget
        ):
            continue
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f'{digest}.txt'
        if not path.exists():
            path.write_text(content, encoding='utf-8')
        relative = path.as_posix()
        preview = content[:2_000]
        block['content'] = (
            '[ForgeCode stored a large tool result]\n'
            f'path: {relative}\nsha256: {digest}\n'
            f'characters: {len(content)}\npreview:\n{preview}'
        )
        written.append(relative)
        total -= max(0, len(content) - len(str(block['content'])))
    return written


def snip_middle_messages(
    messages: list[dict[str, Any]],
    config: CompactionConfig,
    *,
    scope_hints: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    '''Remove middle history while treating tool-use/result pairs atomically.'''
    if len(messages) <= config.message_limit:
        return messages
    units = atomic_message_units(messages)
    first = take_units_from_start(units, config.keep_first_messages)
    recent = take_units_from_end(units, config.keep_recent_messages)
    first_ids = {id(unit) for unit in first}
    recent = [unit for unit in recent if id(unit) not in first_ids]
    selected_ids = first_ids | {id(unit) for unit in recent}
    evidence = select_file_evidence_units(
        units,
        excluded_ids=selected_ids,
        maximum=config.keep_file_evidence_units,
        character_budget=config.file_evidence_character_budget,
        scope_hints=scope_hints,
    )
    selected_ids.update(id(unit) for unit in evidence)
    selected = [unit for unit in units if id(unit) in selected_ids]
    kept_count = sum(len(unit) for unit in selected)
    removed = len(messages) - kept_count
    if removed <= 0:
        return messages
    marker = {
        'role': 'user',
        'content': f'[ForgeCode omitted {removed} middle messages.]',
    }
    prefix_ids = {id(unit) for unit in first}
    suffix = [unit for unit in selected if id(unit) not in prefix_ids]
    return [
        *(message for unit in first for message in unit),
        marker,
        *(message for unit in suffix for message in unit),
    ]


def select_file_evidence_units(
    units: list[list[dict[str, Any]]],
    *,
    excluded_ids: set[int],
    maximum: int,
    character_budget: int,
    scope_hints: tuple[str, ...] = (),
) -> list[list[dict[str, Any]]]:
    '''Keep recent distinct read_file evidence outside the message window.'''
    if maximum <= 0 or character_budget <= 0:
        return []
    selected: list[list[dict[str, Any]]] = []
    covered_paths: set[str] = set()
    characters = 0
    for unit in reversed(units):
        if id(unit) in excluded_ids:
            covered_paths.update(file_read_paths(unit))
            continue
        paths = file_read_paths(unit)
        if scope_hints and paths and not all(
            any(scope_path_matches(path, hint) for hint in scope_hints)
            for path in paths
        ):
            continue
        if not paths or not (paths - covered_paths):
            continue
        unit_characters = len(
            json.dumps(unit, ensure_ascii=False, default=str)
        )
        if characters + unit_characters > character_budget:
            continue
        selected.append(unit)
        covered_paths.update(paths)
        characters += unit_characters
        if len(selected) >= maximum:
            break
    selected.reverse()
    return selected


def scope_path_matches(path: str, pattern: str) -> bool:
    candidate = path.replace('\\', '/').strip('/')
    normalized = pattern.replace('\\', '/').strip()
    if normalized.endswith('/**'):
        prefix = normalized[:-3].rstrip('/')
        return candidate == prefix or candidate.startswith(prefix + '/')
    return fnmatchcase(candidate, normalized)


def file_read_paths(unit: list[dict[str, Any]]) -> set[str]:
    calls: dict[str, str] = {}
    successful_results: set[str] = set()
    for message in unit:
        content = message.get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get('type') == 'tool_use'
                and block.get('name') == 'read_file'
            ):
                arguments = block.get('input')
                if isinstance(arguments, dict):
                    path = str(arguments.get('path', '')).strip()
                    if path:
                        calls[str(block.get('id', ''))] = path.replace(
                            '\\',
                            '/',
                        )
            elif (
                isinstance(block, dict)
                and block.get('type') == 'tool_result'
                and block.get('is_error') is not True
            ):
                successful_results.add(str(block.get('tool_use_id', '')))
    return {
        path
        for call_id, path in calls.items()
        if call_id and call_id in successful_results
    }


def shorten_old_tool_results(
    messages: list[dict[str, Any]],
    config: CompactionConfig,
) -> int:
    '''Clear old replayable tool results while preserving durable task outputs.'''
    tool_names: dict[str, str] = {}
    result_blocks: list[dict[str, Any]] = []
    for message in messages:
        content = message.get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                tool_names[str(block.get('id', ''))] = str(
                    block.get('name', '')
                )
            elif block.get('type') == 'tool_result':
                call_id = str(block.get('tool_use_id', ''))
                if tool_names.get(call_id) in COMPACTABLE_TOOL_NAMES:
                    result_blocks.append(block)
    keep_recent = max(1, config.keep_recent_tool_results)
    old_blocks = result_blocks[:-keep_recent]
    shortened = 0
    for block in old_blocks:
        content = str(block.get('content', ''))
        if len(content) <= config.old_tool_result_limit:
            continue
        if content.startswith('[ForgeCode stored a large tool result]'):
            continue
        block['content'] = (
            '[Old replayable tool result content cleared; '
            f'original characters: {len(content)}]'
        )
        shortened += 1
    return shortened


def iter_tool_result_blocks(
    messages: list[dict[str, Any]],
):
    for message in messages:
        content = message.get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'tool_result':
                yield block


def atomic_message_units(
    messages: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    '''Group each assistant tool-use message with its following result message.'''
    units: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if (
            has_block_type(message, 'tool_use')
            and index + 1 < len(messages)
            and has_block_type(messages[index + 1], 'tool_result')
        ):
            units.append([message, messages[index + 1]])
            index += 2
            continue
        units.append([message])
        index += 1
    return units


def has_block_type(message: dict[str, Any], block_type: str) -> bool:
    content = message.get('content')
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get('type') == block_type
        for block in content
    )


def take_units_from_start(
    units: list[list[dict[str, Any]]],
    message_budget: int,
) -> list[list[dict[str, Any]]]:
    if message_budget <= 0:
        return []
    selected: list[list[dict[str, Any]]] = []
    count = 0
    for unit in units:
        if selected and count >= message_budget:
            break
        selected.append(unit)
        count += len(unit)
    return selected


def take_units_from_end(
    units: list[list[dict[str, Any]]],
    message_budget: int,
) -> list[list[dict[str, Any]]]:
    if message_budget <= 0:
        return []
    selected: list[list[dict[str, Any]]] = []
    count = 0
    for unit in reversed(units):
        if selected and count >= message_budget:
            break
        selected.append(unit)
        count += len(unit)
    selected.reverse()
    return selected
