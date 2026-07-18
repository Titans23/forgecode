'''Tests for structured full-history compaction.'''

import asyncio
from collections.abc import AsyncIterator
import json
from pathlib import Path
from typing import Any

from forge.context.compactor import CompactionConfig
from forge.context.manager import ContextManager
from forge.runtime.state import (
    ModelStreamEvent,
    ModelTextDelta,
    ModelUsageUpdate,
    TokenUsage,
)


class SummaryClient:
    provider = 'fake'

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.calls.append({'messages': messages, 'tools': tools, 'system': system})
        yield ModelTextDelta(text=self.text)
        yield ModelUsageUpdate(usage=TokenUsage(100, 20))


def valid_summary() -> str:
    return json.dumps(
        {
            'goal': 'fix calculator',
            'constraints': ['do not read hidden tests'],
            'findings': ['divide returns zero'],
            'modified_files': ['src/calculator/core.py'],
            'failed_attempts': ['none'],
            'verification': ['public tests pass'],
            'open_questions': [],
            'next_action': 'inspect diff',
        }
    )


def test_forced_compaction_preserves_structured_state_and_transcript(
    tmp_path: Path,
) -> None:
    messages = [
        {'role': 'user', 'content': 'fix divide without hidden tests'},
        {'role': 'assistant', 'content': 'working'},
        {'role': 'user', 'content': 'latest evidence'},
    ]
    manager = ContextManager(messages, tmp_path)
    client = SummaryClient(valid_summary())

    report = asyncio.run(
        manager.compact_history(messages, client, force=True)
    )

    assert report is not None and report.success
    assert report.transcript_path is not None
    assert Path(report.transcript_path).exists()
    assert 'fix calculator' in str(messages[0]['content'])
    assert 'do not read hidden tests' in str(messages[0]['content'])
    assert messages[-1]['content'] == 'latest evidence'
    assert client.calls[0]['tools'] is None


def test_auto_compaction_only_runs_above_threshold(tmp_path: Path) -> None:
    messages = [{'role': 'user', 'content': 'x' * 100}]
    manager = ContextManager(
        messages,
        tmp_path,
        CompactionConfig(auto_compact_characters=10),
    )
    client = SummaryClient(valid_summary())

    report = asyncio.run(manager.compact_history(messages, client))

    assert report is not None and report.automatic
    assert len(client.calls) == 1


def test_configured_window_replaces_character_fallback(tmp_path: Path) -> None:
    messages = [{'role': 'user', 'content': 'x' * 100}]
    manager = ContextManager(
        messages,
        tmp_path,
        CompactionConfig(auto_compact_characters=10),
    )
    client = SummaryClient(valid_summary())

    report = asyncio.run(
        manager.compact_history(
            messages,
            client,
            context_window_tokens=1_000,
            reserved_output_tokens=100,
        )
    )

    assert report is None
    assert client.calls == []


def test_auto_compaction_counts_full_request_at_eighty_percent(
    tmp_path: Path,
) -> None:
    messages = [{'role': 'user', 'content': 'h' * 400}]
    manager = ContextManager(messages, tmp_path)
    client = SummaryClient(valid_summary())

    report = asyncio.run(
        manager.compact_history(
            messages,
            client,
            system_prompt='s' * 800,
            repository_context='r' * 400,
            context_window_tokens=1_000,
            reserved_output_tokens=400,
        )
    )

    assert report is not None and report.automatic
    assert len(client.calls) == 1


def test_summary_failures_open_fuse_after_three_attempts(
    tmp_path: Path,
) -> None:
    messages = [{'role': 'user', 'content': 'long history'}]
    manager = ContextManager(
        messages,
        tmp_path,
        CompactionConfig(max_summary_failures=3),
    )
    client = SummaryClient('not json')

    reports = [
        asyncio.run(manager.compact_history(messages, client, force=True))
        for _ in range(4)
    ]

    assert all(report is not None and not report.success for report in reports)
    assert len(client.calls) == 3
    assert reports[-1].reason == 'summary failure fuse is open'
