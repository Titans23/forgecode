"""Real Linux-container boundaries on Docker Desktop or Linux; no model calls."""
import asyncio
import json
import os

import pytest

from forge.sandbox.docker_process import ContainerLimits, DockerRuntime


IMAGE = os.environ.get('FORGE_STRICT_TEST_IMAGE',
    'python@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2')


def run_python(source, *, limits=None, stdin=b''):
    async def run():
        runtime = await DockerRuntime.connect(IMAGE)
        try:
            result = await runtime.run(['python', '-c', source], stdin=stdin, limits=limits)
            assert not runtime.containers
            return result
        finally:
            await runtime.close()
    return asyncio.run(run())


def test_real_read_write_network_secret_and_temporary_directory_boundary(tmp_path, monkeypatch):
    secret = tmp_path / 'host-private.txt'
    secret.write_text('synthetic host secret')
    monkeypatch.setenv('FORGE_BOUNDARY_SECRET', 'synthetic environment secret')
    source = '''
import json, os, pathlib, socket, tempfile
assert os.getuid() == 65532
assert 'FORGE_BOUNDARY_SECRET' not in os.environ
assert not pathlib.Path(HOST_PATH).exists()
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not pathlib.Path('/run/desktop/mnt/host').exists()
status = pathlib.Path('/proc/self/status').read_text()
assert 'CapEff:\\t0000000000000000' in status
assert 'NoNewPrivs:\\t1' in status
assert 'Seccomp:\\t2' in status
assert pathlib.Path('/sys/fs/cgroup/memory.max').read_text().strip() == '536870912'
assert pathlib.Path('/sys/fs/cgroup/memory.swap.max').read_text().strip() == '0'
quota, period = map(int, pathlib.Path('/sys/fs/cgroup/cpu.max').read_text().split())
assert quota / period == 1
for target in ('/root/escape', '/etc/hosts', '/etc/forge-escape'):
    try:
        pathlib.Path(target).write_text('escape')
    except OSError:
        pass
    else:
        raise AssertionError('unexpected write: ' + target)
pathlib.Path('result.txt').write_text('isolated')
with tempfile.TemporaryDirectory() as directory:
    pathlib.Path(directory, 'nested').mkdir()
    pathlib.Path(directory, 'nested', 'result').write_text('temporary')
assert sorted(p.name for p in pathlib.Path('/sys/class/net').iterdir()) == ['lo']
for address in ('1.1.1.1', '172.17.0.1', '127.0.0.1'):
    try:
        socket.create_connection((address, 443), timeout=.5)
    except OSError:
        pass
    else:
        raise AssertionError('unexpected network connection')
print(json.dumps({'stdin': input(), 'temporary_directory': 'passed', 'boundary': 'passed'}))
'''.replace('HOST_PATH', repr(str(secret)))
    result = run_python(source, stdin=b'owned input\n')
    assert result['exit_code'] == 0, result
    assert json.loads(result['stdout'])['stdin'] == 'owned input'
    assert secret.read_text() == 'synthetic host secret'
    assert not (tmp_path / 'result.txt').exists()


def test_real_disk_exhaustion_is_bounded():
    source = '''
import errno
try:
    with open('full.bin', 'wb', buffering=0) as stream:
        for _ in range(32):
            stream.write(b'x' * 1048576)
except OSError as error:
    assert error.errno == errno.ENOSPC, error
    print('disk limit enforced')
else:
    raise AssertionError('disk quota was not enforced')
'''
    result = run_python(source, limits=ContainerLimits(disk_bytes=8388608))
    assert result['exit_code'] == 0 and b'disk limit enforced' in result['stdout'], result


def test_real_pid_exhaustion_and_descendant_cleanup():
    source = '''
import errno, subprocess, sys
children = []
try:
    for _ in range(40):
        children.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']))
except OSError as error:
    assert error.errno == errno.EAGAIN, error
    print('pid limit enforced', len(children), flush=True)
else:
    raise AssertionError('pid quota was not enforced')
# Deliberately leave descendants; the owner must remove the complete container.
'''
    result = run_python(source, limits=ContainerLimits(pids=16, seconds=15))
    assert result['exit_code'] == 0 and b'pid limit enforced' in result['stdout'], result


def test_real_memory_limit_reports_oom():
    result = run_python('value = bytearray(512 * 1024 * 1024)',
        limits=ContainerLimits(memory_bytes=134217728, seconds=20))
    assert result['exit_code'] == 137 and result['oom_killed'], result


def test_real_output_is_drained_but_retention_is_bounded():
    result = run_python("import sys; sys.stdout.write('x' * 200000); sys.stderr.write('y' * 200000)",
        limits=ContainerLimits(output_bytes=1024))
    assert result['exit_code'] == 0
    assert len(result['stdout']) + len(result['stderr']) == 1024
    assert result['discarded_bytes'] == 400000 - 1024


@pytest.mark.parametrize('cancel', [False, True])
def test_real_timeout_or_cancel_removes_owned_container(cancel):
    async def run():
        runtime = await DockerRuntime.connect(IMAGE)
        try:
            task = asyncio.create_task(runtime.run(['python', '-c',
                'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); time.sleep(60)'],
                limits=ContainerLimits(seconds=2 if not cancel else 30)))
            if cancel:
                for _ in range(100):
                    if runtime.containers:
                        name = next(iter(runtime.containers))
                        running = await runtime.command('ps', '--quiet', '--filter', 'name=^/' + name + '$')
                        if running.strip():
                            break
                    await asyncio.sleep(.05)
                else:
                    pytest.fail('Container did not start')
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
                await task
            assert not runtime.containers
        finally:
            await runtime.close()
    asyncio.run(run())


def test_real_close_during_admission_cannot_leave_a_late_container():
    async def run():
        runtime = await DockerRuntime.connect(IMAGE)
        task = asyncio.create_task(runtime.run(['python', '-c', 'import time; time.sleep(60)']))
        try:
            while not runtime.containers:
                assert not task.done()
                await asyncio.sleep(0)
            await runtime.close()
            assert task.cancelled()
            assert not runtime.containers
            with pytest.raises(Exception, match='closed'):
                await runtime.run(['python', '-c', 'print("must not start")'])
        finally:
            await runtime.close()
    asyncio.run(run())
