'''External deadlines settle real work; ordinary sessions have no turn budget.'''
import asyncio
from contextlib import aclosing
import json
from time import monotonic

import pytest

from forge.runtime.agent_loop import Conversation
from forge.runtime.state import ModelTextDelta, ModelUsageUpdate, TokenUsage, TurnCompleted
from forge.sessions.store import SessionStore


@pytest.mark.parametrize('phase', ['expired', 'prepare', 'model', 'consumer'])
def test_deadline_persists_one_terminal_result_without_cancelling_consumer(tmp_path, phase):
    journal = SessionStore(tmp_path, data_root=tmp_path / 'private').create(model='offline')
    requests = []
    closed = []

    class Model:
        async def stream(self, **kwargs):
            requests.append(kwargs)
            try:
                yield ModelUsageUpdate(TokenUsage(7, 1))
                yield ModelTextDelta('Observed text')
                await asyncio.Event().wait()
            finally:
                closed.append(True)

    async def run():
        seconds = {'expired': -1, 'prepare': .1, 'model': 20, 'consumer': 2}[phase]
        c = Conversation(client=Model(), context_root=tmp_path, session_journal=journal,
                         caller_deadline=monotonic() + seconds)
        if phase == 'prepare':
            async def wait(**kwargs):
                await asyncio.Event().wait()
            c.session_start = wait
        events = []
        async with aclosing(c.stream('Observe the caller cutoff.')) as stream:
            async for event in stream:
                events.append(event)
                if isinstance(event, ModelTextDelta):
                    if phase == 'consumer':
                        # A timeout scope leaking across yield would cancel us here.
                        await asyncio.sleep(max(0, c.caller_deadline - monotonic()) + .1)
                    else:
                        c.caller_deadline = monotonic() + .05
        final = [e.result for e in events if isinstance(e, TurnCompleted)]
        assert len(final) == 1 and final[0].status == 'failed'
        assert final[0].stop_reason == 'caller_deadline_exceeded'
        assert final[0].model_calls == (1 if phase in {'model', 'consumer'} else 0)
        if requests:
            assert final[0].usage.input_tokens == 7 and closed == [True]
        assert not asyncio.current_task().cancelling()
        assert c.messages[0]['content'] == 'Observe the caller cutoff.'

    asyncio.run(run())
    records = [json.loads(row) for row in journal.path.read_text(encoding='utf-8').splitlines()]
    for kind in ('turn_started', 'caller_deadline_exceeded', 'turn_completed'):
        assert sum(row['type'] == kind for row in records) == 1
    assert not any(row['type'] == 'turn_cancelled' for row in records)


def test_operation_timeout_is_not_relabelled_as_caller_deadline(tmp_path):
    async def run():
        c = Conversation(client=object(), context_root=tmp_path, caller_deadline=monotonic() + 60)
        async def fail(**kwargs):
            raise TimeoutError('connection timed out')
        c.session_start = fail
        with pytest.raises(TimeoutError, match='connection timed out'):
            async for _ in c.stream('Wait'): pass
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['foreground', 'background', 'service'])
def test_deadline_stops_owned_command_and_preserves_settled_tool_record(tmp_path, mode):
    from forge.runtime.state import ModelResponseCompleted, ModelToolCallCompleted, ToolCall
    from tests.runtime.test_command_jobs import command, session

    class Model:
        model = 'offline'
        calls = 0
        async def stream(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield ModelToolCallCompleted(ToolCall(0, 'long', 'run_command', {
                    'command': command("import time; from pathlib import Path; Path('started').touch(); time.sleep(60); Path('late').touch()"),
                    'run_in_background': mode != 'foreground', 'timeout_seconds': 90,
                    'lifetime': 'session' if mode == 'service' else 'command'}))
                yield ModelResponseCompleted('tool_use')
            else:
                await asyncio.Event().wait()
        async def aclose(self): pass

    async def run():
        async with await session(tmp_path, Model()) as handle:
            c = handle.conversation
            c.caller_deadline = monotonic() + 5
            result = await asyncio.wait_for(handle.run('Run the long command.'), 20)
            assert result.stop_reason == 'caller_deadline_exceeded'
            assert (tmp_path / 'project/started').exists()
            assert not (tmp_path / 'project/late').exists()
            assert c.command_jobs.running == (mode == 'service')
            rows = [json.loads(row) for row in handle.journal.path.read_text(encoding='utf-8').splitlines()]
            ended = [row['payload'] for row in rows if row['type'] == 'tool_completed']
            assert len(ended) == 1 and ended[0]['tool_call_id'] == 'long'
            if mode != 'foreground':
                assert ended[0]['result']['metadata']['background']
                if mode == 'service':
                    assert next(iter(c.command_jobs.jobs.values())).owner is c.command_jobs
                else:
                    ended = [row['payload'] for row in rows if row['type'] == 'job_finished']
                    assert len(ended) == 1 and ended[0]['state'] == 'cancelled'
            if mode != 'service':
                assert ended[0]['result']['metadata']['cleanup']['state'] == 'clean'
        assert handle.cleanup['state'] == 'clean'
        assert not c.command_jobs.running
    asyncio.run(run())
