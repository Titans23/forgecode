"""Owned process trees. Process ownership alone grants no sandbox capability."""
import asyncio
import json
import os
from pathlib import Path
import sys
import subprocess


def linux_process_identity(pid):
    """Start ticks disambiguate a current owned process from a reused PID."""
    try:
        return Path(f'/proc/{int(pid)}/stat').read_text().rsplit(') ', 1)[1].split()[19]
    except (OSError, ValueError, IndexError):
        return None


class _DetachedProcess:
    """File-backed Linux child; no asyncio pipe transport can kill a handed-off service."""
    stdin = None

    def __init__(self, process):
        self.process, self.pid = process, process.pid

    @property
    def returncode(self):
        return self.process.poll()

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.02)
        return self.returncode

    def terminate(self):
        self.process.terminate()

    def kill(self):
        self.process.kill()


class LocalProcessOwner:
    def __init__(self, process, job):
        self.process, self.job = process, job
        self._closing = None
        self.transferred = False

    @classmethod
    async def start(cls, argv, *, cwd, environment, output=False, capture_stderr=False,
                    output_path=None, input_path=None):
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            raise ValueError('Owned launch requires an explicit argument list')
        job = None
        ready_read = ready_write = None
        options = {'cwd': cwd, 'env': environment, 'stdin': asyncio.subprocess.PIPE,
            'stdout': asyncio.subprocess.PIPE if output else asyncio.subprocess.DEVNULL,
            'stderr': asyncio.subprocess.PIPE if capture_stderr else asyncio.subprocess.DEVNULL}
        if os.name == 'nt':
            from forge.tools.windows_job import WindowsJob
            from forge.release.processes import worker_argv
            job = WindowsJob()
            argv = worker_argv(argv)
            options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x4  # CREATE_SUSPENDED
        elif sys.platform.startswith('linux'):
            from forge.release.processes import worker_argv
            ready_read, ready_write = os.pipe()
            os.set_blocking(ready_read, False)
            argv = worker_argv(argv,cleanup=True,ready_fd=ready_write)
            options['start_new_session'] = True
            options['pass_fds'] = (ready_write,)
        else:
            raise ValueError('Process ownership requires Windows Job or Linux subreaper')
        process = None
        async def launch_child():
            if input_path is not None:
                if not sys.platform.startswith('linux') or output_path is None:
                    raise ValueError('File-backed service handoff requires Linux')
                def spawn():
                    with Path(input_path).open('rb') as source, Path(output_path).open('wb') as destination:
                        return _DetachedProcess(subprocess.Popen(argv, **{**options,
                            'stdin': source, 'stdout': destination}))
                return await asyncio.to_thread(spawn)
            if output_path is not None:
                with Path(output_path).open('wb') as destination:
                    return await asyncio.create_subprocess_exec(*argv, **{**options, 'stdout': destination})
            return await asyncio.create_subprocess_exec(*argv, **options)
        launch = asyncio.create_task(launch_child())
        ready = None
        async def wait_ready():
            async with asyncio.timeout(5):
                while True:
                    try:
                        value = os.read(ready_read, 1)
                    except BlockingIOError:
                        await asyncio.sleep(.01)
                        continue
                    if value != b'!':
                        raise OSError('Owned worker exited before establishing cleanup')
                    return
        try:
            process = await asyncio.shield(launch)
            if ready_read is not None:
                ready = asyncio.create_task(wait_ready())
                await asyncio.shield(ready)
            if job:
                job.assign(process.pid)
                job.resume(process.pid)
                process.stdin.write(b'!')
                await process.stdin.drain()
            return cls(process, job)
        except BaseException as error:
            # Cancellation during launch must still obtain and reap the gated
            # child; otherwise its pipes and process can outlive the request.
            if process is None:
                try:
                    process = await asyncio.shield(launch)
                except Exception:
                    pass
            if process is not None:
                if ready_read is not None:
                    # A cancel during Python startup must not kill the worker
                    # before its subreaper and signal handlers own cleanup.
                    try:
                        if ready is None:
                            ready = asyncio.create_task(wait_ready())
                        await asyncio.shield(ready)
                    except (OSError, TimeoutError):
                        pass
                if process.stdin is not None:
                    process.stdin.close()
                cleanup = await cls(process, job).close()
            else:
                if job:
                    job.close()
                cleanup = {'state': 'clean', 'reason': 'launch_did_not_create_process'}
            if isinstance(error, asyncio.CancelledError):
                from forge.tool_types import ToolExecutionCancelled
                raise ToolExecutionCancelled(cleanup) from error
            raise
        finally:
            for descriptor in (ready_read, ready_write):
                if descriptor is not None:
                    os.close(descriptor)

    async def _close(self):
        report = {'state': 'unknown', 'scope': 'owned-process-tree'}
        try:
            async with asyncio.timeout(2):
                if self.job:
                    self.job.terminate()
                    while self.job.active_processes():
                        await asyncio.sleep(0.01)
                    report = {**report, 'state': 'clean', 'active_processes': 0}
                else:
                    if self.process.returncode is None:
                        self.process.terminate()  # Live owned subreaper only; no historical PID.
                    await self.process.wait()
                    if 0 <= self.process.returncode < 256 and self.process.returncode != 125:
                        report = {**report, 'state': 'clean', 'reason': 'subreaper_reaped_owned_descendants'}
                await self.process.wait()
        except (OSError, TimeoutError) as error:
            report['reason'] = type(error).__name__
        finally:
            if self.process.stdin is not None:
                self.process.stdin.close()
            if self.job:
                self.job.close()
            if self.process.returncode is None:
                self.process.kill()
                # Cleanup stays unknown if the supervised worker did not confirm.
                try:
                    await asyncio.wait_for(self.process.wait(), 1)
                except TimeoutError:
                    pass
            # A cancelled reader does not drain the Proactor pipe transports.
            # The owned process has settled (or cleanup is explicitly unknown);
            # close its local descriptors before the event loop can disappear.
            transport = getattr(self.process, '_transport', None)
            if transport is not None:
                transport.close()
                await asyncio.sleep(0)
        return report

    async def close(self):
        if self.transferred:
            return {'state': 'transferred', 'scope': 'owned-process-tree', 'pid': self.process.pid}
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        return await asyncio.shield(self._closing)

    def transfer(self):
        if not isinstance(self.process, _DetachedProcess) or self._closing is not None:
            raise ValueError('Only a live file-backed Linux service can be transferred')
        self.transferred = True
