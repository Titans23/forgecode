"""Task-local dependencies and existing owned-process supervision for native evals."""

import asyncio
import json
import os
from pathlib import Path
import shutil
from time import monotonic

from forge.sandbox.process_owner import LocalProcessOwner


def task_environment(python=None, version=None):
    allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE',
               'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
               'COMMONPROGRAMFILES', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE'}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONHASHSEED='0', PYTHONUTF8='1',
               PYTHONDONTWRITEBYTECODE='1', GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    if python:
        env['PATH'] = str(python.parent) + os.pathsep + env.get('PATH', '')
    if version:
        env['SETUPTOOLS_SCM_PRETEND_VERSION'] = version + '.0.dev0'
    return env


async def command(argv, cwd, log, env, *, timeout=600):
    """Use existing tree ownership; no OS isolation is implied by this helper."""
    owner = await LocalProcessOwner.start(argv, cwd=cwd, environment=env, output=True, capture_stderr=True)
    start = monotonic()
    timed_out = False
    failure = None

    async def drain(stream, path):
        count = 0
        with path.open('wb') as output:
            while chunk := await stream.read(65536):
                output.write(chunk[:max(0, 8 * 1024 * 1024 - count)])
                count += len(chunk)
        return count

    readers = [asyncio.create_task(drain(owner.process.stdout, log.with_suffix('.stdout.log'))),
               asyncio.create_task(drain(owner.process.stderr, log.with_suffix('.stderr.log')))]
    try:
        try:
            async with asyncio.timeout(timeout):
                await owner.process.wait()
        except TimeoutError:
            timed_out = True
    except BaseException as error:
        failure = error
        raise
    finally:
        cleanup = await owner.close()
        sizes = await asyncio.gather(*readers)
        if failure is not None:
            failure.cleanup_report = cleanup
    result = {'argv': argv, 'exit_code': owner.process.returncode, 'seconds': monotonic() - start,
              'timed_out': timed_out, 'cleanup': cleanup, 'output_bytes': sizes,
              'logs_truncated': any(size > 8 * 1024 * 1024 for size in sizes)}
    log.with_suffix('.command.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    if timed_out or cleanup['state'] != 'clean':
        error = RuntimeError('Command deadline or process cleanup failure: ' + str(log))
        error.cleanup_report = cleanup
        raise error
    return result


async def checked(argv, cwd, log, env):
    result = await command(argv, cwd, log, env)
    if result['exit_code']:
        raise RuntimeError('Preparation command failed: ' + str(log))
    return result


async def install_task_environment(workspace, interpreter, requirements, logs, *, version, offline=True, environment_root=None):
    """Copy dependencies into this fixture; never expose a reference editable install."""
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('uv is unavailable')
    logs.mkdir(parents=True, exist_ok=True)
    environment = task_environment(version=version)
    environment_root = Path(environment_root) if environment_root is not None else workspace / '.venv'
    await checked([uv, '--no-config', 'venv', str(environment_root), '--python', str(interpreter),
                   '--no-python-downloads'], workspace, logs / 'venv', environment)
    python = environment_root / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    environment = task_environment(python, version)
    common = [uv, '--no-config', *(('--offline',) if offline else ()), 'pip', 'install',
              '--python', str(python), '--no-deps', '--link-mode', 'copy']
    await checked([*common, '-r', str(requirements)], workspace, logs / 'dependencies', environment)
    await checked([*common, '--no-build-isolation', '-e', str(workspace)], workspace, logs / 'editable', environment)
    return python
