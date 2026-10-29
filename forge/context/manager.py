'''Conversation context accounting and orchestration.'''

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from forge.context.compactor import (
    CheapCompactionResult,
    CompactionConfig,
    cheap_compact,
    persist_large_tool_results,
    summarize_history,
    atomic_message_units,
)
from forge.context.repository import MemoryRecord, RepositoryContext
from forge.runtime.state import TokenUsage
from forge.runtime.turn_state import BudgetExhausted, add_usage


@dataclass(frozen=True, slots=True)
class ContextStats:
    '''A provider-neutral snapshot of model-visible conversation context.'''

    message_count: int
    estimated_characters: int
    tool_result_characters: int
    system_characters: int = 0
    repository_characters: int = 0
    tool_schema_characters: int = 0
    context_window_tokens: int | None = None
    reserved_output_tokens: int = 0
    stored_message_count: int | None = None
    stored_estimated_characters: int | None = None
    stored_tool_result_characters: int | None = None

    @property
    def estimated_tokens(self) -> int:
        '''Estimate all model-visible input without one provider tokenizer.'''
        return estimate_tokens(
            self.estimated_characters
            + self.system_characters
            + self.repository_characters
            + self.tool_schema_characters
        )

    @property
    def history_tokens(self) -> int:
        return estimate_tokens(self.estimated_characters)

    @property
    def system_tokens(self) -> int:
        return estimate_tokens(self.system_characters)

    @property
    def repository_tokens(self) -> int:
        return estimate_tokens(self.repository_characters)

    @property
    def tool_schema_tokens(self) -> int:
        return estimate_tokens(self.tool_schema_characters)

    @property
    def remaining_tokens(self) -> int | None:
        if self.context_window_tokens is None:
            return None
        return max(
            0,
            self.context_window_tokens
            - self.projected_tokens,
        )

    @property
    def projected_tokens(self) -> int:
        return self.estimated_tokens + self.reserved_output_tokens

    @property
    def utilization(self) -> float | None:
        if not self.context_window_tokens:
            return None
        return self.projected_tokens / self.context_window_tokens

    @property
    def stored_messages(self) -> int:
        return (
            self.message_count
            if self.stored_message_count is None
            else self.stored_message_count
        )

    @property
    def stored_characters(self) -> int:
        return (
            self.estimated_characters
            if self.stored_estimated_characters is None
            else self.stored_estimated_characters
        )

    @property
    def stored_tool_characters(self) -> int:
        return (
            self.tool_result_characters
            if self.stored_tool_result_characters is None
            else self.stored_tool_result_characters
        )

    @property
    def stored_tokens(self) -> int:
        return estimate_tokens(self.stored_characters)


def context_stats(
    messages: list[dict[str, Any]],
    *,
    stored_messages: list[dict[str, Any]] | None = None,
    system_prompt: str = '',
    repository_context: str = '',
    tools: list[dict[str, Any]] | None = None,
    context_window_tokens: int | None = None,
    reserved_output_tokens: int = 0,
) -> ContextStats:
    '''Measure serialized history and tool-result payloads deterministically.'''
    total, tool_results = measure_messages(messages)
    stored_total, stored_tool_results = measure_messages(
        messages if stored_messages is None else stored_messages
    )
    return ContextStats(
        message_count=len(messages),
        estimated_characters=total,
        tool_result_characters=tool_results,
        system_characters=len(system_prompt),
        repository_characters=len(repository_context),
        tool_schema_characters=(
            len(json.dumps(tools, ensure_ascii=False, default=str))
            if tools
            else 0
        ),
        context_window_tokens=context_window_tokens,
        reserved_output_tokens=reserved_output_tokens,
        stored_message_count=(
            len(messages) if stored_messages is None else len(stored_messages)
        ),
        stored_estimated_characters=stored_total,
        stored_tool_result_characters=stored_tool_results,
    )


def measure_messages(
    messages: list[dict[str, Any]],
) -> tuple[int, int]:
    '''Return serialized characters and raw tool-result characters.'''
    from forge.context.compactor import describe_images
    messages = describe_images(messages, budget=True)
    total = 0
    tool_results = 0
    for message in messages:
        total += len(json.dumps(message, ensure_ascii=False, default=str))
        content = message.get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') != 'tool_result':
                continue
            tool_results += len(str(block.get('content', '')))
    return total, tool_results


def estimate_tokens(characters: int) -> int:
    if characters <= 0:
        return 0
    return max(1, (characters + 3) // 4)


def request_output_tokens(stats: ContextStats) -> int:
    '''Fit the configured output ceiling to the estimated input, leaving 1% framing margin.'''
    if stats.context_window_tokens is None:
        return stats.reserved_output_tokens
    available = int(stats.context_window_tokens * 0.99) - stats.estimated_tokens
    return min(stats.reserved_output_tokens, max(0, available))


class ContextManager:
    '''Own context inspection while compaction policies evolve independently.'''

    def __init__(
        self,
        messages: list[dict[str, Any]],
        root: Path | None = None,
        config: CompactionConfig | None = None,
    ) -> None:
        self._messages = messages
        self.root = (root or Path.cwd()).resolve()
        self.config = config or CompactionConfig()
        self.last_compaction: CheapCompactionResult | None = None
        self.summary_failures = 0
        self.last_report: CompactionReport | None = None
        self.repository = RepositoryContext(self.root)

    def reset_history(self) -> None:
        '''Discard transient observations when committed history is replaced.'''
        self.summary_failures = 0
        self.last_compaction = None
        self.last_report = None
        self.repository.reset_scopes()

    @property
    def stats(self) -> ContextStats:
        return context_stats(self._messages)

    def stats_for_request(
        self,
        *,
        system_prompt: str,
        repository_context: str,
        tools: list[dict[str, Any]] | None,
        context_window_tokens: int | None,
        reserved_output_tokens: int,
    ) -> ContextStats:
        prepared = self.prepare(self._messages)
        stats = context_stats(
            prepared,
            stored_messages=self._messages,
            system_prompt=system_prompt,
            repository_context=repository_context,
            tools=tools,
            context_window_tokens=context_window_tokens,
            reserved_output_tokens=reserved_output_tokens,
        )
        return replace(stats, reserved_output_tokens=request_output_tokens(stats))

    def prepare(
        self,
        messages: list[dict[str, Any]],
        *,
        scope_hints: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        '''Return a cheap-compacted request copy for the model.'''
        artifact_dir = self.root / '.forge' / 'context' / 'tool-results'
        self.last_compaction = cheap_compact(
            messages,
            artifact_dir,
            self.config,
            scope_hints=scope_hints,
        )
        prepared = self.last_compaction.messages
        if self.last_compaction.removed_messages:
            retained = set(original_user_instructions(prepared))
            missing = [text for text in original_user_instructions(messages) if text and text not in retained]
            if missing:
                prepared.insert(0, {'role': 'user', 'content':
                    '[ForgeCode original user instructions]\n' + json.dumps(missing, ensure_ascii=False)})
        return prepared

    def persist_tool_result_message(
        self,
        message: dict[str, Any],
    ) -> tuple[str, ...]:
        '''Bound large tool output before it enters durable conversation history.'''
        artifact_dir = self.root / '.forge' / 'context' / 'tool-results'
        return tuple(
            persist_large_tool_results(
                [message],
                artifact_dir,
                self.config,
            )
        )

    def build_system_prompt(self, base: str, query: str) -> str:
        '''Inject stable rules and only query-relevant durable memories.'''
        suffix = self.repository.system_suffix(query)
        return base if not suffix else f'{base}\n\n{suffix}'

    def remember(
        self,
        name: str,
        content: str,
        *,
        description: str = '',
        memory_type: str = 'project',
    ) -> MemoryRecord:
        return self.repository.memory.remember(
            name,
            content,
            description=description,
            memory_type=memory_type,
        )

    def capture_explicit_memory(self, prompt: str) -> MemoryRecord | None:
        '''Persist only user-authored explicit remember directives.'''
        match = re.search(
            r'(?:记住|remember)\s*[:：]\s*(.+)',
            prompt,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match is None:
            return None
        content = match.group(1).strip()
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()[:10]
        try:
            return self.remember(
                f'user-{digest}',
                content,
                description='Explicit user memory',
                memory_type='user',
            )
        except ValueError:
            return None

    async def compact_history(
        self,
        messages: list[dict[str, Any]],
        client: Any,
        *,
        force: bool = False,
        system_prompt: str = '',
        repository_context: str = '',
        tools: list[dict[str, Any]] | None = None,
        context_window_tokens: int | None = None,
        reserved_output_tokens: int = 0,
        scope_hints: tuple[str, ...] = (),
        task_goal: str = '',
    ) -> CompactionReport | None:
        '''Replace long history with a structured summary when required.'''
        prepared = self.prepare(messages, scope_hints=scope_hints)
        before_stats = context_stats(
            prepared,
            stored_messages=messages,
            system_prompt=system_prompt,
            repository_context=repository_context,
            tools=tools,
            context_window_tokens=context_window_tokens,
            reserved_output_tokens=reserved_output_tokens,
        )
        threshold_reached = not force and self._threshold_reached(
            before_stats,
            context_window_tokens=context_window_tokens,
        )
        should_compact = force or threshold_reached
        if not should_compact:
            return None
        if self.summary_failures >= self.config.max_summary_failures:
            return CompactionReport(
                success=False,
                automatic=not force,
                before_characters=before_stats.estimated_characters,
                after_characters=before_stats.estimated_characters,
                transcript_path=None,
                reason='summary failure fuse is open',
            )
        if not force and context_window_tokens is not None:
            envelope = (before_stats.system_tokens + before_stats.repository_tokens
                        + before_stats.tool_schema_tokens)
            if envelope > self.config.pressure_threshold(context_window_tokens):
                return CompactionReport(
                    success=False, automatic=True,
                    before_characters=before_stats.estimated_characters,
                    after_characters=before_stats.estimated_characters,
                    transcript_path=None,
                    reason='Fixed request instructions and tools exceed the compaction threshold; history summarization cannot reduce them.',
                )
        transcript_path = self.persist_transcript(messages)
        instructions = original_user_instructions(messages)
        # Prune first, then summarize only the prefix outside the priced recent tail.
        # Full originals remain in the transcript/tool artifacts and Journal.
        working = prepared
        retain_tokens = 0 if force else self.config.keep_recent_tokens
        usage: TokenUsage | None = None
        reason = ''
        after = before_stats
        for _ in range(1 if force else self.config.compaction_retries + 1):
            units = atomic_message_units(working)
            cutoff = len(units)
            retained_tokens = 0
            for unit in reversed(units):
                cutoff -= 1
                retained_tokens += estimate_tokens(measure_messages(unit)[0])
                if retained_tokens >= retain_tokens:
                    break
            prefix = [message for unit in units[:cutoff] for message in unit]
            tail = [message for unit in units[cutoff:] for message in unit]
            if not prefix and not force:
                reason = 'No safe compaction range outside the retained tail; original history retained.'
                break
            try:
                result = await summarize_history(
                    client, prefix or working, retained_messages=tail,
                    scope_hints=scope_hints, task_goal=task_goal,
                )
            except BudgetExhausted:
                raise
            except Exception as error:
                reason = str(error)
                break
            usage = result.usage if usage is None else add_usage(usage, result.usage)
            compacted = result.messages
            if instructions:
                compacted.insert(1, {
                    'role': 'user',
                    'content': '[ForgeCode original user instructions]\n'
                    + json.dumps(instructions, ensure_ascii=False),
                })
            after = context_stats(
                self.prepare(compacted, scope_hints=scope_hints),
                system_prompt=system_prompt, repository_context=repository_context, tools=tools,
            )
            if not force and after.estimated_characters >= context_stats(working).estimated_characters:
                reason = 'Summary did not reduce the model-visible history; original history retained.'
                break
            if force or not self._threshold_reached(
                after, context_window_tokens=context_window_tokens,
            ):
                messages[:] = compacted
                self.summary_failures = 0
                self.last_report = CompactionReport(
                    success=True, automatic=not force,
                    before_characters=before_stats.estimated_characters,
                    after_characters=after.estimated_characters,
                    transcript_path=transcript_path, usage=usage,
                )
                return self.last_report
            working = compacted
            reason = 'Compaction still above threshold after bounded summary attempts; original history retained.'
        self.summary_failures += 1
        return CompactionReport(
            success=False, automatic=not force,
            before_characters=before_stats.estimated_characters,
            after_characters=after.estimated_characters,
            transcript_path=transcript_path, usage=usage, reason=reason,
        )

    def read_artifact(
        self,
        artifact_id: str,
        *,
        max_characters: int = 20_000,
        offset: int | None = None,
    ) -> str:
        '''Read a stored tool result through a constrained artifact interface.'''
        if max_characters < 1:
            raise ValueError('max_characters must be positive')
        if offset is not None and offset < 0:
            raise ValueError('offset must be non-negative')
        candidate_id = Path(artifact_id).name
        if candidate_id != artifact_id or not re.fullmatch(
            r'[0-9a-f]{64}(?:\.txt)?',
            candidate_id,
            flags=re.IGNORECASE,
        ):
            raise ValueError('Invalid tool-result artifact id.')
        filename = (
            candidate_id if candidate_id.endswith('.txt') else candidate_id + '.txt'
        )
        directory = (self.root / '.forge' / 'context' / 'tool-results').resolve()
        path = (directory / filename).resolve(strict=False)
        try:
            path.relative_to(directory)
        except ValueError as error:
            raise ValueError('Artifact path is outside the context store.') from error
        try:
            content = path.read_text(encoding='utf-8', errors='replace')
        except OSError as error:
            raise FileNotFoundError(f'Unknown tool-result artifact: {artifact_id}') from error
        if offset is not None:
            return content[offset:offset + max_characters]
        if len(content) <= max_characters:
            return content
        tail = max(1, max_characters // 3)
        head = max(1, max_characters - tail)
        return (
            content[:head]
            + f'\n\n[artifact truncated: {len(content) - max_characters} characters]\n\n'
            + content[-tail:]
        )
    def compaction_required(
        self,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str = '',
        repository_context: str = '',
        tools: list[dict[str, Any]] | None = None,
        context_window_tokens: int | None = None,
        reserved_output_tokens: int = 0,
        scope_hints: tuple[str, ...] = (),
    ) -> bool:
        '''Return whether automatic compaction would run for this request.'''
        prepared = self.prepare(messages, scope_hints=scope_hints)
        stats = context_stats(
            prepared,
            stored_messages=messages,
            system_prompt=system_prompt,
            repository_context=repository_context,
            tools=tools,
            context_window_tokens=context_window_tokens,
            reserved_output_tokens=reserved_output_tokens,
        )
        return self._threshold_reached(
            stats,
            context_window_tokens=context_window_tokens,
        )

    def _threshold_reached(
        self,
        stats: ContextStats,
        *,
        context_window_tokens: int | None,
    ) -> bool:
        if context_window_tokens is None:
            return stats.estimated_characters > self.config.auto_compact_characters
        return stats.estimated_tokens > self.config.pressure_threshold(context_window_tokens)

    def persist_transcript(
        self,
        messages: list[dict[str, Any]],
    ) -> str:
        '''Save the exact pre-summary history as JSONL for recovery.'''
        serialized = '\n'.join(
            json.dumps(message, ensure_ascii=False, default=str)
            for message in messages
        )
        digest = hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:16]
        directory = self.root / '.forge' / 'context' / 'transcripts'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{digest}.jsonl'
        path.write_text(serialized + '\n', encoding='utf-8')
        return path.as_posix()


def original_user_instructions(messages: list[dict[str, Any]]) -> list[str]:
    '''Preserve exact user wording across repeated model summaries and resumes.'''
    collected: list[str] = []
    prefix = '[ForgeCode original user instructions]\n'
    for message in messages:
        content = message.get('content')
        if isinstance(content, list):
            content = '\n'.join(block['text'] for block in content
                                if isinstance(block, dict) and block.get('type') == 'text')
        if message.get('role') != 'user' or not isinstance(content, str):
            continue
        if content.startswith(prefix):
            try:
                prior = json.loads(content[len(prefix):])
                if isinstance(prior, list):
                    collected.extend(item for item in prior if isinstance(item, str))
            except json.JSONDecodeError:
                collected.append(content)
        elif not content.startswith(('[ForgeCode ', '[Runtime feedback]')):
            collected.append(content)
    return list(dict.fromkeys(collected))


@dataclass(frozen=True, slots=True)
class CompactionReport:
    success: bool
    automatic: bool
    before_characters: int
    after_characters: int
    transcript_path: str | None
    reason: str = ''
    usage: TokenUsage | None = None
