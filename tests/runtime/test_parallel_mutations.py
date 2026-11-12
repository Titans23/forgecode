'''Real file effects under the ordered parallel scheduler.'''
import asyncio
import os
import threading

import pytest

from forge.file_mutations import FileMutationLocks
from forge.permissions.policy import PermissionManager
from forge.runtime.executor import ToolExecutor
from forge.runtime.state import ToolCall
from forge.runtime.tool_batch import schedule_calls
from forge.tools.base import ToolRegistry
from forge.tools.filesystem import ReadFileTool, WriteFileTool


async def until(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(.005)


def executor(root, tools, **kwargs):
    registry = ToolRegistry(tools)
    return ToolExecutor(registry, PermissionManager(root, mode='auto', load_stored_rules=False), **kwargs)


async def consume(executor, calls, *, checkpoint_id=None):
    completed = {}
    async def prepare(call):
        return await executor.prepare(call, checkpoint_id=checkpoint_id)
    async with asyncio.timeout(20):
        _ = [event async for event in schedule_calls(calls, executor, prepare, limit=10, completed=completed)]
    return completed


@pytest.mark.parametrize('target', ['a.txt', ' leading.txt'])
def test_different_files_overlap_same_file_waits_through_settlement_and_checkpoint(tmp_path, target):
    from forge.hooks import HookOutcome
    from forge.runtime.workspace import WorkspaceTracker
    from forge.sessions.checkpoint import CheckpointStore
    async def run():
        started, finished = [], []
        releases = {value: asyncio.Event() for value in ('first', 'second', 'independent')}
        settle = asyncio.Event()
        class Write(WriteFileTool):
            async def execute(self, args):
                started.append(args.content)
                await asyncio.wait_for(releases[args.content].wait(), 10)
                result = await super().execute(args)
                finished.append(args.content)
                return result
        async def hook(event):
            if event.name == 'AfterFileEdit' and event.arguments['content'] == 'first':
                await asyncio.wait_for(settle.wait(), 10)
            return HookOutcome()
        store = CheckpointStore(tmp_path, tmp_path / 'private')
        checkpoint = store.begin()
        tracker = WorkspaceTracker(tmp_path)
        tracker.full_inventory = False
        runner = executor(tmp_path, [Write(tmp_path)], hook_runner=hook, checkpoint_store=store, workspace_tracker=tracker)
        calls = [ToolCall(i, str(i), 'write_file', {'path': path, 'content': value})
                 for i, (path, value) in enumerate(((target, 'first'), ('./' + target, 'second'), ('b.txt', 'independent')))]
        task = asyncio.create_task(consume(runner, calls, checkpoint_id=checkpoint))
        await until(lambda: len(started) == 2)
        assert started == ['first', 'independent']
        releases['independent'].set()
        releases['first'].set()
        await until(lambda: len(finished) == 2)
        assert 'second' not in started
        settle.set()
        await until(lambda: 'second' in started)
        releases['second'].set()
        outcomes = await task
        assert all(item.result.success for item in outcomes.values())
        assert (tmp_path / target).read_text() == 'second'
        assert (tmp_path / 'b.txt').read_text() == 'independent'
        assert {target, 'b.txt'} <= set(tracker.changed_paths)
        assert set(store.restore(checkpoint)) == {target, 'b.txt'}
        assert not (tmp_path / target).exists() and not (tmp_path / 'b.txt').exists()
        assert runner.registry.file_mutations._entries == {}
    asyncio.run(run())


def test_actual_helper_edits_overlap_and_keep_observed_versions(tmp_path):
    from forge.api import HarnessSession, SessionOptions
    from forge.config import ForgeConfig
    class NoModel:
        model = 'unused-offline'
        async def stream(self, *args, **kwargs):
            raise AssertionError('No model request expected')
            yield
        async def aclose(self):
            pass
    async def run():
        root = tmp_path / 'project'
        root.mkdir()
        handle = await HarnessSession.open(SessionOptions(root=root, data_root=tmp_path / 'private',
            config=ForgeConfig(api_key='offline', model_id='unused-offline')),
            model_client_factory=lambda *args, **kwargs: NoModel())
        runner = handle.conversation.tool_executor
        observer = runner.backend.observer
        request = observer.request
        started, release = [], asyncio.Event()
        async def gated(operation, **values):
            if operation == 'tool' and values.get('name') == 'write_file':
                started.append(values['arguments']['path'])
                if len(started) < 2:
                    await asyncio.wait_for(release.wait(), 10)
                else:
                    release.set()
            return await request(operation, **values)
        observer.request = gated
        try:
            results = await consume(runner, [ToolCall(i, str(i), 'write_file', {'path': path, 'content': 'first'})
                                            for i, path in enumerate(('a.txt', 'b.txt'))])
            assert all(item.result.success for item in results.values()), results
            # Both helper results must update their own observation; a second
            # write can use those versions, while an external edit is refused.
            results = await consume(runner, [ToolCall(i, 'next-' + str(i), 'write_file', {'path': path, 'content': 'second'})
                                            for i, path in enumerate(('a.txt', './a.txt'))])
            assert all(item.result.success for item in results.values()), results
            (root / 'a.txt').write_text('external')
            stale = await runner.execute(ToolCall(0, 'stale', 'write_file', {'path': 'a.txt', 'content': 'overwrite'}))
            assert stale.result.error.code == 'STALE_FILE'
            assert (root / 'a.txt').read_text() == 'external'
            assert not runner.backend.file_mutations._entries
        finally:
            await handle.close()
        assert handle.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_read_write_groups_form_barriers(tmp_path):
    async def run():
        started, release = [], asyncio.Event()
        class Read(ReadFileTool):
            async def execute(self, args):
                started.append('read')
                await release.wait()
                return await super().execute(args)
        class Write(WriteFileTool):
            async def execute(self, args):
                started.append('write')
                return await super().execute(args)
        (tmp_path / 'a').write_text('old')
        runner = executor(tmp_path, [Read(tmp_path), Write(tmp_path)])
        task = asyncio.create_task(consume(runner, [ToolCall(0, 'read', 'read_file', {'path': 'a'}),
            ToolCall(1, 'write', 'write_file', {'path': 'a', 'content': 'new'}),
            ToolCall(2, 'after', 'read_file', {'path': 'a'})]))
        await until(lambda: bool(started))
        assert started == ['read']
        release.set()
        results = await task
        assert started == ['read', 'write', 'read']
        assert 'old' in results['read'].result.content and 'new' in results['after'].result.content
    asyncio.run(run())


@pytest.mark.parametrize('alias_kind', ['relative', 'hardlink', 'case'])
def test_aliases_share_a_lease_and_cancelled_waiters_release_entries(tmp_path, alias_kind):
    path = tmp_path / 'target.txt'
    path.write_text('old')
    if alias_kind == 'hardlink':
        alias = tmp_path / 'other.txt'
        os.link(path, alias)
    elif alias_kind == 'case':
        if os.name != 'nt':
            pytest.skip('Case-insensitive Windows path contract')
        alias = tmp_path / 'TARGET.TXT'
    else:
        alias = tmp_path / '.' / 'target.txt'
    async def run():
        locks = FileMutationLocks()
        release = await locks.acquire([path])
        waiter = asyncio.create_task(locks.acquire([alias]))
        await asyncio.sleep(0)
        assert not waiter.done()
        unrelated = await locks.acquire([tmp_path / 'independent'])
        unrelated()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release()
        final = await locks.acquire([alias, path])
        final()
        assert not locks._entries
    asyncio.run(run())


def test_cancelled_thread_write_keeps_lease_until_actual_write_finishes(tmp_path):
    entered, release = threading.Event(), threading.Event()
    async def run():
        started = []
        class Write(WriteFileTool):
            def _execute_sync(self, args):
                started.append(args.content)
                if args.content == 'first':
                    entered.set()
                    assert release.wait(10)
                return super()._execute_sync(args)
        runner = executor(tmp_path, [Write(tmp_path)])
        first = asyncio.create_task(runner.execute(ToolCall(0, 'first', 'write_file', {'path': 'a', 'content': 'first'})))
        await until(entered.is_set)
        first.cancel()
        second = asyncio.create_task(runner.execute(ToolCall(1, 'second', 'write_file', {'path': 'a', 'content': 'second'})))
        try:
            await asyncio.sleep(.03)
            assert not first.done() and started == ['first']
        finally:
            release.set()
        outcomes = await asyncio.gather(first, second)
        assert outcomes[0].record.status == 'indeterminate'
        assert outcomes[1].result.success
        assert (tmp_path / 'a').read_text() == 'second'
        assert not runner.registry.file_mutations._entries
    asyncio.run(run())


@pytest.mark.parametrize('cancel_middle', [False, True])
def test_multifile_waiter_reserves_all_targets_before_later_edits(tmp_path, cancel_middle):
    async def run():
        locks = FileMutationLocks()
        first = await locks.acquire([tmp_path / 'a'])
        middle = asyncio.create_task(locks.acquire([tmp_path / 'a', tmp_path / 'b']))
        await asyncio.sleep(0)
        last = asyncio.create_task(locks.acquire([tmp_path / 'b']))
        await asyncio.sleep(0)
        assert not middle.done() and not last.done()
        if cancel_middle:
            middle.cancel()
            with pytest.raises(asyncio.CancelledError):
                await middle
            assert not last.done(), 'Cancellation must not allow overtaking the active writer'
            first()
        else:
            first()
            release_middle = await asyncio.wait_for(middle, 2)
            assert not last.done()
            release_middle()
        release_last = await asyncio.wait_for(last, 2)
        release_last()
        assert not locks._entries
    asyncio.run(run())


def test_real_multifile_patches_use_parsed_paths_and_do_not_deadlock(tmp_path):
    from forge.tools.patch import ApplyPatchTool
    async def run():
        for name in ('a space.txt', 'b.txt'):
            (tmp_path / name).write_text('old\n')
        runner = executor(tmp_path, [ApplyPatchTool(tmp_path)])
        patch = '--- "a/a space.txt"\n+++ "b/a space.txt"\n@@ -1 +1 @@\n-old\n+new\n'
        patch += '--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-old\n+new\n'
        next_patch = ('*** Begin Patch\n*** Update File: b.txt\n@@\n-new\n+final\n'
                      '*** Update File: a space.txt\n@@\n-new\n+final\n*** End Patch')
        results = await consume(runner, [ToolCall(0, 'first', 'apply_patch', {'patch': patch}),
                                        ToolCall(1, 'second', 'apply_patch', {'patch': next_patch})])
        assert all(item.result.success for item in results.values()), results
        assert all((tmp_path / name).read_text() == 'final\n' for name in ('a space.txt', 'b.txt'))
        assert not runner.registry.file_mutations._entries
    asyncio.run(run())
