"""Optional entry-point bindings; the legacy CLI defaults remain in the factory."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from forge.config import ForgeConfig
from forge.permissions.policy import PermissionManager


class ToolExecutionBackend(Protocol):
    mode: str

    def check_ready(self, policy: dict, *, required_mode: str) -> None: ...

    async def execute(self, call, registry): ...


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
    recorder: EventRecorder | None = None
    trusted_extensions: bool = True
    task_relation: str | None = None
    max_model_calls: int | None = None
    max_tool_calls: int | None = None
    wall_seconds: int | None = None

    def conversation_options(self):
        options = {'tool_backend': self.backend, 'event_recorder': self.recorder}
        if self.permission_manager is not None:
            options['permission_manager'] = self.permission_manager
        for key, value in (('task_relation', self.task_relation), ('max_iterations', self.max_model_calls),
                           ('max_tool_calls', self.max_tool_calls), ('max_turn_seconds', self.wall_seconds)):
            if value is not None:
                options[key] = value
        return options
