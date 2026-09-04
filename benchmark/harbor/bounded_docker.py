'''Bound transport/cleanup waits without changing task or verifier budgets.'''

import asyncio

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
        elif command and command[0] in {'stop', 'down'}:
            limit = self.cleanup_timeout_seconds
        if limit is not None:
            timeout_sec = min(timeout_sec, limit) if timeout_sec is not None else limit
        return await super()._run_docker_compose_command(
            command, check=check, timeout_sec=timeout_sec,
            stdin_data=stdin_data, on_output=on_output,
        )

    async def stop(self, delete):
        # Includes ownership preparation, not just the final compose down.
        async with asyncio.timeout(self.cleanup_timeout_seconds):
            await super().stop(delete=delete)

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
