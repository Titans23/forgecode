"""Short offline Linux reproductions; does not invoke models or benchmark tasks."""
from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from time import perf_counter


def main():
    root = Path(sys.argv[1])
    source = root / 'forge/tools/shell.py'
    # Execute the production subprocess helpers verbatim, excluding unrelated
    # tool/schema imports so this probe needs only the image's standard library.
    names = {'ProcessResult', 'run_process', '_run_process', '_read_bounded', '_stop_and_collect',
             '_terminate_process_tree', 'sanitized_process_environment'}
    tree = ast.parse(source.read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    namespace = dict(globals(), __file__=str(source), MAX_PROCESS_OUTPUT_BYTES=1_000_000, PROCESS_CLEANUP_SECONDS=2.0,
                     SENSITIVE_ENV_MARKERS=('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL', 'PRIVATE_KEY'))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)

    async def timeout_probe(detached):
        child = 'import time; time.sleep(2)'
        command = ('import subprocess,sys; subprocess.Popen([sys.executable,"-c",' + repr(child)
                   + '],start_new_session=True)') if detached else child
        with tempfile.TemporaryDirectory() as directory:
            started = perf_counter()
            result = await namespace['run_process']([sys.executable, '-c', command],
                cwd=Path(directory), timeout_seconds=0.3)
            return {'requested_timeout_seconds': 0.3, 'elapsed_seconds': round(perf_counter()-started, 3),
                    'timed_out': result.timed_out, 'exit_code': result.exit_code}

    result = {'shell_source_sha256': sha256(source.read_bytes()).hexdigest(),
              'ordinary_child': asyncio.run(timeout_probe(False)),
              'detached_child_inherits_pipes': asyncio.run(timeout_probe(True))}
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = ('import socket,time; s=socket.socket(); s.bind(("127.0.0.1",' + str(port)
              + ')); s.listen(); time.sleep(4)')
    command = '\n'.join([
        'import subprocess,sys,socket,time,json',
        'p=subprocess.Popen([sys.executable,"-c",' + repr(server) + '],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)',
        'ready=False',
        'for i in range(50):',
        ' try:',
        '  s=socket.create_connection(("127.0.0.1",' + str(port) + '),timeout=0.05); s.close(); ready=True; break',
        ' except OSError: time.sleep(0.02)',
        'print(json.dumps({"ready_before_exit":ready,"server_pid":p.pid}))',
    ])
    supervisor = root / 'benchmark/harbor/process_supervisor.py'
    completed = subprocess.run([sys.executable, str(supervisor), '--', sys.executable, '-c', command],
                               capture_output=True, text=True, timeout=10)
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.3):
            alive = True
    except OSError:
        alive = False
    result['supervisor_service'] = {'source_sha256': sha256(supervisor.read_bytes()).hexdigest(),
        'supervisor_exit_code': completed.returncode, 'child_result': json.loads(completed.stdout),
        'alive_after_successful_exit': alive, 'stderr': completed.stderr}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
