import asyncio
import json

import pytest

from forge.api import AgentDefinition, HarnessSession, SessionOptions
from forge.config import ForgeConfig
from forge.runtime.state import ModelReasoningDelta, ModelTextDelta, ModelResponseCompleted, ModelProviderState
from forge.sessions.trajectory import journal_events, journal_to_atif, journal_to_openai


@pytest.mark.parametrize('ending', ['failed', 'cancelled', 'completed', 'deadline'])
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
            if ending == 'deadline':
                await asyncio.Event().wait()
            yield ModelProviderState('fixture', {'opaque': 'continuation'})
            yield ModelResponseCompleted('end_turn')
    async def run():
        opts = SessionOptions(agent=AgentDefinition.core(), data_root=tmp_path,
                              config=ForgeConfig(api_key='fixture', model_id='fixture'))
        async with await HarnessSession.open(opts, model_client_factory=lambda *a, **k: Model()) as session:
            if ending in {'completed', 'deadline'}:
                if ending == 'deadline':
                    from time import monotonic
                    session.conversation.caller_deadline = monotonic() + 1
                result = await session.run('test')
                assert result.stop_reason == ('caller_deadline_exceeded' if ending == 'deadline' else 'completed')
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
    assert row['metadata']['status'] == ('cancelled' if ending == 'deadline' else ending)
    assert row['metadata']['reasoning'] == reasoning
    assert row['metadata']['completeness']['record_closed']
    if ending != 'completed':
        assert not row['metadata']['completeness']['responses_complete']
        assert row['metadata']['turn_outcome']['type'] == (
            'turn_completed' if ending == 'deadline' else 'turn_error' if ending == 'failed' else 'turn_cancelled')
    atif = journal_to_atif(path)
    agent = next(step for step in atif['steps'] if step['source'] == 'agent')
    assert agent['reasoning_content'] == reasoning
    terminal = next(step for step in atif['steps'] if step['message'] == row['metadata']['turn_outcome']['type'])
    assert terminal['extra']['turn_id'] == row['metadata']['turn_id']
    assert terminal['extra']['relationship'] == {k: v for k, v in row['metadata']['turn_outcome'].items() if k != 'type'}
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
    turns = [step for step in export['steps'] if step['message'] == 'turn_completed']
    assert len(turns) == 2 and turns[0]['extra']['turn_id'] != turns[1]['extra']['turn_id']
    assert journal_to_openai(path)[0]['metadata']['completeness']['record_closed']


def test_failed_request_exports_keep_provider_diagnostics(tmp_path):
    from forge.runtime.state import ModelCallError
    class Model:
        model = 'fixture'
        async def stream(self, **kwargs):
            yield ModelTextDelta('observed partial')
            raise ModelCallError('stream_interrupted', 'Provider interrupted', retryable=False,
                diagnostics={'http_status':503, 'request_id':'provider-fixture', 'message':'busy'})
    async def run():
        async with await HarnessSession.open(SessionOptions(agent=AgentDefinition.core(), data_root=tmp_path,
            config=ForgeConfig(api_key='fixture', model_id='fixture')),
            model_client_factory=lambda *a, **kw: Model()) as session:
            result = await session.run('offline diagnostic')
            assert result.status == 'failed'
            return session.journal.path
    path = asyncio.run(run())
    before = path.read_bytes()
    row = journal_to_openai(path)[0]
    assert row['metadata']['status'] == 'failed'
    assert row['metadata']['request']['error']['request_id'] == 'provider-fixture'
    atif = journal_to_atif(path)
    request = next(s['extra']['request'] for s in atif['steps'] if s['source'] == 'agent')
    assert request['error']['http_status'] == 503
    assert not atif['extra']['completeness']['responses_complete']
    assert path.read_bytes() == before


def test_parallel_deadline_exports_completed_and_cancelled_tools_separately(tmp_path):
    from time import monotonic
    from forge.runtime.state import ModelToolCallCompleted, ToolCall
    from tests.runtime.test_parallel_tools import Probe, until

    class Model:
        calls = 0
        async def stream(self, **kwargs):
            self.calls += 1
            assert self.calls == 1, 'A deadline must not request another model response.'
            for i in range(3):
                yield ModelToolCallCompleted(ToolCall(i, str(i), 'probe', {'value': i}))
            yield ModelResponseCompleted('tool_use')
        async def aclose(self): pass

    async def run():
        async with await HarnessSession.open(SessionOptions(agent=AgentDefinition.core(), data_root=tmp_path / 'data',
            config=ForgeConfig(api_key='offline', model_id='offline')),
            model_client_factory=lambda *a, **kw: Model()) as session:
            probe = Probe(tmp_path)
            c = session.conversation
            c.registry.register(probe)
            c.max_parallel_tools = 2
            c.caller_deadline = monotonic() + 2
            task = asyncio.create_task(session.run('Run three independent tools.'))
            try:
                await until(lambda: probe.started == [0, 1])
                probe.release[1].set()
                await until(lambda: probe.started == [0, 1, 2])
                result = await asyncio.wait_for(task, 10)
                assert result.stop_reason == 'caller_deadline_exceeded'
                assert set(probe.cancelled) == {0, 2}
                return session.journal.path
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    path = asyncio.run(run())
    before = path.read_bytes()
    row = journal_to_openai(path)[0]
    expected = {'0': 'cancelled', '1': 'executed', '2': 'cancelled'}
    assert {r['tool_call_id']: r['status'] for r in row['metadata']['tool_results']} == expected
    atif = journal_to_atif(path)
    agent = next(s for s in atif['steps'] if s['source'] == 'agent')
    assert {r['source_call_id']: r['extra']['status'] for r in agent['observation']['results']} == expected
    assert atif['extra']['completeness']['record_closed']
    assert atif['steps'][-1]['extra']['relationship']['stop_reason'] == 'caller_deadline_exceeded'
    assert path.read_bytes() == before
