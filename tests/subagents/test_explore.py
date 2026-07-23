'''Tests for the bounded read-only Explore Agent.'''

import asyncio
from collections.abc import AsyncIterator
import json
from pathlib import Path
from typing import Any

from forge.runtime.state import (
    ModelStreamEvent,
    ModelTextDelta,
    ModelToolCallArgumentsDelta,
    ModelToolCallCompleted,
    ModelToolCallStarted,
    ModelUsageUpdate,
    TokenUsage,
    ToolCall,
)
from forge.subagents.explore import (
    ExploreAgentConfig,
    ExploreRepositoryTool,
    create_explore_registry,
)
from forge.tools import create_default_registry


class FakeExploreClient:
    provider = 'fake'
    max_tokens = 1_000
    context_window = 100_000

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.calls.append(
            {'messages': messages, 'tools': tools, 'system': system}
        )
        yield ModelUsageUpdate(
            usage=TokenUsage(input_tokens=120, output_tokens=0)
        )
        yield ModelTextDelta(text=self.response)
        yield ModelUsageUpdate(
            usage=TokenUsage(input_tokens=120, output_tokens=40)
        )



class ScriptedExploreClient:
    provider = 'fake'
    max_tokens = 1_000
    context_window = 100_000

    def __init__(self, *responses: list[ModelStreamEvent]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.calls.append(
            {'messages': messages, 'tools': tools, 'system': system}
        )
        for event in self.responses.pop(0):
            yield event


def tool_call_response(tool_call: ToolCall) -> list[ModelStreamEvent]:
    return [
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=80, output_tokens=0)
        ),
        ModelToolCallStarted(
            index=tool_call.index,
            id=tool_call.id,
            name=tool_call.name,
        ),
        ModelToolCallArgumentsDelta(
            index=tool_call.index,
            partial_json=json.dumps(tool_call.arguments),
        ),
        ModelToolCallCompleted(tool_call=tool_call),
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=80, output_tokens=20)
        ),
    ]


def text_response(text: str) -> list[ModelStreamEvent]:
    return [
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=160, output_tokens=0)
        ),
        ModelTextDelta(text=text),
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=160, output_tokens=40)
        ),
    ]


def valid_report() -> str:
    return json.dumps(
        {
            'summary': 'The dispatcher calls the handler.',
            'relevant_files': [
                {'path': 'forge/cli.py', 'relevance': 'Entry point.'}
            ],
            'call_paths': ['cli -> dispatcher -> handler'],
            'root_cause_hypotheses': [
                {
                    'hypothesis': 'The handler rejects the state.',
                    'evidence': ['forge/handler.py:10 checks state'],
                    'confidence': 'high',
                }
            ],
            'suggested_edit_points': [
                {
                    'path': 'forge/handler.py',
                    'location': 'handle()',
                    'suggestion': 'Validate before dispatch.',
                }
            ],
            'unresolved_questions': [],
        }
    )


def test_explore_registry_has_only_required_read_tools(tmp_path: Path) -> None:
    registry = create_explore_registry(tmp_path)

    assert registry.names == (
        'list_directory',
        'find_files',
        'grep',
        'read_file',
        'git_log',
    )
    assert all(registry.effect(name) == 'read_only' for name in registry.names)


def test_default_registry_exposes_explore_as_subagent(tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path)

    assert 'explore_repository' in registry.names
    assert registry.effect('explore_repository') == 'read_only'
    assert registry.provenance('explore_repository') == {
        'source': 'subagent',
        'name': 'explore',
    }


def test_explore_returns_only_structured_isolated_report(
    tmp_path: Path,
) -> None:
    client = FakeExploreClient(valid_report())
    tool = ExploreRepositoryTool(
        tmp_path,
        config=ExploreAgentConfig(max_iterations=3, max_input_tokens=5_000),
        client_factory=lambda: client,
    )

    result = asyncio.run(
        tool.run(
            {'question': 'Trace the dispatcher.', 'focus_paths': ['forge']}
        )
    )

    assert result.success
    report = json.loads(result.content)
    assert report['summary'] == 'The dispatcher calls the handler.'
    assert result.metadata['isolated_context'] is True
    assert result.metadata['read_only'] is True
    assert result.metadata['input_tokens'] == 120
    assert result.metadata['report_characters'] == len(result.content)
    assert len(client.calls) == 1
    assert [tool['name'] for tool in client.calls[0]['tools']] == [
        'list_directory',
        'find_files',
        'grep',
        'read_file',
        'git_log',
    ]
    assert 'task_plan' not in str(client.calls[0]['tools'])
    assert 'Focus paths supplied by the parent' in str(
        client.calls[0]['messages']
    )


def test_explore_rejects_unstructured_final_answer(
    tmp_path: Path,
) -> None:
    client = FakeExploreClient('A prose answer with raw findings.')
    tool = ExploreRepositoryTool(tmp_path, client_factory=lambda: client)

    result = asyncio.run(tool.run({'question': 'Find the cause.'}))

    assert not result.success
    assert result.error is not None
    assert result.error.code == 'explore_invalid_report'
    assert len(result.content) <= 2_000


def test_raw_exploration_stays_out_of_parent_result(tmp_path: Path) -> None:
    raw_evidence = 'RAW_INTERNAL_EVIDENCE ' * 800
    (tmp_path / 'large.py').write_text(raw_evidence, encoding='utf-8')
    read_call = ToolCall(
        id='read-1',
        name='read_file',
        arguments={'path': 'large.py'},
        index=0,
    )
    client = ScriptedExploreClient(
        tool_call_response(read_call),
        text_response(valid_report()),
    )
    tool = ExploreRepositoryTool(tmp_path, client_factory=lambda: client)

    result = asyncio.run(tool.run({'question': 'Inspect large.py.'}))

    assert result.success
    assert len(client.calls) == 2
    assert 'RAW_INTERNAL_EVIDENCE' in str(client.calls[1]['messages'])
    assert 'RAW_INTERNAL_EVIDENCE' not in result.content
    assert len(result.content) < len(raw_evidence) / 10
    assert result.metadata['model_calls'] == 2
    assert result.metadata['input_tokens'] == 240