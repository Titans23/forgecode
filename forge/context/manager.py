'''Conversation context accounting and orchestration.'''

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from forge.context.compactor import (
    CheapCompactionResult,
    CompactionConfig,
    cheap_compact,
    summarize_history,
)
from forge.context.repository import MemoryRecord, RepositoryContext


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
            - self.estimated_tokens
            - self.reserved_output_tokens,
        )

    @property
    def utilization(self) -> float | None:
        if not self.context_window_tokens:
            return None
        return self.estimated_tokens / self.context_window_tokens


def context_stats(
    messages: list[dict[str, Any]],
    *,
    system_prompt: str = '',
    repository_context: str = '',
    tools: list[dict[str, Any]] | None = None,
    context_window_tokens: int | None = None,
    reserved_output_tokens: int = 0,
) -> ContextStats:
    '''Measure serialized history and tool-result payloads deterministically.'''
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
    )


def estimate_tokens(characters: int) -> int:
    if characters <= 0:
        return 0
    return max(1, (characters + 3) // 4)


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
        return context_stats(
            self._messages,
            system_prompt=system_prompt,
            repository_context=repository_context,
            tools=tools,
            context_window_tokens=context_window_tokens,
            reserved_output_tokens=reserved_output_tokens,
        )

    def prepare(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        '''Return a cheap-compacted request copy for the model.'''
        artifact_dir = self.root / '.forge' / 'context' / 'tool-results'
        self.last_compaction = cheap_compact(
            messages,
            artifact_dir,
            self.config,
        )
        return self.last_compaction.messages

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
    ) -> CompactionReport | None:
        '''Replace long history with a structured summary when required.'''
        prepared = self.prepare(messages)
        before_stats = context_stats(
            prepared,
            system_prompt=system_prompt,
            repository_context=repository_context,
            tools=tools,
            context_window_tokens=context_window_tokens,
            reserved_output_tokens=reserved_output_tokens,
        )
        if context_window_tokens is None:
            threshold_reached = (
                before_stats.estimated_characters
                > self.config.auto_compact_characters
            )
        else:
            projected_tokens = (
                before_stats.estimated_tokens + reserved_output_tokens
            )
            threshold_reached = (
                projected_tokens
                >= context_window_tokens * self.config.auto_compact_ratio
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
        transcript_path = self.persist_transcript(messages)
        try:
            result = await summarize_history(
                client,
                prepared,
                keep_recent_messages=self.config.summary_keep_recent_messages,
            )
        except Exception as error:
            self.summary_failures += 1
            return CompactionReport(
                success=False,
                automatic=not force,
                before_characters=before_stats.estimated_characters,
                after_characters=before_stats.estimated_characters,
                transcript_path=transcript_path,
                reason=str(error),
            )
        messages[:] = result.messages
        self.summary_failures = 0
        after = context_stats(messages)
        self.last_report = CompactionReport(
            success=True,
            automatic=not force,
            before_characters=before_stats.estimated_characters,
            after_characters=after.estimated_characters,
            transcript_path=transcript_path,
        )
        return self.last_report

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


@dataclass(frozen=True, slots=True)
class CompactionReport:
    success: bool
    automatic: bool
    before_characters: int
    after_characters: int
    transcript_path: str | None
    reason: str = ''
