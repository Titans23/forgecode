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


@pytest.mark.parametrize('early_exit', [True, False])
def test_worker_launch_acknowledgement_and_submission(tmp_path, monkeypatch, early_exit):
    from forge.release import processes

    root, control, workspace, policy = fixture(tmp_path)
    policy['execution_mode'] = 'full-access'
    original = processes.worker_argv

    def wrapped(*args, **kwargs):
        command = original(*args, **kwargs)
        if sys.platform == 'win32':
            # Windows gates the suspended worker with a Job rather than a ready pipe.
            return [str(tmp_path / 'missing-worker.exe')] if early_exit else command
        if kwargs.get('ready_fd') is None:
            return command
        program = ('raise SystemExit(1)' if early_exit else
            'import os,sys,time; time.sleep(5.1); os.execv(sys.argv[1], sys.argv[1:])')
        return [sys.executable, '-c', program, *command]

    async def run():
        client = FileWorkerClient(workspace, policy, local_control=control)
        command = ToolCall(0, 'launch', 'run_command', {'command': 'echo executed >> marker'})
        backend = FileToolBackend(client)
        registry = create_default_registry(root)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(processes, 'worker_argv', wrapped)
                async with asyncio.timeout(4 if early_exit else 18):
                    result = await backend.execute(command, registry)
            if early_exit:
                assert result.error.code == 'HELPER_NOT_STARTED'
                assert result.error.details['command_started'] is False
                assert result.error.details['cleanup']['state'] == 'clean'
                assert not (root / 'marker').exists()
                result = await backend.execute(command, registry)
            assert result.success and (root / 'marker').read_text().split() == ['executed']
        finally:
            await backend.close()
    asyncio.run(run())


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


@pytest.mark.parametrize('failure', ['exit', 'exception', 'malformed'])
def test_helper_failure_preserves_bounded_redacted_diagnostics(tmp_path, monkeypatch, failure):
    import json
    root, control, workspace, policy = fixture(tmp_path)
    policy['execution_mode'] = 'full-access'
    if failure == 'exit':
        program = ("import sys,os\nsys.stdin.buffer.read()\n"
            "sys.stderr.write('token=synthetic-private ' + 'x'*10000)\n"
            "sys.stderr.flush()\nos._exit(17)\n")
    elif failure == 'exception':
        program = ("from forge.sandbox import file_worker as worker\n"
            "async def broken(request):\n"
            " try: raise OSError('token=synthetic-private ' + 'detail '*500)\n"
            " except OSError as cause: raise RuntimeError('operation failed') from cause\n"
            "worker.execute=broken\nraise SystemExit(worker.main())\n")
    else:
        program = "import sys\nsys.stdin.buffer.read()\nsys.stderr.write('broken frame')\nprint('{broken')\n"
    monkeypatch.setattr('forge.sandbox.file_client.worker_argv',
                        lambda: [sys.executable, '-I', '-B', '-c', program])

    async def run():
        from forge.process_output import process_output
        backend = FileToolBackend(FileWorkerClient(workspace, policy, local_control=control))
        streamed = []
        token = process_output.set(lambda stream, chunk: streamed.append(chunk))
        try:
            result = await backend.execute(ToolCall(0, 'broken', 'run_command',
                {'command': 'not executed by the fault-injected helper'}), create_default_registry(root))
            assert not result.success and result.error.code == 'INDETERMINATE'
            detail = result.error.details
            assert detail['cleanup']['state'] == 'clean'
            if failure == 'exit':
                assert detail['worker_exit_code'] == 17
                assert len(detail['worker_stderr']) <= 4096 and detail['stderr_truncated']
                assert '[REDACTED]' in detail['worker_stderr']
            elif failure == 'exception':
                assert detail['worker_exit_code'] == 2 and detail['stage'] == 'file_worker'
                assert [item['type'] for item in detail['exception']] == ['RuntimeError', 'OSError']
                assert len(detail['exception'][1]['message']) <= 1024
            else:
                # The owner may terminate the worker as soon as the invalid
                # frame arrives; preserve the actual code, including cleanup.
                assert isinstance(detail['worker_exit_code'], int)
                assert detail['worker_stderr'] == 'broken frame'
                assert detail['exception'][0]['type'] == 'ContractError'
            assert 'synthetic-private' not in json.dumps(detail)
            assert not streamed, 'Helper stderr must not bypass diagnostic redaction into model output'
        finally:
            process_output.reset(token)
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


@pytest.mark.parametrize('interruptions', [1, 2])
def test_cancellation_during_cleanup_keeps_the_actual_receipt(tmp_path, monkeypatch, interruptions):
    from forge.sandbox.process_owner import LocalProcessOwner
    from forge.tool_types import ToolExecutionCancelled
    root, control, workspace, policy = fixture(tmp_path)
    original = LocalProcessOwner.close

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def delayed_close(owner):
            calls.append(owner)
            entered.set()
            await release.wait()
            return await original(owner)
        monkeypatch.setattr(LocalProcessOwner, 'close', delayed_close)
        client = FileWorkerClient(workspace, policy, local_control=control)
        request = asyncio.create_task(client.request('tool', name='write_file',
            arguments={'path':'written.txt', 'content':'observed effect'}))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            for _ in range(interruptions):
                request.cancel()
                await asyncio.sleep(.01)
                assert not request.done(), 'Cancellation must settle the already running cleanup.'
            release.set()
            with pytest.raises(ToolExecutionCancelled) as stopped:
                await asyncio.wait_for(request, 5)
            assert stopped.value.cleanup['state'] == 'clean'
            assert stopped.value.details['worker_exit_code'] == 0
            assert len(calls) == 1 and not client._owners and not client._requests
            assert (root/'written.txt').read_text() == 'observed effect'
        finally:
            release.set()
            if not request.done(): request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            assert (await client.close())['state'] == 'clean'
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


def test_cancelled_close_waiter_does_not_reopen_a_queued_helper(tmp_path, monkeypatch):
    root, control, workspace, policy = fixture(tmp_path)

    async def run():
        client = FileWorkerClient(workspace, policy, local_control=control)
        from forge.sandbox.process_owner import LocalProcessOwner
        entered, release = asyncio.Event(), asyncio.Event()
        original_start = LocalProcessOwner.start
        async def pending_start(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original_start(*args, **kwargs)
        monkeypatch.setattr(LocalProcessOwner, 'start', pending_start)
        request = asyncio.create_task(client.request('tool', name='write_file',
            arguments={'path': 'queued.txt', 'content': 'must not execute'}))
        await asyncio.wait_for(entered.wait(), 5)
        closer = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        closer.cancel()
        await asyncio.gather(closer, return_exceptions=True)
        release.set()
        result = await asyncio.gather(request, return_exceptions=True)
        assert isinstance(result[0], ContractError), result
        assert (await client.close())['state'] == 'clean'
        assert not (root / 'queued.txt').exists()

    asyncio.run(run())
