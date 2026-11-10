"""Optional execution providers; no native backend is imported until selected."""
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from forge.runtime.dependencies import ToolExecutionBackend


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    root: Path
    data_root: Path
    mode: str
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...] | None
    wall_seconds: int


@dataclass(frozen=True, slots=True)
class ExecutionSetup:
    backend: ToolExecutionBackend
    policy: dict


SandboxProvider = Callable[[ExecutionRequest], Awaitable[ExecutionSetup]]


async def open_execution(request: ExecutionRequest, provider: str | None, *, plugins=None) -> ExecutionSetup:
    """Select once before model construction; a failed provider never falls back."""
    if request.mode == 'full-access':
        from forge.sandbox.provider import open_host_execution
        factory = open_host_execution
    elif provider == 'native':
        from forge.sandbox.provider import open_native_execution
        factory = open_native_execution
    elif plugins is not None and provider in plugins.sandbox_providers:
        factory = plugins.sandbox_providers[provider]
    else:
        raise ValueError(f'Sandbox provider is not registered: {provider}')
    setup = await factory(request)
    if not isinstance(setup, ExecutionSetup):
        raise TypeError('Sandbox provider must return ExecutionSetup')
    try:
        from forge.protocol import validate
        validate('sandbox-policy', setup.policy)
        setup.backend.check_ready(setup.policy,
            required_mode='local-trusted' if request.mode == 'full-access' else request.mode)
    except BaseException as error:
        try:
            error.cleanup_report = await setup.backend.close()
        except BaseException as cleanup_error:
            error.cleanup_report = {'state': 'unknown', 'reason': type(cleanup_error).__name__}
        raise
    return setup
