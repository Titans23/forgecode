'''Real owned background commands; no model calls and no native isolation claim.'''
import asyncio
import json
import sys

import pytest

from forge.api import HarnessSession, SessionOptions
from forge.config import ForgeConfig
from forge.runtime.state import ToolCall


class NoModel:
    model = 'unused-offline-model'
    async def stream(self, *args, **kwargs):
        raise AssertionError('This test must not call a model')
        yield
    async def aclose(self):
        pass


async def session(tmp_path, model=None):
    root = tmp_path / 'project'
    root.mkdir()
    return await HarnessSession.open(SessionOptions(root=root, data_root=tmp_path / 'private',
        config=ForgeConfig(api_key='offline', model_id='unused-offline-model')),
        model_client_factory=lambda *args, **kwargs: model or NoModel())


def command(code):
    return f'"{sys.executable}" -u -c "{code}"'


def test_background_command_streams_before_completion_and_preserves_one_tool_lifecycle(tmp_path):
    async def run():
        handle = await session(tmp_path)
        conversation = handle.conversation
        try:
            outcome = await conversation.tool_executor.execute(ToolCall(0, 'start', 'run_command', {
                'command': command("import time; print('first', flush=True); time.sleep(2); print('last', flush=True)"),
                'run_in_background': True, 'timeout_seconds': 15}))
            assert outcome.result.success and outcome.result.metadata['background']
            identity = outcome.result.metadata['job_id']
            async with asyncio.timeout(15):
                while True:
                    early = await conversation.command_jobs.output(identity, wait_seconds=1)
                    if 'first' in early.content:
                        break
            assert early.metadata['state'] == 'running' and 'last' not in early.content
            cursor = early.metadata['next_cursor']
            # Replaying old output must not turn completion waits into a busy loop.
            waiting = asyncio.create_task(conversation.command_jobs.output(identity, wait_for='completion', wait_seconds=15))
            await asyncio.sleep(.05)
            assert not waiting.done()
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError): await waiting
            assert conversation.command_jobs.jobs[identity].state == 'running'
            completed = await conversation.command_jobs.output(identity, wait_for='completion', wait_seconds=15)
            assert completed.metadata['state'] == 'completed' and completed.metadata['wait_reason'] == 'completed'
            final = await conversation.tool_executor.execute(ToolCall(1, 'read-output', 'job_output', {
                'job_id': identity, 'after': cursor}))
            assert final.result.metadata['state'] == 'completed'
            assert 'last' in final.result.content and 'first' not in final.result.content
            empty = await conversation.command_jobs.output(identity, after=final.result.metadata['next_cursor'])
            assert empty.content == ''
            rows = [json.loads(line) for line in conversation.session_journal.path.read_text(encoding='utf-8').splitlines()]
            assert sum(row['type'] == 'tool_started' and row['payload']['tool_call_id'] == 'start' for row in rows) == 1
            assert sum(row['type'] == 'tool_completed' and row['payload']['tool_call_id'] == 'start' for row in rows) == 1
            assert sum(row['type'] == 'job_finished' for row in rows) == 1
        finally:
            await handle.close()
        assert handle.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_owned_job_cancel_prevents_late_write_and_blocks_permission_switch(tmp_path):
    async def run():
        handle = await session(tmp_path)
        conversation = handle.conversation
        try:
            outcome = await conversation.tool_executor.execute(ToolCall(0, 'start', 'run_command', {
                'command': command("import time; from pathlib import Path; print('ready', flush=True); time.sleep(30); Path('late.txt').write_text('late')"),
                'run_in_background': True, 'timeout_seconds': 45}))
            identity = outcome.result.metadata['job_id']
            async with asyncio.timeout(15):
                while 'ready' not in (await conversation.command_jobs.output(identity, wait_seconds=1)).content:
                    pass
            with pytest.raises(RuntimeError, match='command jobs'):
                await conversation.select_permissions('read-only')
            killed = await conversation.tool_executor.execute(ToolCall(1, 'kill', 'job_kill', {'job_id': identity}))
            assert killed.result.success and killed.result.metadata['state'] == 'cancelled'
            assert not conversation.command_jobs.running
            assert not (tmp_path / 'project' / 'late.txt').exists()
            assert not conversation.tool_executor.backend.observer._owners
            unknown = await conversation.command_jobs.cancel('job-from-another-session')
            assert unknown.error.code == 'unknown_job'
        finally:
            await handle.close()
        assert handle.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_cancelling_one_job_allows_the_model_to_continue(tmp_path):
    from forge.runtime.state import ModelToolCallCompleted, ModelResponseCompleted, ModelTextDelta
    async def run():
        class Model(NoModel):
            calls = 0
            async def stream(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    yield ModelToolCallCompleted(ToolCall(0, 'background', 'run_command', {
                        'command': command("import time; print('ready', flush=True); time.sleep(30)"),
                        'run_in_background': True, 'timeout_seconds': 40}))
                elif self.calls == 2:
                    jobs = handle.conversation.command_jobs
                    identity = next(iter(jobs.jobs))
                    async with asyncio.timeout(10):
                        cursor = 0
                        while True:
                            output = await jobs.output(identity, after=cursor, wait_seconds=1)
                            if 'ready' in output.content:
                                break
                            assert output.metadata['state'] in {'queued', 'running'}
                            cursor = output.metadata['next_cursor']
                    yield ModelToolCallCompleted(ToolCall(0, 'stop', 'job_kill', {'job_id': identity}))
                elif self.calls == 3:
                    yield ModelToolCallCompleted(ToolCall(0, 'next', 'run_command', {
                        'command': command("print('continued')")}))
                else:
                    yield ModelTextDelta('Continued after cancelling only the job.')
                yield ModelResponseCompleted('end_turn' if self.calls == 4 else 'tool_use')
        model = Model()
        handle = await session(tmp_path, model)
        try:
            result = await asyncio.wait_for(handle.run('Start, cancel, then run another command.'), 25)
            assert result.status == 'completed' and model.calls == 4
            jobs = handle.conversation.command_jobs
            assert not jobs.running and next(iter(jobs.jobs.values())).state == 'cancelled'
            rows = handle.conversation.session_store.load(handle.conversation.session_journal.session_id)
            assert not rows.pending_inputs
        finally:
            await handle.close()
        assert handle.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_job_cancellation_with_unconfirmed_cleanup_remains_indeterminate(tmp_path):
    from forge.permissions.policy import PermissionManager
    from forge.runtime.agent_loop import Conversation
    from forge.tools.base import ToolRegistry
    from forge.tools.shell import RunCommandTool
    from forge.tool_types import ToolExecutionCancelled
    async def run():
        entered = asyncio.Event()
        class Backend:
            async def execute(self, call, registry):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    raise ToolExecutionCancelled({'state': 'unknown'}) from None
        c = Conversation(client=NoModel(), registry=ToolRegistry([RunCommandTool(tmp_path)]),
            context_root=tmp_path, include_task_tools=False, tool_backend=Backend(),
            permission_manager=PermissionManager(tmp_path, mode='never', execution_mode='full-access', load_stored_rules=False))
        outcome = await c.tool_executor.execute(ToolCall(0, 'start', 'run_command', {
            'command': 'unexecuted-fixture', 'run_in_background': True}))
        await asyncio.wait_for(entered.wait(), 2)
        stopped = await c.command_jobs.cancel(outcome.result.metadata['job_id'])
        assert not stopped.success and stopped.error.code == 'INDETERMINATE'
        await c.command_jobs.close()
    asyncio.run(run())


def test_immediate_job_cancel_and_close_do_not_leave_queued_state(tmp_path):
    from forge.runtime.jobs import CommandJobs
    async def run():
        jobs = CommandJobs(limit=1)
        async def never_start():
            raise AssertionError('Cancelled job must not run')
        call = ToolCall(0, 'start', 'run_command', {'command': 'unused'})
        identity = jobs.start(call, never_start, owner=object())
        result = await jobs.cancel(identity)
        assert result.metadata['state'] == 'cancelled'
        assert not jobs.running
        await jobs.close()
        with pytest.raises(RuntimeError, match='closing'):
            jobs.start(call, never_start, owner=object())
    asyncio.run(run())


@pytest.mark.parametrize('accept', [True, False])
def test_background_grant_is_once_and_lives_until_owned_job_ends(tmp_path, accept):
    from forge.permissions.policy import ApprovalResponse, PermissionManager
    from forge.runtime.agent_loop import Conversation
    from forge.tools.base import ToolRegistry, ToolResult
    from forge.tools.shell import RunCommandTool
    async def run():
        release = asyncio.Event()
        entered = asyncio.Event()
        prompts, opened, closed = [], [], []
        class Wider:
            async def execute(self, call, registry):
                assert 'sandbox_permissions' not in call.arguments
                assert 'run_in_background' not in call.arguments
                entered.set()
                await release.wait()
                return ToolResult.ok('completed', metadata={'exit_code': 0})
        class Backend:
            def open_approved(self, mode):
                opened.append(mode)
                return Wider()
            async def close_approved(self, backend):
                closed.append(backend)
                return {'state': 'clean'}
            async def execute(self, call, registry):
                raise AssertionError('The default restricted backend must not run the widened command')
            async def close(self):
                return {'state': 'clean'}
        async def approve(request):
            prompts.append(request)
            return ApprovalResponse('allow_once' if accept else 'deny')
        permissions = PermissionManager(tmp_path, mode='ask', execution_mode='read-only',
            approval_handler=approve, load_stored_rules=False)
        conversation = Conversation(client=NoModel(), registry=ToolRegistry([RunCommandTool(tmp_path)]),
            context_root=tmp_path, include_task_tools=False, permission_manager=permissions, tool_backend=Backend())
        try:
            outcome = await conversation.tool_executor.execute(ToolCall(0, 'start', 'run_command', {
                'command': 'echo allowed', 'run_in_background': True,
                'sandbox_permissions': 'danger-full-access', 'justification': 'One owned fixture command.'}))
            assert len(prompts) == 1 and permissions.execution_mode == 'read-only'
            if not accept:
                assert outcome.result.error.code == 'permission_denied'
                assert not opened and not conversation.command_jobs.jobs
                return
            identity = outcome.result.metadata['job_id']
            await asyncio.wait_for(entered.wait(), 5)
            assert opened == ['full-access'] and not closed
            release.set()
            await asyncio.wait_for(conversation.command_jobs.jobs[identity].task, 5)
            assert len(closed) == 1 and len(prompts) == 1
            assert permissions.execution_mode == 'read-only'
        finally:
            await conversation.runtime_close()
    asyncio.run(run())


def test_cancelling_the_model_turn_drains_its_background_command(tmp_path):
    from contextlib import aclosing
    from forge.runtime.state import ModelToolCallCompleted, ModelResponseCompleted
    async def run():
        waiting = asyncio.Event()
        class Model(NoModel):
            calls = 0
            async def stream(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    yield ModelToolCallCompleted(ToolCall(0, 'background', 'run_command', {
                        'command': command("import time; print('ready', flush=True); time.sleep(30)"),
                        'run_in_background': True, 'timeout_seconds': 40}))
                    yield ModelResponseCompleted('tool_use')
                else:
                    waiting.set()
                    await asyncio.Event().wait()
        handle = await session(tmp_path, Model())
        conversation = handle.conversation
        async def consume():
            async with aclosing(conversation.stream('Start the independent command.')) as events:
                async for _ in events:
                    pass
        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(waiting.wait(), 15)
            identity = next(iter(conversation.command_jobs.jobs))
            async with asyncio.timeout(15):
                while 'ready' not in (await conversation.command_jobs.output(identity, wait_seconds=1)).content:
                    pass
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 10)
            assert not conversation.command_jobs.running
            assert not conversation.tool_executor.backend.observer._owners
            assert not conversation._running
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await handle.close()
    asyncio.run(run())


def test_streamed_output_caps_and_cursor_validation():
    from forge.runtime.jobs import CommandJob, CommandJobs
    async def run():
        jobs = CommandJobs()
        job = CommandJob('job-test', 'call', None, 'synthetic', state='completed')
        jobs.jobs[job.id] = job
        job.feed('stdout', b'a' * 70000)
        job.feed('stderr', '\u4e2d'.encode('utf-8')[:1])
        job.feed('stderr', '\u4e2d'.encode('utf-8')[1:])
        result = await jobs.output(job.id)
        assert job.characters <= 65536 and result.metadata['output_truncated']
        assert '\u4e2d' in result.content
        assert (await jobs.output(job.id, after=job.cursor + 1)).error.code == 'invalid_cursor'
    asyncio.run(run())


def test_completion_wait_respects_caller_deadline_without_stopping_job():
    from forge.runtime.jobs import CommandJob, CommandJobs
    from time import monotonic
    async def run():
        jobs = CommandJobs(caller_deadline=monotonic() + .05)
        job = CommandJob('owned', 'call', None, 'pending', state='running')
        jobs.jobs[job.id] = job
        job.feed('stdout', b'old output')
        result = await asyncio.wait_for(jobs.output(job.id, wait_for='completion', wait_seconds=3600), 1)
        assert result.metadata['wait_reason'] == 'caller_deadline'
        assert result.metadata['state'] == 'running' and 'old output' in result.content
        assert (await jobs.output(job.id, after=result.metadata['next_cursor'])).content == ''
    asyncio.run(run())


@pytest.mark.parametrize('structured_finish', [False, True])
def test_session_service_survives_answer_and_verification_but_not_session_close(tmp_path, structured_finish):
    import socket
    from forge.runtime.state import ModelToolCallCompleted, ModelResponseCompleted, ModelTextDelta
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    server = command(f"import socket,time; s=socket.socket(); s.bind(('127.0.0.1',{port})); s.listen(); print('READY',flush=True); time.sleep(60)")
    async def run():
        class Model(NoModel):
            calls = 0
            async def stream(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    yield ModelToolCallCompleted(ToolCall(0, 'service', 'run_command', {
                        'command': server, 'run_in_background': True, 'lifetime': 'session', 'timeout_seconds': None}))
                elif self.calls == 2:
                    jobs = handle.conversation.command_jobs
                    identity = next(iter(jobs.jobs))
                    async with asyncio.timeout(10):
                        cursor = 0
                        while True:
                            output = await jobs.output(identity, after=cursor, wait_seconds=1)
                            if 'READY' in output.content:
                                break
                            assert output.metadata['state'] in {'queued', 'running'}, output
                            cursor = output.metadata['next_cursor']
                    yield ModelToolCallCompleted(ToolCall(0, 'ready', 'verify', {
                        'command': command(f"import socket; s=socket.create_connection(('127.0.0.1',{port}),timeout=2); s.close()"),
                        'covers': ['The fixture service accepts a local connection.']}))
                elif self.calls == 3 and structured_finish:
                    yield ModelToolCallCompleted(ToolCall(0, 'finish', 'finish_task', {
                        'task_kind': 'answer', 'status': 'completed', 'summary': 'Service is ready.'}))
                else:
                    yield ModelTextDelta('Service is ready.')
                yield ModelResponseCompleted('tool_use' if self.calls < 3 or self.calls == 3 and structured_finish else 'end_turn')
        handle = await session(tmp_path, Model())
        try:
            result = await asyncio.wait_for(handle.run('Run the requested service until this session closes.'), 25)
            assert result.status == 'completed' and result.verification.success
            jobs = handle.conversation.command_jobs
            assert jobs.running and not jobs.pending_commands
            with socket.create_connection(('127.0.0.1', port), timeout=2):
                pass
            assert (await handle.run('Confirm the already running service.')).status == 'completed'
            with pytest.raises(RuntimeError, match='command jobs'):
                await handle.set_permissions('read-only')
        finally:
            await handle.close()
        assert handle.cleanup['state'] == 'clean' and not jobs.running
        # Windows may finish TCP socket rundown just after the last owned PID exits.
        async with asyncio.timeout(3):
            while True:
                try:
                    probe = socket.create_connection(('127.0.0.1', port), timeout=.2)
                except OSError:
                    break
                probe.close()
                await asyncio.sleep(.05)
        records = handle.conversation.session_store._read_records(handle.conversation.session_journal.path)
        assert any(r['type'] == 'job_retained' for r in records)
        ended = [handle.conversation.session_store._payload(r, handle.conversation.session_journal.path)
                 for r in records if r['type'] == 'job_finished']
        assert ended[-1]['state'] == 'cancelled' and ended[-1]['lifetime'] == 'session'
    asyncio.run(run())


@pytest.mark.parametrize('structured_finish', [False, True])
def test_both_completion_paths_wait_for_finite_jobs(tmp_path, structured_finish):
    from forge.runtime.state import ModelToolCallCompleted, ModelResponseCompleted, ModelTextDelta
    async def run():
        class Model(NoModel):
            calls = 0
            async def stream(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    yield ModelToolCallCompleted(ToolCall(0, 'finite', 'run_command', {
                        'command': command("import time; time.sleep(30)"), 'run_in_background': True, 'timeout_seconds': 40}))
                elif self.calls == 2:
                    if structured_finish:
                        yield ModelToolCallCompleted(ToolCall(0, 'early', 'finish_task', {
                            'task_kind': 'answer', 'status': 'completed', 'summary': 'Premature answer.'}))
                    else:
                        yield ModelTextDelta('Premature answer.')
                elif self.calls == 3:
                    assert 'Finite command jobs' in str(kwargs['messages'])
                    yield ModelToolCallCompleted(ToolCall(0, 'stop', 'job_kill', {
                        'job_id': next(iter(handle.conversation.command_jobs.jobs))}))
                else:
                    yield ModelTextDelta('Finite work stopped.')
                yield ModelResponseCompleted('end_turn' if self.calls >= 4 or self.calls == 2 and not structured_finish else 'tool_use')
        model = Model()
        handle = await session(tmp_path, model)
        try:
            result = await asyncio.wait_for(handle.run('Exercise the finite job completion boundary.'), 25)
            assert result.status == 'completed' and result.text == 'Finite work stopped.' and model.calls == 4
        finally:
            await handle.close()
    asyncio.run(run())
