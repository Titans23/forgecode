"""Owned, offline Linux containers. No host mounts, daemon socket or host env in tasks."""
import asyncio
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from uuid import uuid4

from forge.protocol import ContractError


def unavailable(message):
    return ContractError(message, kind='SANDBOX_UNAVAILABLE', code=-32010)


def docker_binary(forbidden_roots=()):
    """Do not execute a project-supplied docker program, including Windows cwd search."""
    roots = (Path.cwd().resolve(), *(Path(root).resolve() for root in forbidden_roots))
    name = 'docker.exe' if os.name == 'nt' else 'docker'
    for directory in os.get_exec_path():
        if not directory or not Path(directory).is_absolute():
            continue
        if os.name == 'nt' and not re.match(r'^[A-Za-z]:[\\/]', directory):
            continue
        candidate = Path(directory) / name
        if not candidate.is_file():
            continue
        candidate = candidate.resolve(strict=True)
        if any(candidate.is_relative_to(root) for root in roots):
            continue
        if os.name == 'nt' and not re.match(r'^[A-Za-z]:[\\/]', str(candidate)):
            continue
        if os.access(candidate, os.X_OK):
            return str(candidate)
    raise unavailable('Docker must be installed outside the workspace on an absolute trusted PATH')


@dataclass(frozen=True)
class ContainerLimits:
    memory_bytes: int = 536870912
    disk_bytes: int = 134217728
    pids: int = 64
    cpus: float = 1.0
    seconds: float = 120
    output_bytes: int = 1048576

    def __post_init__(self):
        for name, minimum in (('memory_bytes', 67108864), ('disk_bytes', 4194304), ('pids', 8), ('output_bytes', 1)):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ValueError(f'{name} must be an integer >= {minimum}')
        for name in ('cpus', 'seconds'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be finite and positive')


class DockerRuntime:
    """A trusted local engine connection with an immutable, already-installed image."""

    def __init__(self, binary, endpoint, image, config):
        self.binary, self.endpoint, self.image = binary, endpoint, image
        self.config = config
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith('DOCKER_')}
        self.containers = set()
        self._active = set()
        self.closed = False

    @classmethod
    async def connect(cls, image, *, forbidden_roots=()):
        if not isinstance(image, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]{0,255}', image):
            raise ValueError('An explicit local container image is required')
        binary = docker_binary(forbidden_roots)
        config = tempfile.TemporaryDirectory(prefix='forge-docker-config-')
        try:
            endpoint = os.environ.get('DOCKER_HOST')
            if not endpoint:
                process = await cls._spawn([binary, 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], cwd=config.name)
                try:
                    async with asyncio.timeout(15):
                        output, _ = await process.communicate()
                    if process.returncode:
                        raise unavailable('Cannot resolve the selected Docker context')
                    endpoint = output.decode().strip()
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
            if not endpoint.startswith(('unix:///', 'npipe:////./pipe/')):
                raise unavailable('Strict sandbox requires a local Unix socket or Windows named-pipe engine')
            runtime = cls(binary, endpoint, image, config)
            info = json.loads(await runtime.command('info', '--format', '{{json .}}'))
            if info.get('OSType') != 'linux' or info.get('CgroupVersion') != '2' or any(info.get(key) is not True
                    for key in ('MemoryLimit', 'SwapLimit', 'PidsLimit', 'CpuCfsQuota')):
                raise unavailable('A Linux cgroup v2 engine with memory, swap, PID and CPU enforcement is required')
            metadata = json.loads(await runtime.command('image', 'inspect', image))[0]
            if metadata.get('Os') != 'linux' or metadata['Config'].get('Volumes'):
                raise unavailable('Strict images must be Linux images without implicit writable volumes')
            identity = metadata['Id']
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', identity):
                raise unavailable('Image must resolve to an immutable local image ID')
            runtime.image = identity
            return runtime
        except BaseException:
            config.cleanup()
            raise

    @staticmethod
    async def _spawn(argv, **kwargs):
        return await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}), **kwargs)

    def argv(self, *args):
        return [self.binary, '--host', self.endpoint, '--config', self.config.name, *args]

    async def command(self, *args):
        process = await self._spawn(self.argv(*args), env=self.environment, cwd=self.config.name)
        try:
            async with asyncio.timeout(30):
                output, error = await process.communicate()
            if process.returncode:
                raise unavailable('Docker operation failed: ' + error.decode(errors='replace')[:1200])
            return output
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def run(self, argv, *, stdin=b'', limits=None):
        if self.closed:
            raise unavailable('Docker runtime is closed')
        task = asyncio.current_task()
        self._active.add(task)
        try:
            return await self._run(argv, stdin=stdin, limits=limits)
        finally:
            self._active.discard(task)

    async def _run(self, argv, *, stdin, limits):
        limits = limits or ContainerLimits()
        if not argv or any(not isinstance(v, str) or '\0' in v for v in argv):
            raise ValueError('Command must be a nonempty argument vector')
        name = 'forge-strict-' + uuid4().hex
        self.containers.add(name)
        # The three writable filesystems together cannot exceed disk_bytes.
        scratch = limits.disk_bytes // 8
        workspace = limits.disk_bytes - 2 * scratch
        options = ['create', '--name', name, '--label', 'forge.owner=' + name,
            '--pull=never', '--network=none', '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--user=65532:65532', '--init',
            '--memory', str(limits.memory_bytes), '--memory-swap', str(limits.memory_bytes),
            '--pids-limit', str(limits.pids), '--cpus', str(limits.cpus),
            '--ulimit', 'core=0', '--log-driver=none', '--interactive',
            '--shm-size', str(scratch), '--workdir=/workspace',
            '--tmpfs', f'/workspace:rw,nosuid,nodev,uid=65532,gid=65532,mode=700,size={workspace}',
            '--tmpfs', f'/tmp:rw,nosuid,nodev,noexec,uid=65532,gid=65532,mode=700,size={scratch}',
            '--env=HOME=/tmp', '--env=TMPDIR=/tmp', '--entrypoint', argv[0], self.image, *argv[1:]]
        process = None
        streams = []
        captured = {'stdout': bytearray(), 'stderr': bytearray()}
        retained = discarded = 0

        async def drain(reader, stream):
            nonlocal retained, discarded
            while chunk := await reader.read(8192):
                keep = max(0, min(len(chunk), limits.output_bytes - retained))
                captured[stream].extend(chunk[:keep])
                retained += keep
                discarded += len(chunk) - keep

        # Resolve create before cleanup even when cancellation races admission.
        create = asyncio.create_task(self.command(*options))
        try:
            await asyncio.shield(create)
            process = await self._spawn(self.argv('start', '--attach', '--interactive', name), env=self.environment, cwd=self.config.name)
            streams = [asyncio.create_task(drain(process.stdout, 'stdout')), asyncio.create_task(drain(process.stderr, 'stderr'))]
            async with asyncio.timeout(limits.seconds):
                process.stdin.write(stdin)
                await process.stdin.drain()
                process.stdin.close()
                await process.wait()
                await asyncio.gather(*streams)
            state = json.loads(await self.command('inspect', '--format', '{{json .State}}', name))
            if state['Running'] or state['Status'] != 'exited':
                raise unavailable('Container execution did not reach a confirmed terminal state')
            return {'exit_code': state['ExitCode'], 'oom_killed': state['OOMKilled'],
                'stdout': bytes(captured['stdout']), 'stderr': bytes(captured['stderr']),
                'discarded_bytes': discarded, 'image': self.image}
        finally:
            try:
                await asyncio.shield(create)
            except Exception:
                pass
            cleanup = asyncio.create_task(self._remove(name))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
            finally:
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
                for task in streams:
                    task.cancel()
                await asyncio.gather(*streams, return_exceptions=True)

    async def _remove(self, name):
        error = None
        try:
            await self.command('rm', '--force', '--volumes', name)
        except ContractError as failure:
            error = failure
        existing = await self.command('ps', '--all', '--quiet', '--filter', 'name=^/' + name + '$')
        if existing.strip():
            raise unavailable('Owned container cleanup is unconfirmed') from error
        self.containers.discard(name)

    async def close(self):
        self.closed = True
        active = list(self._active)
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        for name in list(self.containers):
            await self._remove(name)
        self.config.cleanup()
