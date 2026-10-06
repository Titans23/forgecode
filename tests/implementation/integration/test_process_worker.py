"""Real helper commands, bounded flood collection and nested process cancellation."""
import asyncio
from hashlib import sha256
import sys

from test_file_worker import fixture
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
