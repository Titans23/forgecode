"""Live local-trusted process ownership. This grants no native sandbox capability."""
import asyncio
import json
import os
from pathlib import Path
import sys


class LocalProcessOwner:
    def __init__(self, process, job):
        self.process, self.job = process, job
        self._closing = None

    @classmethod
    async def start(cls, argv, *, cwd, environment, output=False):
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            raise ValueError('Owned launch requires an explicit argument list')
        job = None
        options = {'cwd': cwd, 'env': environment, 'stdin': asyncio.subprocess.PIPE,
            'stdout': asyncio.subprocess.PIPE if output else asyncio.subprocess.DEVNULL,
            'stderr': asyncio.subprocess.DEVNULL}
        if os.name == 'nt':
            import subprocess
            from forge.tools.windows_job import WindowsJob
            from forge.release.processes import worker_argv
            job = WindowsJob()
            argv = worker_argv(argv)
            options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        elif sys.platform.startswith('linux'):
            from forge.release.processes import worker_argv
            argv = worker_argv(argv,cleanup=True)
            options['start_new_session'] = True
        else:
            raise ValueError('Process ownership requires Windows Job or Linux subreaper')
        process = None
        try:
            process = await asyncio.create_subprocess_exec(*argv, **options)
            if job:
                job.assign(process.pid)
                process.stdin.write(b'!')
                await process.stdin.drain()
            return cls(process, job)
        except BaseException:
            if job:
                job.close()
            if process is not None:
                if process.returncode is None:
                    process.kill()  # Gated child has not executed the task.
                await process.wait()
            raise

    async def _close(self):
        report = {'state': 'unknown', 'scope': 'local-trusted-process-tree'}
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
            if self.job:
                self.job.close()
            if self.process.returncode is None:
                self.process.kill()
                # Cleanup stays unknown if the supervised worker did not confirm.
                try:
                    await asyncio.wait_for(self.process.wait(), 1)
                except TimeoutError:
                    pass
        return report

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        return await asyncio.shield(self._closing)
