"""Deferred process lifecycle tests; injected outcomes are not OS evidence."""
import asyncio
from types import SimpleNamespace

import pytest

from forge.protocol import ContractError
from forge.runtime.state import ToolCall
from forge.sandbox.policy_backend import PolicyToolBackend
from forge.tools.base import ToolResult


def fixture(factory, *, validate=lambda *args: None):
    async def close_files():
        return {'state': 'clean'}
    client = SimpleNamespace(native=None, policy={'execution_mode': 'workspace-write'}, close=close_files)
    return PolicyToolBackend(client, factory), SimpleNamespace(validate=validate)


def test_invalid_command_never_prepares_process_boundary():
    async def factory():
        raise AssertionError('must not initialize')
    async def run():
        backend, registry = fixture(factory, validate=lambda *args: ToolResult.fail('invalid_arguments', 'invalid'))
        result = await backend.execute(ToolCall(0, 'invalid', 'run_command', {}), registry)
        assert not result.success and result.error.code == 'invalid_arguments'
        assert backend._opening is None
        assert (await backend.close())['state'] == 'clean'
    asyncio.run(run())


@pytest.mark.parametrize('cleanup', ['clean', 'unknown'])
def test_native_failure_is_cached_without_host_fallback_and_cleanup_remains_honest(cleanup):
    async def run():
        attempts = []
        async def factory():
            attempts.append(True)
            error = ContractError('owned unavailable fixture', kind='SANDBOX_UNAVAILABLE')
            error.cleanup_report = {'state': cleanup}
            raise error
        backend, registry = fixture(factory)
        for name in ('run_command', 'verify', 'git_status'):
            result = await backend.execute(ToolCall(0, name, name, {}), registry)
            assert not result.success and result.error.code == 'SANDBOX_UNAVAILABLE'
        assert attempts == [True]
        first, second = await asyncio.gather(backend.close(), backend.close())
        assert first == second and first['state'] == cleanup
    asyncio.run(run())


def test_cancelled_first_command_and_concurrent_close_never_admit_late_execution():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        closed = []
        class Prepared:
            async def execute(self, *args):
                raise AssertionError('cancelled command must not execute')
            async def close(self):
                if not closed:
                    closed.append(True)
                return {'state': 'clean'}
        async def factory():
            started.set()
            await release.wait()
            return Prepared()
        backend, registry = fixture(factory)
        command = asyncio.create_task(backend.execute(ToolCall(0, 'run', 'run_command', {}), registry))
        await asyncio.wait_for(started.wait(), 1)
        command.cancel()
        with pytest.raises(asyncio.CancelledError):
            await command
        closing = asyncio.create_task(backend.close())
        await asyncio.sleep(0)
        release.set()
        assert (await asyncio.wait_for(closing, 2))['state'] == 'clean'
        assert closed == [True]
        with pytest.raises(ContractError, match='closed'):
            await backend.prepare_process()
        result = await backend.execute(ToolCall(0, 'later', 'run_command', {}), registry)
        assert not result.success
    asyncio.run(run())


def test_concurrent_first_commands_share_one_prepared_backend():
    async def run():
        attempts, calls = [], []
        class Prepared:
            async def execute(self, call, registry):
                calls.append(call.id)
                return ToolResult.ok('injected execution')
            async def close(self):
                return {'state': 'clean'}
        async def factory():
            attempts.append(True)
            await asyncio.sleep(0)
            return Prepared()
        backend, registry = fixture(factory)
        results = await asyncio.gather(*(backend.execute(ToolCall(0, str(i), 'run_command', {}), registry)
                                         for i in range(2)))
        assert all(result.success for result in results)
        assert attempts == [True] and set(calls) == {'0', '1'}
        assert (await backend.close())['state'] == 'clean'
    asyncio.run(run())
