import asyncio
import json

import pytest

from forge.api import AgentDefinition, HarnessSession, SessionOptions
from forge.config import ForgeConfig
from forge.runtime.state import ModelReasoningDelta, ModelTextDelta, ModelResponseCompleted, ModelProviderState
from forge.sessions.trajectory import journal_events, journal_to_openai


@pytest.mark.parametrize('ending', ['failed', 'cancelled', 'completed'])
def test_export_preserves_reasoning_and_unsuccessful_attempts(tmp_path, ending):
    class Model:
        model = 'fixture'
        async def stream(self, **kwargs):
            yield ModelReasoningDelta('observed reasoning ')
            yield ModelReasoningDelta('before terminal')
            yield ModelTextDelta('partial response')
            if ending == 'failed':
                raise OSError('transport failed')
            if ending == 'cancelled':
                raise asyncio.CancelledError
            yield ModelProviderState('fixture', {'opaque': 'continuation'})
            yield ModelResponseCompleted('end_turn')
    async def run():
        opts = SessionOptions(agent=AgentDefinition.core(), data_root=tmp_path,
                              config=ForgeConfig(api_key='fixture', model_id='fixture'))
        async with await HarnessSession.open(opts, model_client_factory=lambda *a, **k: Model()) as session:
            if ending == 'completed':
                await session.run('test')
            else:
                with pytest.raises(OSError if ending == 'failed' else asyncio.CancelledError):
                    await session.run('test')
            assert session.recorder is None
            return session.journal.path
    path = asyncio.run(run())
    before = path.read_bytes()
    events = journal_events(path)
    reasoning = ''.join(e['payload']['text'] for e in events if e['type'] == 'model_reasoning_chunk')
    assert reasoning == 'observed reasoning before terminal'
    if ending == 'completed':
        assert next(e['payload']['data'] for e in events if e['type'] == 'model_provider_state') == {'opaque': 'continuation'}
    assert not list(tmp_path.rglob('*.trajectory.jsonl'))
    rows = journal_to_openai(path)
    assert len(rows) == 1
    row = rows[0]
    assert row['messages'][-1] == {'role': 'assistant', 'content': 'partial response'}
    assert row['metadata']['status'] == ending
    assert row['metadata']['reasoning'] == reasoning
    assert row['metadata']['completeness']['record_closed']
    if ending != 'completed':
        assert not row['metadata']['completeness']['responses_complete']
        assert row['metadata']['turn_outcome']['type'] in {'turn_error', 'turn_cancelled'}
    assert path.read_bytes() == before


def test_tool_completed_preserves_body_before_context_pruning(tmp_path):
    from forge.runtime.executor import ToolExecutor
    from forge.permissions.policy import PermissionManager
    from forge.plugins import PluginTool
    from forge.sessions.store import SessionStore
    from forge.runtime.state import ToolCall
    from forge.tools.base import ToolRegistry
    journal = SessionStore(tmp_path, data_root=tmp_path / 'data').create(model='fixture')
    body = 'large tool result\n' * 30000
    tool = PluginTool('fixture', 'large', 'fixture', {'type': 'object'}, lambda args: body, 'read_only')
    registry = ToolRegistry([tool])
    executor = ToolExecutor(registry, PermissionManager(tmp_path), session_journal=journal)
    journal.record_turn_started('large result', None)
    outcome = asyncio.run(executor.execute(ToolCall(0, 'call', 'large', {})))
    event = next(e for e in journal_events(journal.path) if e['type'] == 'tool_completed')
    assert event['payload']['result']['content'] == outcome.result.content == body
    assert 'payload_ref' in next(json.loads(line) for line in journal.path.read_text().splitlines()
                                 if json.loads(line)['type'] == 'tool_completed')


def test_reused_tool_ids_stay_bound_to_their_own_turn(tmp_path):
    from forge.plugins import PluginTool
    from forge.runtime.state import ToolCall, ModelToolCallCompleted
    from forge.sessions.trajectory import journal_to_atif
    class Model:
        model = 'fixture'
        calls = 0
        async def stream(self, **kwargs):
            self.calls += 1
            if self.calls % 2:
                yield ModelToolCallCompleted(ToolCall(0, 'reused', 'echo', {'value': str(self.calls)}))
                yield ModelResponseCompleted('tool_use')
            else:
                yield ModelTextDelta('done')
                yield ModelResponseCompleted('end_turn')
    async def run():
        opts = SessionOptions(agent=AgentDefinition.core(), data_root=tmp_path,
                              config=ForgeConfig(api_key='fixture', model_id='fixture'))
        async with await HarnessSession.open(opts, model_client_factory=lambda *a, **k: Model()) as session:
            session.conversation.registry.register(PluginTool('fixture', 'echo', 'echo',
                {'type': 'object'}, lambda args: args['value'], 'read_only'))
            await session.run('one')
            await session.run('two')
            return session.journal.path
    path = asyncio.run(run())
    export = journal_to_atif(path)
    observations = [result for step in export['steps'] for result in (step.get('observation') or {}).get('results', [])]
    assert [json.loads(result['content']) for result in observations] == ['1', '3']
    assert journal_to_openai(path)[0]['metadata']['completeness']['record_closed']
