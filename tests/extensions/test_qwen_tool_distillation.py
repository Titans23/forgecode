from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from extensions.qwen_tool_distillation import (
    EpisodeSource,
    ForgeCodeRolloutEnvironment,
    QwenEndpoint,
    QwenModelClient,
    RecordingModelClient,
    RolloutTask,
    TraceRecorder,
    TraceRejected,
    build_sft_rows,
    clean_episode,
    load_trace,
    to_openai_messages,
    to_openai_tools,
    validate_json_schema,
)
from forge.runtime.model_client import ModelOutputTruncatedError, ModelProtocolError
from forge.runtime.state import (
    ModelResponseCompleted,
    ModelTextDelta,
    ModelToolCallArgumentsDelta,
    ModelToolCallCompleted,
    ModelToolCallStarted,
    ModelUsageUpdate,
    TokenUsage,
)


class _AsyncChunks:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks

    def __aiter__(self):
        async def iterate():
            for chunk in self.chunks:
                yield chunk

        return iterate()


class _Completions:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks
        self.request: dict[str, Any] | None = None

    async def create(self, **request: Any) -> _AsyncChunks:
        self.request = request
        return _AsyncChunks(self.chunks)


class _SDK:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chat = SimpleNamespace(completions=_Completions(chunks))


def _collect(stream):
    async def run():
        return [event async for event in stream]

    return asyncio.run(run())


def _source(**overrides: Any) -> EpisodeSource:
    values: dict[str, Any] = {
        'task_id': 'task-1',
        'repository': 'owner/repo',
        'source_revision': 'a' * 40,
        'task_template': 'bugfix',
        'split': 'train',
        'license_id': 'MIT',
        'source_is_public': True,
    }
    values.update(overrides)
    return EpisodeSource(**values)


def _valid_trace(path: Path) -> list[dict[str, Any]]:
    recorder = TraceRecorder(path, episode_id='episode', execution_verified=True)
    recorder.start(_source())
    request_id = recorder.model_request(
        role='main',
        system='SYSTEM-SENTINEL',
        messages=[{'role': 'user', 'content': 'fix it'}],
        tools=[
            {
                'name': 'read_file',
                'description': 'Read one file',
                'input_schema': {
                    'type': 'object',
                    'properties': {'path': {'type': 'string', 'minLength': 1}},
                    'required': ['path'],
                    'additionalProperties': False,
                },
            }
        ],
        model='teacher',
    )
    recorder.model_response(
        request_id=request_id,
        text='',
        calls=[],
        usage=TokenUsage(10, 3),
        finish_reason='tool_calls',
    )
    recorder.append(
        'outcome',
        {
            'status': 'completed',
            'success': True,
            'validated': True,
            'completion_gate_accepted': True,
        },
    )
    return load_trace(path)


def test_endpoint_uses_only_extension_namespace() -> None:
    endpoint = QwenEndpoint.from_env(
        {
            'MODEL_ID': 'must-not-leak',
            'QWEN_DISTILL_MODEL': 'default-model',
            'QWEN_DISTILL_EXPLORE_MODEL': 'explore-model',
        },
        role='explore',
    )
    assert endpoint.model == 'explore-model'
    assert endpoint.role == 'explore'


def test_anthropic_history_and_tool_schema_are_converted() -> None:
    tools = to_openai_tools(
        [{'name': 'read_file', 'description': 'read', 'input_schema': {'type': 'object'}}]
    )
    messages = to_openai_messages(
        [
            {
                'role': 'assistant',
                'content': [
                    {'type': 'text', 'text': 'checking'},
                    {
                        'type': 'tool_use',
                        'id': 'call-1',
                        'name': 'read_file',
                        'input': {'path': 'a.py'},
                    },
                ],
            },
            {
                'role': 'user',
                'content': [
                    {'type': 'tool_result', 'tool_use_id': 'call-1', 'content': 'ok'}
                ],
            },
        ],
        system='system',
    )
    assert tools[0]['function']['parameters'] == {'type': 'object'}
    assert messages[1]['tool_calls'][0]['function']['name'] == 'read_file'
    assert messages[2] == {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'ok'}


def test_qwen_stream_preserves_parallel_calls_usage_and_native_options() -> None:
    chunks = [
        {
            'choices': [
                {
                    'index': 0,
                    'delta': {
                        'tool_calls': [
                            {
                                'index': 0,
                                'id': 'a',
                                'function': {'name': 'read_', 'arguments': '{"path":'},
                            },
                            {
                                'index': 1,
                                'id': 'b',
                                'function': {'name': 'read_file', 'arguments': '{"path":"b"}'},
                            },
                        ]
                    },
                }
            ]
        },
        {
            'choices': [
                {
                    'index': 0,
                    'finish_reason': 'tool_calls',
                    'delta': {
                        'tool_calls': [
                            {
                                'index': 0,
                                'function': {'name': 'file', 'arguments': '"a"}'},
                            }
                        ]
                    },
                }
            ],
            'usage': {
                'prompt_tokens': 12,
                'completion_tokens': 4,
                'prompt_tokens_details': {'cached_tokens': 2},
            },
        },
    ]
    sdk = _SDK(chunks)
    client = QwenModelClient(QwenEndpoint(), sdk_client=sdk)
    events = _collect(
        client.stream(
            [{'role': 'user', 'content': 'read'}],
            [{'name': 'read_file', 'input_schema': {'type': 'object'}}],
            'system',
        )
    )
    completed = [event.tool_call for event in events if isinstance(event, ModelToolCallCompleted)]
    assert [(call.index, call.id, call.arguments['path']) for call in completed] == [
        (0, 'a', 'a'),
        (1, 'b', 'b'),
    ]
    usage = next(event.usage for event in events if isinstance(event, ModelUsageUpdate))
    assert usage == TokenUsage(10, 4, cache_read_input_tokens=2)
    assert sdk.chat.completions.request['parallel_tool_calls'] is True
    assert sdk.chat.completions.request['extra_body'] == {
        'chat_template_kwargs': {'enable_thinking': False}
    }


@pytest.mark.parametrize(
    ('chunks', 'reason'),
    [
        (
            [
                {
                    'choices': [
                        {
                            'finish_reason': 'tool_calls',
                            'delta': {
                                'tool_calls': [
                                    {
                                        'index': 0,
                                        'id': 'a',
                                        'function': {
                                            'name': 'read_file',
                                            'arguments': '{bad',
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
            ],
            'invalid_tool_arguments',
        ),
        (
            [
                {
                    'choices': [
                        {
                            'finish_reason': 'tool_calls',
                            'delta': {
                                'tool_calls': [
                                    {
                                        'index': 0,
                                        'id': 'a',
                                        'function': {
                                            'name': 'delete_world',
                                            'arguments': '{}',
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
            ],
            'unavailable_tool',
        ),
    ],
)
def test_qwen_stream_rejects_malformed_or_unknown_calls(chunks, reason) -> None:
    client = QwenModelClient(QwenEndpoint(), sdk_client=_SDK(chunks))
    with pytest.raises(ModelProtocolError) as captured:
        _collect(
            client.stream(
                [{'role': 'user', 'content': 'read'}],
                [{'name': 'read_file', 'input_schema': {'type': 'object'}}],
            )
        )
    assert captured.value.reason == reason


def test_qwen_stream_exposes_truncation() -> None:
    client = QwenModelClient(
        QwenEndpoint(),
        sdk_client=_SDK(
            [
                {
                    'choices': [
                        {
                            'finish_reason': 'length',
                            'delta': {
                                'tool_calls': [
                                    {
                                        'index': 0,
                                        'id': 'a',
                                        'function': {
                                            'name': 'read_file',
                                            'arguments': '{"path":',
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
            ]
        ),
    )
    with pytest.raises(ModelOutputTruncatedError) as captured:
        _collect(
            client.stream(
                [{'role': 'user', 'content': 'read'}],
                [{'name': 'read_file', 'input_schema': {'type': 'object'}}],
            )
        )
    assert captured.value.tool_names == ('read_file',)


@dataclass
class _WrappedClient:
    model: str = 'teacher'
    provider: str = 'fake'

    async def stream(self, messages, tools=None, system=None):
        yield ModelTextDelta('done')
        yield ModelUsageUpdate(TokenUsage(2, 1))
        yield ModelResponseCompleted('stop')


def test_recording_wrapper_records_exact_request_and_visible_response(tmp_path: Path) -> None:
    path = tmp_path / 'trace.jsonl'
    recorder = TraceRecorder(path, episode_id='episode')
    recorder.start(_source())
    client = RecordingModelClient(_WrappedClient(), recorder)
    _collect(client.stream([{'role': 'user', 'content': 'exact'}], [], 'dynamic'))
    records = load_trace(path)
    request = next(item['payload'] for item in records if item['record_type'] == 'model_request')
    response = next(item['payload'] for item in records if item['record_type'] == 'model_response')
    assert request['system'] == 'dynamic'
    assert request['messages'] == [{'role': 'user', 'content': 'exact'}]
    assert response['text'] == 'done'
    assert response['usage']['output_tokens'] == 1


def test_cleaner_rejects_unvalidated_or_unlicensed_data(tmp_path: Path) -> None:
    records = _valid_trace(tmp_path / 'trace.jsonl')
    records[0]['payload']['license_id'] = 'Proprietary'
    outcome = next(item for item in records if item['record_type'] == 'outcome')
    outcome['payload']['validated'] = False
    with pytest.raises(TraceRejected) as captured:
        clean_episode(records)
    assert 'not approved' in str(captured.value)
    assert 'not independently validated' in str(captured.value)


def test_sft_keeps_exact_system_and_masks_context(tmp_path: Path) -> None:
    records = _valid_trace(tmp_path / 'trace.jsonl')
    response = next(item for item in records if item['record_type'] == 'model_response')
    response['payload']['text'] = '<think>hidden</think>visible'
    rows = build_sft_rows(records, token_counter=len, max_tokens=10_000)
    assert rows[0]['messages'][0] == {
        'role': 'system',
        'content': 'SYSTEM-SENTINEL',
        'loss': False,
    }
    assert rows[0]['messages'][-1] == {
        'role': 'assistant',
        'content': 'visible',
        'loss': True,
    }
    assert all(not message['loss'] for message in rows[0]['messages'][:-1])


def test_schema_validation_handles_nested_constraints() -> None:
    schema = {
        'type': 'object',
        'properties': {
            'items': {
                'type': 'array',
                'minItems': 1,
                'items': {
                    'type': 'object',
                    'properties': {'name': {'type': 'string', 'minLength': 2}},
                    'required': ['name'],
                },
            }
        },
        'required': ['items'],
    }
    assert validate_json_schema({'items': [{'name': 'x'}]}, schema) == [
        '$.items[0].name is too short'
    ]


def test_rollout_environment_isolated_worktree_and_completion_gate(tmp_path: Path) -> None:
    repository = tmp_path / 'repo'
    repository.mkdir()
    subprocess.run(['git', 'init', str(repository)], check=True, capture_output=True)
    subprocess.run(
        ['git', '-C', str(repository), 'config', 'user.email', 'test@example.invalid'],
        check=True,
    )
    subprocess.run(
        ['git', '-C', str(repository), 'config', 'user.name', 'Test'], check=True
    )
    (repository / 'value.txt').write_text('old', encoding='utf-8')
    subprocess.run(['git', '-C', str(repository), 'add', 'value.txt'], check=True)
    subprocess.run(['git', '-C', str(repository), 'commit', '-m', 'fixture'], check=True)
    revision = subprocess.run(
        ['git', '-C', str(repository), 'rev-parse', 'HEAD'],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    def execute(workspace, name, arguments, metadata):
        assert name == 'write_value'
        (workspace / 'value.txt').write_text(arguments['value'], encoding='utf-8')
        return {'success': True}

    def gate(workspace, answer, task):
        return {
            'accepted': answer == 'done'
            and (workspace / 'value.txt').read_text(encoding='utf-8') == 'new'
        }

    environment = ForgeCodeRolloutEnvironment(
        repository,
        tools=[
            {
                'name': 'write_value',
                'input_schema': {
                    'type': 'object',
                    'properties': {'value': {'type': 'string'}},
                    'required': ['value'],
                },
            }
        ],
        tool_executor=execute,
        completion_gate=gate,
    )
    observation = environment.reset(RolloutTask('task', 'change it', revision))
    workspace = environment.workspace
    assert observation['workspace_revision'] == revision
    transition = asyncio.run(
        environment.step(
            {
                'type': 'tool_call',
                'id': 'call-1',
                'name': 'write_value',
                'arguments': {'value': 'new'},
            }
        )
    )
    assert transition.observation['workspace_changed'] is True
    assert transition.reward <= 0.25
    finished = asyncio.run(environment.step({'type': 'finish', 'content': 'done'}))
    assert finished.done is True
    assert finished.reward == 1.0
    assert (repository / 'value.txt').read_text(encoding='utf-8') == 'old'
    environment.close()
    assert workspace is not None and not workspace.exists()
