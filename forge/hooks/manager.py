'''Hook matching, execution, and result aggregation.'''

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

from forge.hooks.config import load_hook_settings
from forge.hooks.models import (
    HookEvent,
    HookExecution,
    HookOutcome,
    HookSettings,
    HookSpec,
)
from forge.hooks.runner import run_hook_command


BLOCKING_EVENTS = frozenset(
    {'BeforeModelCall', 'PreToolUse', 'BeforeFileEdit', 'BeforeCompact'}
)


class HookManager:
    '''Run configured hooks deterministically at lifecycle boundaries.'''

    def __init__(self, root: Path, settings: HookSettings) -> None:
        self.root = root.resolve()
        self.settings = settings

    @classmethod
    def from_root(cls, root: Path) -> HookManager:
        return cls(root, load_hook_settings(root))

    async def emit(self, event: HookEvent) -> HookOutcome:
        arguments = (
            dict(event.arguments) if event.arguments is not None else None
        )
        contexts: list[str] = []
        executions: list[HookExecution] = []
        for spec in self.settings.hooks.get(event.name, ()):
            current = HookEvent(
                name=event.name,
                session_id=event.session_id,
                tool_name=event.tool_name,
                tool_call_id=event.tool_call_id,
                arguments=arguments,
                paths=event.paths,
                payload=event.payload,
            )
            if not spec.enabled or not _matches(spec, current):
                continue
            try:
                result = await run_hook_command(self.root, spec, current)
            except (OSError, ValueError) as error:
                decision = 'error'
                reason = str(error)
                duration = 0.0
                exit_code = None
                timed_out = False
                output_preview = ''
                updated_arguments = None
                additional_context = ''
            else:
                decision = result.decision
                reason = result.reason
                duration = result.duration_seconds
                exit_code = result.exit_code
                timed_out = result.timed_out
                output_preview = result.output_preview
                updated_arguments = result.updated_arguments
                additional_context = result.additional_context
            modified = updated_arguments is not None
            if modified:
                arguments = dict(updated_arguments)
            if additional_context:
                contexts.append(additional_context)
            execution = HookExecution(
                hook_id=spec.id,
                event=event.name,
                decision=decision,
                duration_seconds=duration,
                exit_code=exit_code,
                timed_out=timed_out,
                arguments_modified=modified,
                context_injected=bool(additional_context),
                reason=reason,
                output_preview=output_preview,
            )
            executions.append(execution)
            if (
                decision in {'deny', 'error'}
                and event.name in BLOCKING_EVENTS
            ):
                return HookOutcome(
                    allowed=False,
                    arguments=arguments,
                    additional_context=tuple(contexts),
                    reason=reason or f'Hook {spec.id} denied {event.name}.',
                    executions=tuple(executions),
                )
        return HookOutcome(
            arguments=arguments,
            additional_context=tuple(contexts),
            executions=tuple(executions),
        )


def _matches(spec: HookSpec, event: HookEvent) -> bool:
    tools = spec.matcher.tools
    if tools and not any(
        fnmatchcase(event.tool_name or '', pattern) for pattern in tools
    ):
        return False
    paths = spec.matcher.paths
    if paths and not any(
        _path_matches(path, pattern)
        for path in event.paths
        for pattern in paths
    ):
        return False
    return True


def _path_matches(path: str, pattern: str) -> bool:
    normalized = Path(path).as_posix()
    normalized_pattern = Path(pattern).as_posix()
    return fnmatchcase(normalized, normalized_pattern) or (
        normalized_pattern.startswith('**/')
        and fnmatchcase(normalized, normalized_pattern[3:])
    )
