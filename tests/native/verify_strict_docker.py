"""Real Linux-container boundaries on Docker Desktop or Linux; no model calls."""
import asyncio
import json
import os

import pytest

from forge.sandbox.docker_process import ContainerLimits, DockerRuntime
from forge.sandbox.docker_workspace import run_workspace
from forge.sandbox.path_policy import PathPolicy, inspect_path
from forge.sandbox.workspace_transfer import TransferLimits
from forge.protocol import ContractError


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
assert os.getuid() == EXPECTED_UID
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
'''.replace('HOST_PATH', repr(str(secret))).replace('EXPECTED_UID', str(os.getuid() if os.name != 'nt' and os.getuid() else 65532))
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
    # Docker's asynchronous OOMKilled event can lag container exit. Observe
    # the kernel counter while the small parent remains in the cgroup instead.
    source = '''
import json, pathlib, subprocess, sys
events = pathlib.Path('/sys/fs/cgroup/memory.events')
def counters():
    return dict(zip(*[iter(events.read_text().split())] * 2))
before = int(counters()['oom_kill'])
child = subprocess.run([sys.executable, '-c', 'value = bytearray(512 * 1024 * 1024)'])
print(json.dumps({'exit_code': child.returncode, 'oom_kills': int(counters()['oom_kill']) - before}))
'''
    result = run_python(source,
        limits=ContainerLimits(memory_bytes=134217728, seconds=20))
    assert result['exit_code'] == 0, result
    evidence = json.loads(result['stdout'])
    assert evidence['exit_code'] == -9 and evidence['oom_kills'] >= 1, evidence


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
            with pytest.raises(ContractError, match='closed'):
                await runtime.run(['python', '-c', 'print("must not start")'])
        finally:
            await runtime.close()
    asyncio.run(run())


def workspace_policy(root, *, writes=True):
    return PathPolicy(inspect_path(root, absolute=True), 'strict_allowlist_required',
        (root,), (root,) if writes else (), ())


def test_real_workspace_code_fix_and_verification_roundtrip(tmp_path):
    (tmp_path / 'calculator.py').write_text('def add(a, b):\n    return a - b\n')
    (tmp_path / 'test_calculator.py').write_text('import unittest\nfrom calculator import add\n'
        'class TestAdd(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n')
    (tmp_path / '.env').write_text('synthetic credential excluded from input')
    async def run():
        runtime = await DockerRuntime.connect(IMAGE)
        try:
            fix = "from pathlib import Path; import subprocess,sys; assert not Path('.env').exists(); p=Path('calculator.py'); p.write_text(p.read_text().replace('a - b', 'a + b')); sys.exit(subprocess.call([sys.executable,'-B','-m','unittest','-v']))"
            result = await run_workspace(runtime, workspace_policy(tmp_path), ['python', '-B', '-c', fix])
            assert result['exit_code'] == 0 and b'OK' in result['stderr'], result
            assert result['changed_paths'] == ['calculator.py']
            assert 'return a + b' in (tmp_path / 'calculator.py').read_text()
            verify = await run_workspace(runtime, workspace_policy(tmp_path, writes=False), ['python', '-B', '-m', 'unittest', '-v'])
            assert verify['exit_code'] == 0 and not verify['changed_paths'], verify
            assert (tmp_path / '.env').read_text() == 'synthetic credential excluded from input'
        finally:
            await runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize('attack', ['symlink', 'protected'])
def test_real_invalid_workspace_output_never_mutates_host(tmp_path, attack):
    (tmp_path / 'code.py').write_text('original')
    sources = {
        'symlink': "from pathlib import Path; Path('code.py').write_text('changed'); Path('link').symlink_to('/etc/passwd')",
        'protected': "from pathlib import Path; Path('code.py').write_text('changed'); Path('.env').write_text('forbidden')",
    }
    async def run():
        runtime = await DockerRuntime.connect(IMAGE)
        try:
            with pytest.raises(ContractError):
                await run_workspace(runtime, workspace_policy(tmp_path),
                    ['python', '-c', sources[attack]])
            assert (tmp_path / 'code.py').read_text() == 'original'
            assert sorted(p.name for p in tmp_path.iterdir()) == ['code.py']
            assert not runtime.containers
        finally:
            await runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize('scoped', [False, True])
def test_real_kernel_write_scopes_and_readonly_references(tmp_path, scoped):
    project, reference = tmp_path / 'project', tmp_path / 'reference'
    project.mkdir()
    reference.mkdir()
    (project / 'src').mkdir()
    (project / 'src/code').write_text('original')
    (project / 'locked').write_text('locked')
    (reference / 'example').write_text('reference')
    (reference / '.env').write_text('synthetic secret')
    policy = PathPolicy(inspect_path(project, absolute=True), 'strict_allowlist_required',
        (project, reference), (project / 'src',) if scoped else (), ())
    source = '''
from pathlib import Path
import errno
assert Path('/references/0/example').read_text() == 'reference'
assert not Path('/references/0/.env').exists()
for name in ('/workspace/locked', '/references/0/example', '/inputs/project/locked', EXTRA):
    path = Path(name)
    for action in (lambda: path.write_text('escape'), lambda: path.chmod(0o777), lambda: path.rename(path.with_name('moved'))):
        try:
            action()
        except OSError as error:
            assert error.errno in (errno.EROFS, errno.EACCES, errno.EPERM, errno.EXDEV), error
        else:
            raise AssertionError('kernel permitted mutation: ' + name)
if SCOPED:
    Path('src/code').write_text('fixed')
    Path('src/new').write_text('new')
print('kernel scopes enforced')
'''.replace('EXTRA', repr('/workspace/locked' if scoped else '/workspace/src/code')).replace('SCOPED', repr(scoped))
    async def run():
        runtime = await DockerRuntime.connect(IMAGE)
        try:
            result = await run_workspace(runtime, policy, ['python', '-B', '-c', source])
            assert result['exit_code'] == 0, result
            assert result['changed_paths'] == (['src/code', 'src/new'] if scoped else [])
            assert result['read_roots'] == {str(reference): '/references/0'}
            assert (project / 'locked').read_text() == 'locked'
            assert (reference / 'example').read_text() == 'reference'
            assert not runtime.containers
        finally:
            await runtime.close()
    asyncio.run(run())


def test_real_concurrent_user_edit_rejects_sandbox_writeback(tmp_path):
    (tmp_path / 'code.py').write_text('original')
    async def run():
        runtime = await DockerRuntime.connect(IMAGE)
        task = asyncio.create_task(run_workspace(runtime, workspace_policy(tmp_path), ['python', '-c',
            "from pathlib import Path; import time; Path('code.py').write_text('agent output'); time.sleep(1)"]))
        try:
            while not runtime.containers:
                assert not task.done()
                await asyncio.sleep(.01)
            (tmp_path / 'code.py').write_text('concurrent user edit')
            with pytest.raises(ContractError, match='changed while'):
                await task
            assert (tmp_path / 'code.py').read_text() == 'concurrent user edit'
        finally:
            await runtime.close()
    asyncio.run(run())


def test_real_cancel_after_guest_write_does_not_publish_late_changes(tmp_path):
    (tmp_path / 'code.py').write_text('original')
    async def run():
        runtime = await DockerRuntime.connect(IMAGE)
        task = asyncio.create_task(run_workspace(runtime, workspace_policy(tmp_path), ['python', '-c',
            "from pathlib import Path; import time; Path('code.py').write_text('guest change'); Path('ready').touch(); time.sleep(60)"]))
        try:
            for _ in range(100):
                if runtime.containers:
                    name = next(iter(runtime.containers))
                    running = await runtime.command('ps', '--quiet', '--filter', 'name=^/' + name + '$')
                    if running.strip():
                        result = await runtime.command('exec', name, 'python', '-c',
                            "from pathlib import Path; print(Path('/workspace/ready').exists())")
                        if result.strip() == b'True':
                            break
                assert not task.done()
                await asyncio.sleep(.05)
            else:
                pytest.fail('Guest write was not observed')
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (tmp_path / 'code.py').read_text() == 'original'
            assert not (tmp_path / 'ready').exists()
            assert not runtime.containers
        finally:
            await runtime.close()
    asyncio.run(run())


def test_real_detached_descendants_stop_before_workspace_export(tmp_path):
    source = "import subprocess,sys; from pathlib import Path; subprocess.Popen([sys.executable,'-c',\"import time; from pathlib import Path; time.sleep(1); Path('late').write_text('late')\"],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); Path('early').write_text('early')"
    async def run():
        runtime = await DockerRuntime.connect(IMAGE)
        try:
            result = await run_workspace(runtime, workspace_policy(tmp_path), ['python', '-c', source])
            assert result['exit_code'] == 0
            assert result['changed_paths'] == ['early']
            assert not (tmp_path / 'late').exists()
            assert not runtime.containers
        finally:
            await runtime.close()
    asyncio.run(run())


def test_real_workspace_output_quota_rejects_without_partial_publication(tmp_path):
    (tmp_path / 'old').write_bytes(b'12')
    async def run():
        runtime = await DockerRuntime.connect(IMAGE)
        try:
            with pytest.raises(ContractError, match='no host changes'):
                await run_workspace(runtime, workspace_policy(tmp_path), ['python', '-c',
                    "from pathlib import Path; Path('old').write_bytes(b'new'); Path('big').write_bytes(b'12345')"],
                    transfer_limits=TransferLimits(bytes=4))
            assert (tmp_path / 'old').read_bytes() == b'12'
            assert not (tmp_path / 'big').exists()
        finally:
            await runtime.close()
    asyncio.run(run())
