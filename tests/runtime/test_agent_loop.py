'''Tests for the minimal M1 streaming conversation runtime.'''

import asyncio
from collections.abc import AsyncIterator
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import Field

from forge.context.compactor import CompactionConfig
from forge.runtime.agent_loop import (
    AgentLoopLimitError,
    Conversation,
    ModelResponseError,
    load_system_prompt,
)
from forge.runtime.model_client import (
    ModelOutputTruncatedError,
    ModelProtocolError,
)
from forge.runtime.state import (
    ConversationEvent,
    ModelCallCompleted,
    ModelCallStarted,
    ModelStreamEvent,
    ModelTextDelta,
    ModelToolCallArgumentsDelta,
    ModelToolCallCompleted,
    ModelToolCallStarted,
    ModelUsageUpdate,
    TokenUsage,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
    TurnResult,
    ToolCall,
)
from forge.tools.base import Tool, ToolInput, ToolRegistry, ToolResult


class FakeModelClient:
    '''Record requests and emit deterministic model stream events.'''

    provider = 'fake'

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
            if isinstance(event, Exception):
                raise event
            yield event


def streamed_response(
    *text_parts: str,
    input_tokens: int = 10,
    output_tokens: int = 2,
) -> list[ModelStreamEvent]:
    return [
        ModelUsageUpdate(
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=0,
            )
        ),
        *(ModelTextDelta(text=part) for part in text_parts),
        ModelUsageUpdate(
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        ),
    ]


def collect_turn(
    conversation: Conversation,
    prompt: str,
) -> list[ConversationEvent]:
    async def collect() -> list[ConversationEvent]:
        return [event async for event in conversation.stream(prompt)]

    return asyncio.run(collect())


class ReadFileInput(ToolInput):
    path: str = Field(min_length=1)


class RecordingReadFileTool(Tool[ReadFileInput]):
    name = 'read_file'
    description = 'Read a test file.'
    input_model = ReadFileInput

    def __init__(
        self,
        root: Path,
        result: ToolResult | None = None,
    ) -> None:
        super().__init__(root)
        self.calls: list[str] = []
        self.result = result or ToolResult.ok(
            'Read file.',
            content='file contents',
        )

    async def execute(self, arguments: ReadFileInput) -> ToolResult:
        self.calls.append(arguments.path)
        return self.result


class NoOpWriteTool(RecordingReadFileTool):
    name = 'no_op_write'
    description = 'Pretend to write without changing the workspace.'
    effect = 'workspace_write'


class NoChangeWorkspaceTracker:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.revision = 0
        self.changed_paths: tuple[str, ...] = ()

    async def begin_turn(self) -> None:
        self.revision = 0

    async def refresh(self):
        return None


def tool_response(
    *tool_calls: ToolCall,
    input_tokens: int = 15,
    output_tokens: int = 10,
) -> list[ModelStreamEvent]:
    events: list[ModelStreamEvent] = [
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=0)
        )
    ]
    for tool_call in tool_calls:
        events.extend(
            [
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
            ]
        )
    events.append(
        ModelUsageUpdate(
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
    )
    return events


def test_conversation_forwards_stream_and_returns_final_result() -> None:
    client = FakeModelClient(streamed_response('RE', 'ADY'))
    conversation = Conversation(client=client)

    events = collect_turn(conversation, 'Only reply READY')

    assert events == [
        ModelCallStarted(iteration=1),
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=10, output_tokens=0)
        ),
        ModelTextDelta(text='RE'),
        ModelTextDelta(text='ADY'),
        ModelUsageUpdate(
            usage=TokenUsage(input_tokens=10, output_tokens=2)
        ),
        ModelCallCompleted(iteration=1),
        TurnCompleted(
            result=TurnResult(
                text='READY',
                usage=TokenUsage(input_tokens=10, output_tokens=2),
            )
        ),
    ]
    assert client.calls[0]['messages'] == [
        {'role': 'user', 'content': 'Only reply READY'}
    ]
    assert client.calls[0]['tools'] is None
    assert client.calls[0]['system'].startswith(load_system_prompt())
    assert 'Goal:\nOnly reply READY' in client.calls[0]['system']


def test_system_prompt_defines_forgecode_identity() -> None:
    prompt = load_system_prompt()

    assert 'Your product identity is ForgeCode.' in prompt
    assert 'Do not claim to be Anthropic' in prompt
    assert 'The M2 runtime can use built-in file' in prompt
    assert 'only when its schema is included' in prompt
    assert 'call another tool instead of giving a premature' in prompt
    assert 'Do not run destructive commands' in prompt
    assert 'use the `verify` tool' in prompt


def test_conversation_accepts_an_explicit_system_prompt() -> None:
    client = FakeModelClient(streamed_response('READY'))
    conversation = Conversation(
        client=client,
        system_prompt='test system',
    )

    collect_turn(conversation, 'hello')

    assert client.calls[0]['system'].startswith('test system')
    assert 'Goal:\nhello' in client.calls[0]['system']


def test_conversation_executes_tool_and_continues_until_final_text(
    tmp_path: Path,
) -> None:
    tool_call = ToolCall(
        index=0,
        id='toolu_read',
        name='read_file',
        arguments={'path': 'README.md'},
    )
    client = FakeModelClient(
        tool_response(tool_call),
        streamed_response(
            'Finished',
            input_tokens=30,
            output_tokens=4,
        ),
    )
    tool = RecordingReadFileTool(tmp_path)
    registry = ToolRegistry([tool])
    conversation = Conversation(client=client, registry=registry)

    events = collect_turn(conversation, 'Read the README')

    assert ToolExecutionStarted(tool_call=tool_call) in events
    assert ToolExecutionCompleted(
        tool_call=tool_call,
        result=tool.result,
    ) in events
    assert events[-1] == TurnCompleted(
        result=TurnResult(
            text='Finished',
            usage=TokenUsage(input_tokens=45, output_tokens=14),
            tool_calls=(tool_call,),
        )
    )
    assert tool.calls == ['README.md']
    assert client.calls[0]['tools'] == registry.definitions
    second_request = client.calls[1]
    assert second_request['messages'][:2] == [
        {'role': 'user', 'content': 'Read the README'},
        {
            'role': 'assistant',
            'content': [
                {
                    'type': 'tool_use',
                    'id': 'toolu_read',
                    'name': 'read_file',
                    'input': {'path': 'README.md'},
                }
            ],
        },
    ]
    tool_result_message = second_request['messages'][2]
    assert tool_result_message['role'] == 'user'
    assert len(tool_result_message['content']) == 1
    result_block = tool_result_message['content'][0]
    assert result_block['tool_use_id'] == 'toolu_read'
    assert result_block['is_error'] is False
    payload = json.loads(result_block['content'])
    assert payload == {
        'success': True,
        'summary': 'Read file.',
        'content': 'file contents',
        'error': None,
        'metadata': {},
    }
    assert conversation.messages == [
        {'role': 'user', 'content': 'Read the README'},
        {
            'role': 'assistant',
            'content': [
                {
                    'type': 'tool_use',
                    'id': 'toolu_read',
                    'name': 'read_file',
                    'input': {'path': 'README.md'},
                }
            ],
        },
        tool_result_message,
        {'role': 'assistant', 'content': 'Finished'},
    ]


def test_conversation_executes_multiple_tool_calls_in_order(
    tmp_path: Path,
) -> None:
    first = ToolCall(
        index=0,
        id='toolu_first',
        name='read_file',
        arguments={'path': 'a.py'},
    )
    second = ToolCall(
        index=1,
        id='toolu_second',
        name='read_file',
        arguments={'path': 'b.py'},
    )
    client = FakeModelClient(
        tool_response(first, second),
        streamed_response('Done', input_tokens=25, output_tokens=3),
    )
    tool = RecordingReadFileTool(tmp_path)
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([tool]),
    )

    events = collect_turn(conversation, 'Read both files')

    assert tool.calls == ['a.py', 'b.py']
    result_blocks = client.calls[1]['messages'][2]['content']
    assert [
        block['tool_use_id']
        for block in result_blocks
    ] == [
        'toolu_first',
        'toolu_second',
    ]
    assert events[-1].result.tool_calls == (first, second)


def test_failed_tool_result_is_returned_to_model(
    tmp_path: Path,
) -> None:
    tool_call = ToolCall(
        index=0,
        id='toolu_missing',
        name='missing_tool',
        arguments={},
    )
    client = FakeModelClient(
        tool_response(tool_call),
        streamed_response('Could not run that tool.'),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([RecordingReadFileTool(tmp_path)]),
        context_root=tmp_path,
    )

    collect_turn(conversation, 'Use a missing tool')

    blocks = client.calls[1]['messages'][2]['content']
    block = blocks[0]
    payload = json.loads(block['content'])
    assert block['is_error'] is True
    assert payload['success'] is False
    assert payload['error']['code'] == 'unknown_tool'


def test_agent_loop_stops_at_model_call_limit(tmp_path: Path) -> None:
    first = ToolCall(
        index=0,
        id='toolu_1',
        name='read_file',
        arguments={'path': 'a.py'},
    )
    second = ToolCall(
        index=0,
        id='toolu_2',
        name='read_file',
        arguments={'path': 'b.py'},
    )
    client = FakeModelClient(tool_response(first), tool_response(second))
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([RecordingReadFileTool(tmp_path)]),
        max_iterations=2,
    )

    with pytest.raises(AgentLoopLimitError, match='exceeded 2'):
        collect_turn(conversation, 'Never finish')

    assert len(client.calls) == 2


def test_agent_loop_has_no_model_call_limit_by_default() -> None:
    conversation = Conversation(client=FakeModelClient())

    assert conversation.max_iterations is None


def test_invalid_tool_json_is_retried_without_executing_partial_calls(
    tmp_path: Path,
) -> None:
    partial_call = ToolCall(
        index=0,
        id='toolu_partial',
        name='read_file',
        arguments={'path': 'should-not-run.py'},
    )
    tool = RecordingReadFileTool(tmp_path)
    client = FakeModelClient(
        [
            ModelUsageUpdate(usage=TokenUsage(10, 4)),
            ModelToolCallCompleted(tool_call=partial_call),
            ModelProtocolError(
                'Invalid JSON arguments for tool write_file.',
                reason='invalid_tool_arguments',
                tool_name='write_file',
            ),
        ],
        streamed_response('Recovered safely.', input_tokens=12),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([tool]),
    )

    events = collect_turn(conversation, 'Build a page')

    assert tool.calls == []
    assert events[-1].result.text == 'Recovered safely.'
    assert events[-1].result.usage.input_tokens == 22
    feedback = client.calls[1]['messages'][-1]['content']
    assert 'No tool was executed' in feedback
    assert 'Available tools: read_file' in feedback
    assert 'write_file with at most 4000 characters' in feedback
    assert 'Recovery attempt 1 of 2' in feedback


def test_max_tokens_truncation_retries_with_small_patch_feedback() -> None:
    client = FakeModelClient(
        [
            ModelUsageUpdate(usage=TokenUsage(10, 4096)),
            ModelOutputTruncatedError(('apply_patch',)),
        ],
        streamed_response('Retried in smaller steps.', input_tokens=15),
    )
    conversation = Conversation(client=client)

    events = collect_turn(conversation, 'Build a game')

    assert events[-1].result.text == 'Retried in smaller steps.'
    feedback = client.calls[1]['messages'][-1]['content']
    assert 'reached the max_tokens limit' in feedback
    assert 'apply_patch with at most 4000 characters' in feedback
    assert 'Modify only one function or one file section' in feedback


def test_plain_text_truncation_preserves_and_continues_response() -> None:
    client = FakeModelClient(
        [
            ModelUsageUpdate(usage=TokenUsage(10, 0)),
            ModelTextDelta(text='First half, '),
            ModelUsageUpdate(usage=TokenUsage(10, 8192)),
            ModelOutputTruncatedError(),
        ],
        streamed_response(
            'second half.',
            input_tokens=15,
            output_tokens=3,
        ),
    )
    conversation = Conversation(client=client)

    events = collect_turn(conversation, 'Explain the result')

    result = events[-1].result
    assert result.text == 'First half, second half.'
    assert result.usage == TokenUsage(25, 8195)
    continuation_messages = client.calls[1]['messages'][-2:]
    assert continuation_messages[0] == {
        'role': 'assistant',
        'content': 'First half, ',
    }
    assert 'already generated has been preserved' in (
        continuation_messages[1]['content']
    )
    assert 'without repeating earlier content' in (
        continuation_messages[1]['content']
    )
    assert 'Continuation attempt 1 of 2' in (
        continuation_messages[1]['content']
    )
    assert conversation.messages[-1] == {
        'role': 'assistant',
        'content': 'second half.',
    }


def test_plain_text_continuation_stops_after_configured_limit() -> None:
    truncated = [
        ModelUsageUpdate(usage=TokenUsage(10, 0)),
        ModelTextDelta(text='partial'),
        ModelUsageUpdate(usage=TokenUsage(10, 8192)),
        ModelOutputTruncatedError(),
    ]
    client = FakeModelClient(truncated, truncated)
    conversation = Conversation(
        client=client,
        max_output_continuations=1,
    )

    with pytest.raises(
        ModelOutputTruncatedError,
        match='max_tokens limit',
    ):
        collect_turn(conversation, 'Explain at length')

    assert len(client.calls) == 2


def test_protocol_recovery_stops_after_configured_limit() -> None:
    error = ModelProtocolError(
        'invalid arguments',
        reason='invalid_tool_arguments',
        tool_name='write_file',
    )
    client = FakeModelClient([error], [error])
    conversation = Conversation(
        client=client,
        max_protocol_recoveries=1,
    )

    with pytest.raises(ModelProtocolError, match='invalid arguments'):
        collect_turn(conversation, 'Build a page')

    assert len(client.calls) == 2


def test_second_protocol_recovery_requests_minimal_skeleton() -> None:
    error = ModelOutputTruncatedError(('apply_patch',))
    client = FakeModelClient(
        [error],
        [error],
        streamed_response('Recovered with a skeleton.'),
    )
    conversation = Conversation(client=client)

    events = collect_turn(conversation, 'Build a game')

    assert events[-1].result.text == 'Recovered with a skeleton.'
    feedback = client.calls[2]['messages'][-1]['content']
    assert 'at most 2000 characters' in feedback
    assert 'Create only a minimal skeleton' in feedback
    assert 'HTML, CSS, and JavaScript in separate tool calls' in feedback


def test_conversation_sends_previous_turns_as_context() -> None:
    client = FakeModelClient(
        streamed_response('Hello'),
        streamed_response('Your name is Ada', input_tokens=20),
    )
    conversation = Conversation(client=client)

    collect_turn(conversation, 'Hello')
    collect_turn(conversation, 'What is my name?')

    assert client.calls[1]['messages'] == [
        {'role': 'user', 'content': 'Hello'},
        {'role': 'assistant', 'content': 'Hello'},
        {'role': 'user', 'content': 'What is my name?'},
    ]
    assert client.calls[1]['system'].startswith(load_system_prompt())
    assert 'Goal:\nWhat is my name?' in client.calls[1]['system']
    assert conversation.messages == [
        {'role': 'user', 'content': 'Hello'},
        {'role': 'assistant', 'content': 'Hello'},
        {'role': 'user', 'content': 'What is my name?'},
        {'role': 'assistant', 'content': 'Your name is Ada'},
    ]


def test_current_goal_survives_many_tool_calls_and_message_snipping(
    tmp_path: Path,
) -> None:
    (tmp_path / 'sample.txt').write_text('content\n', encoding='utf-8')
    responses = [
        tool_response(
            ToolCall(
                index=0,
                id=f'toolu_{index}',
                name='read_file',
                arguments={'path': 'sample.txt'},
            )
        )
        for index in range(30)
    ]
    client = FakeModelClient(
        *responses,
        streamed_response('Finished the original task.'),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([RecordingReadFileTool(tmp_path)]),
        context_config=CompactionConfig(
            message_limit=10,
            keep_first_messages=2,
            keep_recent_messages=8,
        ),
    )

    collect_turn(conversation, 'Keep this exact active goal')

    assert len(client.calls) == 31
    assert all(
        'Goal:\nKeep this exact active goal' in call['system']
        for call in client.calls
    )


def test_exact_tool_repeat_is_skipped_after_limit(tmp_path: Path) -> None:
    call = lambda index: ToolCall(
        index=0,
        id=f'toolu_{index}',
        name='read_file',
        arguments={'path': 'sample.txt'},
    )
    tool = RecordingReadFileTool(tmp_path)
    client = FakeModelClient(
        tool_response(call(1)),
        tool_response(call(2)),
        tool_response(call(3)),
        streamed_response('Used the existing result.'),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([tool]),
    )

    events = collect_turn(conversation, 'Read the sample once')

    assert tool.calls == ['sample.txt', 'sample.txt']
    completed = [
        event for event in events
        if isinstance(event, ToolExecutionCompleted)
    ]
    assert completed[-1].result.error is not None
    assert completed[-1].result.error.code == 'repeated_tool_call'


def test_stagnation_stops_mutating_turn_without_total_call_limit(
    tmp_path: Path,
) -> None:
    tool = NoOpWriteTool(tmp_path)
    tracker = NoChangeWorkspaceTracker(tmp_path)
    responses = [
        tool_response(
            ToolCall(
                index=0,
                id=f'toolu_{index}',
                name='no_op_write',
                arguments={'path': f'file-{index}.txt'},
            )
        )
        for index in range(1, 4)
    ]
    conversation = Conversation(
        client=FakeModelClient(*responses),
        registry=ToolRegistry([tool], workspace_tracker=tracker),
        stagnation_warning=2,
        stagnation_limit=3,
    )

    events = collect_turn(conversation, 'Make a real code change')

    result = next(
        event.result for event in events if isinstance(event, TurnCompleted)
    )
    assert result.status == 'blocked'
    assert '3 model calls without workspace' in result.text
    assert conversation.task_manager.active is not None
    assert conversation.task_manager.active.status == 'blocked'


def test_conversation_does_not_commit_stream_without_text() -> None:
    client = FakeModelClient(
        [
            ModelUsageUpdate(
                usage=TokenUsage(input_tokens=10, output_tokens=0)
            )
        ]
    )
    conversation = Conversation(client=client)

    with pytest.raises(
        ModelResponseError,
        match='did not contain any text',
    ):
        collect_turn(conversation, 'Hello')

    assert conversation.messages == []


def test_relevant_repository_memory_is_injected_only_for_current_query(
    tmp_path: Path,
) -> None:
    client = FakeModelClient(
        streamed_response('Use pytest'),
        streamed_response('Use the formatter'),
    )
    conversation = Conversation(
        client=client,
        registry=ToolRegistry([RecordingReadFileTool(tmp_path)]),
        context_root=tmp_path,
    )
    conversation.context.remember('testing', 'Calculator tests use pytest.')

    collect_turn(conversation, 'How do calculator tests run?')
    collect_turn(conversation, 'How should formatting work?')

    assert 'Calculator tests use pytest.' in client.calls[0]['system']
    assert 'Calculator tests use pytest.' not in client.calls[1]['system']


def test_conversation_does_not_commit_stream_without_usage() -> None:
    client = FakeModelClient([ModelTextDelta(text='Hello')])
    conversation = Conversation(client=client)

    with pytest.raises(
        ModelResponseError,
        match='did not contain token usage',
    ):
        collect_turn(conversation, 'Hello')

    assert conversation.messages == []


def test_conversation_rejects_empty_prompt() -> None:
    conversation = Conversation(client=FakeModelClient())

    with pytest.raises(ValueError, match='prompt must not be empty'):
        collect_turn(conversation, '   ')


def test_conversation_context_stats_include_request_layers() -> None:
    client = FakeModelClient()
    client.max_tokens = 100
    client.context_window = 1_000
    tools = [
        {
            'name': 'read_file',
            'description': 'Read a file.',
            'input_schema': {'type': 'object'},
        }
    ]
    conversation = Conversation(
        client=client,
        system_prompt='system rules',
        tools=tools,
    )
    conversation.messages.append({'role': 'user', 'content': 'history'})

    stats = conversation.context_stats

    assert stats.message_count == 1
    assert stats.system_characters == len('system rules')
    assert stats.tool_schema_characters > 0
    assert stats.context_window_tokens == 1_000
    assert stats.reserved_output_tokens == 100
    assert stats.remaining_tokens is not None
