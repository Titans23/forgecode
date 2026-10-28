"""Real Hook subprocess ownership, without model calls or sandbox claims."""
import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from forge.hooks.models import HookEvent, HookSpec
from forge.hooks.runner import run_hook_command


def hook_with_child(root: Path, mode: str) -> HookSpec:
    (root / 'child.py').write_text(
        "from pathlib import Path\n"
        "import time\n"
        "deadline = time.monotonic() + 10\n"
        "while not Path('stop').exists() and time.monotonic() < deadline:\n"
        "    with Path('heartbeat').open('ab') as stream: stream.write(b'x')\n"
        "    time.sleep(.02)\n",
        encoding='utf-8',
    )
    script = root / 'hook.py'
    script.write_text(
        "from pathlib import Path\n"
        "import json, subprocess, sys, time\n"
        "json.load(sys.stdin)\n"
        "subprocess.Popen([sys.executable, 'child.py'], stdin=subprocess.DEVNULL, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "while not Path('heartbeat').exists(): time.sleep(.01)\n"
        "if sys.argv[1] == 'normal':\n"
        "    print(json.dumps({'decision': 'allow'}))\n"
        "elif sys.argv[1] == 'quota':\n"
        "    sys.stdout.write('x' * 600001); sys.stdout.flush()\n"
        "    sys.stderr.write('x' * 600001); sys.stderr.flush()\n"
        "else:\n"
        "    deadline = time.monotonic() + 10\n"
        "    while not Path('stop').exists() and time.monotonic() < deadline: time.sleep(.02)\n",
        encoding='utf-8',
    )
    return HookSpec(id='owned-tree', command=(sys.executable, str(script), mode),
        timeout_seconds=2 if mode == 'timeout' else 8)


@pytest.mark.parametrize('mode', ['cancel', 'normal', 'timeout', 'quota'])
def test_hook_cleans_redirected_child_before_return(tmp_path, mode):
    async def run():
        task = asyncio.create_task(run_hook_command(tmp_path, hook_with_child(tmp_path, mode),
            HookEvent(name='PreToolUse', tool_name='read_file')))
        try:
            async with asyncio.timeout(8):
                while not (tmp_path / 'heartbeat').exists():
                    if task.done():
                        raise AssertionError(task.result())
                    await asyncio.sleep(.01)
            if mode == 'cancel':
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await task
                assert result.decision == ('allow' if mode == 'normal' else 'error')
                assert result.timed_out == (mode == 'timeout')
                if mode == 'quota':
                    assert 'exceeded 1000000 bytes' in result.reason
            before = (tmp_path / 'heartbeat').read_bytes()
            await asyncio.sleep(.15)
            assert (tmp_path / 'heartbeat').read_bytes() == before, 'Hook child is still running'
        finally:
            # Bound the pre-fix reproducer too; never leave its leaked fixture child running.
            (tmp_path / 'stop').touch()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(.15)
    asyncio.run(run())


def test_hook_preserves_protocol_environment_without_loaders_or_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('FORGE_HOOK_ORDINARY', 'ordinary-value')
    monkeypatch.setenv('FORGE_TEST_API_KEY', 'test-credential')
    monkeypatch.setenv('PYTHONPATH', 'untrusted-loader')
    monkeypatch.setenv('NODE_OPTIONS', 'untrusted-loader')
    code = (
        "import json, os, sys; payload = json.load(sys.stdin); "
        "keys = ['PATH', 'FORGE_HOOK_EVENT', 'FORGE_SESSION_ID', 'FORGE_TOOL_NAME', "
        "'FORGE_FILE_PATH', 'FORGE_HOOK_ORDINARY', 'FORGE_TEST_API_KEY', 'PYTHONPATH', 'NODE_OPTIONS']; "
        "print(json.dumps({'additional_context': json.dumps({'payload': payload, "
        "'environment': {key: os.environ.get(key) for key in keys}})}))"
    )
    spec = HookSpec(id='environment', command=(sys.executable, '-c', code))
    event = HookEvent(name='PreToolUse', session_id='session-test', tool_name='read_file',
        tool_call_id='call-test', paths=('note.txt',), arguments={'path': 'note.txt'})
    result = asyncio.run(run_hook_command(tmp_path, spec, event))

    assert result.decision == 'allow', result.reason
    observed = json.loads(result.additional_context)
    assert observed['payload']['tool'] == {'id': 'call-test', 'name': 'read_file', 'arguments': {'path': 'note.txt'}}
    assert observed['payload']['session_id'] == 'session-test'
    assert observed['environment'] == {
        'PATH': os.environ['PATH'], 'FORGE_HOOK_EVENT': 'PreToolUse', 'FORGE_SESSION_ID': 'session-test',
        'FORGE_TOOL_NAME': 'read_file', 'FORGE_FILE_PATH': 'note.txt', 'FORGE_HOOK_ORDINARY': 'ordinary-value',
        'FORGE_TEST_API_KEY': None, 'PYTHONPATH': None, 'NODE_OPTIONS': None,
    }


def test_successful_hook_refuses_unconfirmed_cleanup(tmp_path, monkeypatch):
    from forge.sandbox.process_owner import LocalProcessOwner

    close = LocalProcessOwner.close

    async def unconfirmed(owner):
        await close(owner)
        return {'state': 'unknown', 'reason': 'injected report failure after actual cleanup'}

    monkeypatch.setattr(LocalProcessOwner, 'close', unconfirmed)
    spec = HookSpec(id='unknown-cleanup', command=(sys.executable, '-c', "print('{\"decision\": \"allow\"}')"))
    result = asyncio.run(run_hook_command(tmp_path, spec, HookEvent(name='BeforeModelCall')))

    assert result.decision == 'error'
    assert 'cleanup could not be confirmed' in result.reason


@pytest.mark.parametrize('mode', ['cancel', 'timeout'])
def test_hook_closes_pipes_when_stdin_backpressure_prevents_output_reads(tmp_path, monkeypatch, mode):
    from forge.sandbox.process_owner import LocalProcessOwner

    owners = []
    start = LocalProcessOwner.start

    async def capture_owner(cls, *args, **kwargs):
        owner = await start(*args, **kwargs)
        owners.append(owner)
        return owner

    monkeypatch.setattr(LocalProcessOwner, 'start', classmethod(capture_owner))
    code = (
        "from pathlib import Path\n"
        "import os, sys, threading\n"
        "timer = threading.Timer(10, lambda: os._exit(0))\n"
        "timer.daemon = True; timer.start()\n"
        "Path('started').touch()\n"
        "while True:\n"
        "    sys.stdout.write('x' * 65536); sys.stdout.flush()\n"
    )
    spec = HookSpec(id='backpressure', command=(sys.executable, '-c', code),
        timeout_seconds=3 if mode == 'timeout' else 8)

    async def run():
        task = asyncio.create_task(run_hook_command(tmp_path, spec,
            HookEvent(name='BeforeModelCall', payload={'large': 'x' * 4_000_000})))
        try:
            async with asyncio.timeout(8):
                while not (tmp_path / 'started').exists():
                    if task.done():
                        raise AssertionError(task.result())
                    await asyncio.sleep(.01)
            process = owners[0].process
            _, high_water = process.stdin.transport.get_write_buffer_limits()
            assert process.stdin.transport.get_write_buffer_size() > high_water
            assert not task.done()
            if mode == 'cancel':
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await task
                assert result.decision == 'error' and result.timed_out
            assert process.returncode is not None
            for descriptor in (0, 1, 2):
                pipe = process._transport.get_pipe_transport(descriptor)
                assert pipe is None or pipe.is_closing(), f'Unclosed Hook pipe {descriptor}'
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            for owner in owners:
                await owner.close()
                # Keep a failing regression fixture safe too, without hiding the assertions above.
                for descriptor in (0, 1, 2):
                    pipe = owner.process._transport.get_pipe_transport(descriptor)
                    if pipe is not None:
                        pipe.close()
    asyncio.run(run())
