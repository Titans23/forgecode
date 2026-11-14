'''Deterministic overlap, barriers, authority and cancellation contracts.'''
import asyncio
import base64
import json
from functools import wraps

import pytest

from forge.hooks import HookOutcome
from forge.permissions.policy import PermissionManager, PermissionRequest
from forge.runtime.executor import ToolExecutor
from forge.runtime.state import ToolCall, ToolExecutionReady, ToolExecutionStarted
from forge.runtime.tool_batch import schedule_calls
from forge.tools.base import Tool, ToolInput, ToolRegistry, ToolResult


def run_async(test):
    @wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


class Input(ToolInput):
    value: int


class Probe(Tool[Input]):
    name = 'probe'
    description = 'Controlled asynchronous operation'
    input_model = Input

    def __init__(self, root, *, exclusive=()):
        super().__init__(root)
        self.exclusive = exclusive
        self.started = []
        self.cancelled = []
        self.release = [asyncio.Event() for _ in range(8)]

    def is_concurrency_safe(self, arguments):
        return arguments['value'] not in self.exclusive

    def permission_request(self, arguments):
        return PermissionRequest(self.name, 'repository.read', 'low')

    async def execute(self, arguments):
        value = arguments.value
        self.started.append(value)
        try:
            await self.release[value].wait()
        except asyncio.CancelledError:
            self.cancelled.append(value)
            raise
        return ToolResult.ok(f'result {value}')


def batch(root, probe, count, *, limit=2, hooks=None):
    registry = ToolRegistry([probe])
    executor = ToolExecutor(registry, PermissionManager(root, mode='auto', load_stored_rules=False), hook_runner=hooks)
    calls = [ToolCall(i, str(i), 'probe', {'value': i}) for i in range(count)]
    outcomes = {}
    return schedule_calls(calls, executor, executor.prepare, limit=limit, completed=outcomes), outcomes


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0)


@run_async
async def test_parallel_pool_refills_and_results_commit_in_model_order(tmp_path):
    probe = Probe(tmp_path)
    stream, outcomes = batch(tmp_path, probe, 3)
    events = []
    async def consume():
        async for event in stream:
            events.append(event)
    task = asyncio.create_task(consume())
    await until(lambda: probe.started == [0, 1])
    probe.release[1].set()
    await until(lambda: probe.started == [0, 1, 2])
    assert [e.tool_call.id for e in events if isinstance(e, ToolExecutionReady)] == ['1']
    assert not outcomes  # Fast completion is visible before ordered settlement.
    probe.release[2].set()
    probe.release[0].set()
    await asyncio.wait_for(task, 5)
    assert [event[0].id for event in events if isinstance(event, tuple)] == ['0', '1', '2']
    assert all(outcome.result.success for outcome in outcomes.values())


@run_async
@pytest.mark.parametrize('limit,exclusive', [(1, ()), (3, (1,))])
async def test_exclusive_call_and_serial_configuration_form_barriers(tmp_path, limit, exclusive):
    probe = Probe(tmp_path, exclusive=exclusive)
    stream, _ = batch(tmp_path, probe, 3, limit=limit)
    async def consume():
        return [event async for event in stream]
    task = asyncio.create_task(consume())
    for index in range(3):
        await until(lambda: index in probe.started)
        assert probe.started == list(range(index + 1))
        probe.release[index].set()
    await asyncio.wait_for(task, 5)


@run_async
async def test_hook_reclassification_waits_for_earlier_calls(tmp_path):
    probe = Probe(tmp_path, exclusive=(4,))
    async def hook(event):
        if event.name == 'PreToolUse' and event.arguments['value'] == 1:
            return HookOutcome(arguments={'value': 4})
        return HookOutcome()
    stream, outcomes = batch(tmp_path, probe, 3, hooks=hook)
    async def consume():
        return [event async for event in stream]
    task = asyncio.create_task(consume())
    await until(lambda: probe.started == [0])
    probe.release[0].set()
    await until(lambda: probe.started == [0, 4])
    assert 2 not in probe.started
    probe.release[4].set()
    probe.release[2].set()
    await asyncio.wait_for(task, 5)
    assert outcomes['1'].arguments == {'value': 4}


@run_async
async def test_denial_stops_new_dispatch_and_drains_started_calls(tmp_path):
    probe = Probe(tmp_path)
    async def hook(event):
        if event.name == 'PreToolUse' and event.arguments['value'] == 1:
            await until(lambda: bool(probe.started))
            return HookOutcome(allowed=False, reason='denied')
        return HookOutcome()
    stream, outcomes = batch(tmp_path, probe, 4, hooks=hook)
    async with asyncio.timeout(5):
        _ = [event async for event in stream]
    assert probe.started == [0] and probe.cancelled == [0]
    assert outcomes['0'].result.error.code == 'execution_cancelled'
    assert outcomes['1'].result.error.code == 'hook_denied'
    assert all(outcomes[str(i)].record.status == 'cancelled' for i in (2, 3))


@run_async
async def test_consumer_close_cancels_active_body_and_records_unstarted_tail(tmp_path):
    probe = Probe(tmp_path)
    stream, outcomes = batch(tmp_path, probe, 3)
    async for event in stream:
        if isinstance(event, ToolExecutionStarted):
            await until(lambda: probe.started == [0])
            break
    await stream.aclose()
    assert probe.cancelled == [0]
    assert outcomes['0'].result.error.code == 'execution_cancelled'
    assert all(outcomes[str(i)].result.error.code == 'not_executed_after_interruption' for i in (1, 2))


@run_async
async def test_native_outputs_are_demultiplexed_per_execution(tmp_path):
    from forge.protocol import canonical_hash
    from forge.sandbox.file_client import FileWorkerClient
    policy = {'limits': {'wall_time_seconds': None, 'command_output_bytes': 1048576}}
    class Native:
        policy_hash = canonical_hash(policy)
        outputs = asyncio.Queue()
        ids = []
        async def execute(self, identity, command):
            self.ids.append(identity)
            if len(self.ids) == 2:
                for stream in ('stdout', 'stderr'):
                    for item in reversed(self.ids):
                        await self.outputs.put({'execution_id': item, 'stream': stream, 'final': True,
                            'raw_base64': base64.b64encode(item.encode() if stream == 'stdout' else b'').decode()})
        async def status(self, identity):
            return {'state': 'finished', 'discarded_bytes': 0, 'exit_code': 0}
        async def cancel(self, *args):
            return {'confirmed': True, 'cleanup': {'state': 'clean'}}
        async def close(self):
            return {'state': 'clean'}
        async def aclose(self):
            pass
    native = Native()
    client = FileWorkerClient({'canonical_path': str(tmp_path)}, policy, native=native)
    try:
        result = await asyncio.wait_for(asyncio.gather(
            client._native_request('first', json.dumps({}).encode()),
            client._native_request('second', json.dumps({}).encode())), 5)
        assert result == [b'first', b'second']
    finally:
        assert (await client.close())['state'] == 'clean'


@run_async
async def test_finalization_failure_is_not_replayed(tmp_path):
    probe = Probe(tmp_path)
    probe.release[0].set()
    hooks = []
    async def hook(event):
        if event.name == 'PostToolUse':
            hooks.append(event.tool_call_id)
            raise RuntimeError('post hook failed')
        return HookOutcome()
    stream, outcomes = batch(tmp_path, probe, 1, hooks=hook)
    _ = [event async for event in stream]
    assert hooks == ['0']
    assert outcomes['0'].record.status == 'indeterminate'
    assert outcomes['0'].result.error.details['stage'] == 'settlement'
    assert outcomes['0'].result.error.details['exception'] == [
        {'type': 'RuntimeError', 'message': 'post hook failed'}]
    assert probe.started == [0]


@run_async
async def test_conversation_preserves_journal_pairing_and_per_call_context(tmp_path):
    from forge.runtime.agent_loop import Conversation
    from forge.runtime.state import ModelToolCallCompleted, ModelResponseCompleted, ModelTextDelta
    from forge.sessions.store import SessionStore
    from forge.sessions.trajectory import journal_events
    probe = Probe(tmp_path)
    probe.release[0].set()
    probe.release[1].set()
    class Model:
        model = 'scripted-parallel'
        count = 0
        async def stream(self, messages, **kwargs):
            self.count += 1
            if self.count == 1:
                for i in range(2):
                    yield ModelToolCallCompleted(ToolCall(i, str(i), 'probe', {'value': i}))
                yield ModelResponseCompleted('tool_use')
            else:
                yield ModelTextDelta('done')
                yield ModelResponseCompleted('end_turn')
    store = SessionStore(tmp_path, data_root=tmp_path / 'private')
    journal = store.create(model='scripted-parallel')
    conversation = Conversation(client=Model(), registry=ToolRegistry([probe]), context_root=tmp_path,
        session_journal=journal, session_store=store, include_task_tools=False,
        permission_manager=PermissionManager(tmp_path, mode='auto', load_stored_rules=False))
    try:
        _ = [event async for event in conversation.stream('Run both independent probes.')]
        events = journal_events(journal.path)
        assert [e['payload']['tool_call_id'] for e in events if e['type'] == 'tool_started'] == ['0', '1']
        assert [e['payload']['tool_call_id'] for e in events if e['type'] == 'tool_completed'] == ['0', '1']
        observations = [e['payload']['event'] for e in events if e['type'] == 'observation']
        identities = lambda kind: [e['attributes']['execution_id'] for e in observations if e['event_type'] == kind]
        assert len(set(identities('tool.intent'))) == 2
        assert identities('tool.intent') == identities('tool.started') == identities('tool.finished')
        restored = store.load(journal.session_id)
        assert not restored.indeterminate_tools
        results = [block['tool_use_id'] for message in restored.messages if message['role'] == 'user'
                   and isinstance(message['content'], list) for block in message['content'] if block.get('type') == 'tool_result']
        assert results == ['0', '1']
    finally:
        await conversation.runtime_close()
