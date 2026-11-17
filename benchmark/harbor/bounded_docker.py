'''Bound transport/cleanup waits without changing task or verifier budgets.'''

import asyncio
from time import monotonic

from harbor.environments.docker.docker import DockerEnvironment


class BoundedDockerEnvironment(DockerEnvironment):
    transport_timeout_seconds = 120
    cleanup_timeout_seconds = 60

    async def service_is_dir(self, *args, **kwargs):
        async with asyncio.timeout(self.transport_timeout_seconds):
            return await super().service_is_dir(*args, **kwargs)

    async def service_download_file(self, *args, **kwargs):
        async with asyncio.timeout(self.transport_timeout_seconds):
            return await super().service_download_file(*args, **kwargs)

    async def service_download_dir(self, *args, **kwargs):
        async with asyncio.timeout(self.transport_timeout_seconds):
            return await super().service_download_dir(*args, **kwargs)

    async def service_download_dir_with_exclusions(self, *args, **kwargs):
        async with asyncio.timeout(self.transport_timeout_seconds):
            return await super().service_download_dir_with_exclusions(*args, **kwargs)

    async def _run_docker_compose_command(
        self, command, check=True, timeout_sec=None, stdin_data=None, on_output=None,
    ):
        limit = None
        if command and command[0] == 'cp':
            limit = self.transport_timeout_seconds
        elif command and command[0] in {'stop', 'down'} and not getattr(self, '_stopping', False):
            limit = self.cleanup_timeout_seconds
        if limit is not None:
            timeout_sec = min(timeout_sec, limit) if timeout_sec is not None else limit
        stopping = command and command[0] in {'stop', 'down'} and getattr(self, '_stopping', False)
        if stopping:
            command = [*command, '--timeout', '5']
        started = monotonic()
        try:
            return await super()._run_docker_compose_command(
                command, check=check, timeout_sec=timeout_sec,
                stdin_data=stdin_data, on_output=on_output,
            )
        except BaseException as error:
            if stopping:
                from forge.sessions.trajectory import exception_diagnostics
                self.cleanup_details['compose_error'] = exception_diagnostics(error)
            raise
        finally:
            if stopping:
                self.cleanup_details['compose_seconds'] = monotonic() - started

    async def stop(self, delete):
        # One deadline covers preparation and down; the nested transport does
        # not race it with a second identical timer.
        self._stopping = True
        self.cleanup_details = {}
        started = monotonic()
        try:
            async with asyncio.timeout(self.cleanup_timeout_seconds):
                await super().stop(delete=delete)
        finally:
            self._stopping = False
            self.cleanup_details['total_seconds'] = monotonic() - started

    @staticmethod
    async def _collect_buffered_output(process, *, timeout_sec, stdin_data=None):
        try:
            return await DockerEnvironment._collect_buffered_output(
                process, timeout_sec=timeout_sec, stdin_data=stdin_data,
            )
        except asyncio.CancelledError:
            await DockerEnvironment._terminate_process(process)
            raise

    @staticmethod
    async def _collect_streamed_output(process, **kwargs):
        try:
            return await DockerEnvironment._collect_streamed_output(process, **kwargs)
        except asyncio.CancelledError:
            await DockerEnvironment._terminate_process(process)
            raise
