'''Offline regression checks against production helpers, with only stdlib.'''
import ast
import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
from time import perf_counter


root = Path.cwd()
source = root / 'forge/tools/shell.py'
names = {'ProcessResult', 'run_process', '_run_process', '_read_bounded',
         '_stop_and_collect', '_terminate_process_tree', 'sanitized_process_environment'}
nodes = [node for node in ast.parse(source.read_text()).body if getattr(node, 'name', None) in names]
namespace = dict(globals(), __file__=str(source), MAX_PROCESS_OUTPUT_BYTES=1000000,
                 PROCESS_CLEANUP_SECONDS=2., SENSITIVE_ENV_MARKERS=('API_KEY', 'TOKEN', 'SECRET'))
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)


async def check_commands(directory):
    run = namespace['run_process']
    async def command(code, **kwargs):
        return await run([sys.executable, '-c', code], cwd=directory, **kwargs)
    result = await command('import sys; data=sys.stdin.read(); print(data); print("err",file=sys.stderr)',
                           input_text='hello', timeout_seconds=3)
    assert (result.stdout, result.stderr, result.exit_code) == ('hello\n', 'err\n', 0)
    result = await command('print("x"*200000)', timeout_seconds=3, max_output_bytes=100)
    assert result.stdout_bytes == 200001 and result.stdout_truncated and result.stdout_artifact
    result = await command('import time; time.sleep(60)', input_text='x'*2000000, timeout_seconds=.3)
    assert result.timed_out and result.duration_seconds < 2
    peer = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        for mode in ('timeout', 'cancel'):
            pid_file = directory / mode
            child = f'import os,time; from pathlib import Path; Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)'
            parent = f'import subprocess,sys; subprocess.Popen([sys.executable,"-c",{child!r}],start_new_session=True)'
            start = perf_counter()
            task = asyncio.create_task(command(parent, timeout_seconds=.4 if mode == 'timeout' else 10))
            while not pid_file.exists() and perf_counter() - start < 3:
                await asyncio.sleep(.01)
            assert pid_file.exists()
            if mode == 'cancel':
                task.cancel()
            try:
                result = await asyncio.wait_for(task, 3)
                assert mode == 'timeout' and result.timed_out
            except asyncio.CancelledError:
                assert mode == 'cancel'
            assert perf_counter() - start < 2
            assert not Path(f'/proc/{pid_file.read_text()}').exists()
            assert peer.poll() is None
    finally:
        peer.kill()
        peer.wait()
    print('PASS: stdin, output fidelity/archive, blocked stdin, detached timeout/cancel, peer isolation')


def check_handoff(directory):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    pid_file = directory / 'service'
    server = (f'import socket,time,os; from pathlib import Path; s=socket.socket(); '
              f's.bind(("127.0.0.1",{port})); s.listen(); Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)')
    launch = f'import subprocess,sys; subprocess.Popen([sys.executable,"-c",{server!r}],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)'
    # Exercise both ownership layers, just as the benchmark does.
    parent = (f'import subprocess,sys,time; from pathlib import Path; '
              f'subprocess.run([sys.executable,{str(root / "forge/tools/linux_process_worker.py")!r},'
              f'{json.dumps([sys.executable, "-c", launch])!r},"0"],check=True); '
              f'\nwhile not Path({str(pid_file)!r}).exists(): time.sleep(.01)')
    result = subprocess.run([sys.executable, '-m', 'benchmark.harbor.process_supervisor',
        '--preserve-on-success', '--', sys.executable, '-c', parent], capture_output=True, timeout=8)
    assert result.returncode == 0, result.stderr
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            pass
        print('PASS: service reachable after command and benchmark supervisors exit successfully')
    finally:
        os.kill(int(pid_file.read_text()), signal.SIGKILL)


with tempfile.TemporaryDirectory() as temp:
    asyncio.run(check_commands(Path(temp)))
    check_handoff(Path(temp))
