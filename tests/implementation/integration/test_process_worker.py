"""Real helper commands, bounded flood collection and nested process cancellation."""
import asyncio
from hashlib import sha256
import sys

import pytest

from test_file_worker import fixture
from forge.application.models import ContractError
from forge.sandbox.file_client import FileWorkerClient
from forge.sandbox.tool_backend import FileToolBackend
from forge.tools import create_default_registry
from forge.runtime.state import ToolCall


def test_real_helper_verification_runs_program_with_original_evidence_fields(tmp_path):
    root, control, workspace, policy = fixture(tmp_path)
    (root / 'check.py').write_text('import json; print(json.dumps({"answer":42}))')
    async def run():
        client = FileWorkerClient(workspace, policy, local_control=control)
        backend = FileToolBackend(client)
        registry = create_default_registry(root)
        registry.implementation('verify').tracker.revision = 7
        registry.implementation('verify').tracker.environment_epoch = 3
        try:
            result = await backend.execute(ToolCall(0, 'check', 'verify', {
                'command': f'"{sys.executable}" check.py', 'covers': ['program computes answer'],
                'output_checks': [{'key': 'answer', 'operator': 'eq', 'expected': 42,
                    'requirement': 'answer is 42', 'expected_source': 'portable fixture'}]}), registry)
            assert result.success and result.metadata['exit_code'] == 0
            assert result.metadata['workspace_revision'] == 7 and result.metadata['environment_epoch'] == 3
            assert result.metadata['verification_id'] and result.metadata['evidence_valid']
            assert result.metadata['execution_boundary'] == 'local-trusted-file-worker'
        finally:
            assert (await backend.close())['state'] == 'clean'
    asyncio.run(run())


def test_flooding_helper_cancel_reclaims_actual_nested_process_and_stops_writes(tmp_path):
    root, control, workspace, policy = fixture(tmp_path)
    (root / 'child.py').write_text("import pathlib,time\np=pathlib.Path('heartbeat')\nwhile True:\n p.write_text(str(time.time())); time.sleep(.02)\n")
    (root / 'flood.py').write_text("import pathlib,subprocess,sys\np=subprocess.Popen([sys.executable,'child.py'])\npathlib.Path('started').write_text(str(p.pid))\nwhile True:\n sys.stdout.write('x'*65536); sys.stdout.flush()\n")
    async def run():
        client = FileWorkerClient(workspace, policy, local_control=control)
        task = asyncio.create_task(client.request('tool', name='run_command',
            arguments={'command': f'"{sys.executable}" flood.py', 'timeout_seconds': 30}))
        try:
            async with asyncio.timeout(8):
                while not (root / 'heartbeat').exists():
                    if task.done():
                        raise AssertionError(task.result())
                    await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 4)
            report = await client.close()
            assert report['state'] == 'clean', report
            digest = sha256((root / 'heartbeat').read_bytes()).hexdigest()
            await asyncio.sleep(0.15)
            assert sha256((root / 'heartbeat').read_bytes()).hexdigest() == digest
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await client.close()
    asyncio.run(run())


def test_closed_file_client_cannot_launch_a_new_helper_or_write(tmp_path):
    root, control, workspace, policy = fixture(tmp_path)

    async def run():
        client = FileWorkerClient(workspace, policy, local_control=control)
        report = await client.close()
        try:
            with pytest.raises(ContractError, match='closed'):
                await client.request('tool', name='write_file', arguments={'path': 'late.txt', 'content': 'late'})
            assert not (root / 'late.txt').exists()
            assert await client.close() == report
        finally:
            await client.close()

    asyncio.run(run())


def test_close_rejects_queued_write_and_reaps_the_actual_active_helper(tmp_path):
    root, control, workspace, policy = fixture(tmp_path)
    (root / 'heartbeat.py').write_text("import pathlib,time\np=pathlib.Path('heartbeat')\nwhile True:\n p.write_text(str(time.time())); time.sleep(.02)\n")

    async def run():
        client = FileWorkerClient(workspace, policy, local_control=control)
        active = asyncio.create_task(client.request('tool', name='run_command',
            arguments={'command': f'"{sys.executable}" heartbeat.py', 'timeout_seconds': 30}))
        queued = None
        try:
            async with asyncio.timeout(10):
                while not (root / 'heartbeat').exists():
                    if active.done():
                        raise AssertionError(active.result())
                    await asyncio.sleep(.01)
            queued = asyncio.create_task(client.request('tool', name='write_file',
                arguments={'path': 'queued.txt', 'content': 'must not execute'}))
            await asyncio.sleep(0)
            reports = await asyncio.wait_for(asyncio.gather(client.close(), client.close()), 8)
            outcomes = await asyncio.gather(active, queued, return_exceptions=True)
            assert isinstance(outcomes[1], ContractError), outcomes
            assert not (root / 'queued.txt').exists()
            assert reports[0] == reports[1] and reports[0]['state'] == 'clean'
            heartbeat = (root / 'heartbeat').read_bytes()
            await asyncio.sleep(.15)
            assert (root / 'heartbeat').read_bytes() == heartbeat
            reports[0]['reports'].clear()
            assert (await client.close())['reports'], 'Callers must not mutate the cached cleanup result'
        finally:
            for task in (active, queued):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(task for task in (active, queued) if task is not None), return_exceptions=True)
            await client.close()

    asyncio.run(run())


def test_cancelled_close_waiter_does_not_reopen_a_queued_helper(tmp_path):
    root, control, workspace, policy = fixture(tmp_path)

    async def run():
        client = FileWorkerClient(workspace, policy, local_control=control)
        # Hold the existing admission mutex to place the real write request in
        # the queue. No helper or native sandbox behavior is replaced.
        await client._lock.acquire()
        request = asyncio.create_task(client.request('tool', name='write_file',
            arguments={'path': 'queued.txt', 'content': 'must not execute'}))
        await asyncio.sleep(0)
        closer = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        closer.cancel()
        await asyncio.gather(closer, return_exceptions=True)
        client._lock.release()
        result = await asyncio.gather(request, return_exceptions=True)
        assert isinstance(result[0], ContractError), result
        assert (await client.close())['state'] == 'clean'
        assert not (root / 'queued.txt').exists()

    asyncio.run(run())
