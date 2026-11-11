"""Optional entry-point bindings; the legacy CLI defaults remain in the factory."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from forge.config import ForgeConfig
from forge.permissions.policy import PermissionManager


class ToolExecutionBackend(Protocol):
    mode: str

    def check_ready(self, policy: dict, *, required_mode: str) -> None: ...

    async def execute(self, call, registry): ...

    async def close(self) -> dict | None: ...


class EventRecorder(Protocol):
    def record(self, event) -> None: ...

    def record_request(self, kind: str, attributes: dict) -> None: ...


@dataclass(frozen=True, slots=True)
class RuntimeBindings:
    config: ForgeConfig | None = None
    model_client_factory: Callable[..., Any] | None = None
    data_root: Path | None = None
    permission_manager: PermissionManager | None = None
    backend: ToolExecutionBackend | None = None
    owns_tool_backend: bool = True
    recorder: EventRecorder | None = None
    trusted_extensions: bool = True
    task_relation: str | None = None
    intent_routing: bool = False
    turn_baseline_handler: Callable[[], Awaitable[None] | None] | None = None
    plugin_host: Any | None = None
    model_aliases: dict = field(default_factory=dict)
    model_environment: dict = field(default_factory=dict)

    def conversation_options(self):
        options = {'tool_backend': self.backend, 'event_recorder': self.recorder,
                   'owns_tool_backend': self.owns_tool_backend,
                   'turn_baseline_handler': self.turn_baseline_handler}
        if self.permission_manager is not None:
            options['permission_manager'] = self.permission_manager
        if self.task_relation is not None:
            options['task_relation'] = self.task_relation
        return options
