'''Resolve model-visible tools and prompt guidance from one loop phase.'''

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Literal

from forge.runtime.recovery import RecoveryState


class LoopPhase(StrEnum):
    NORMAL = 'normal'
    READ_ONLY = 'read_only'
    RECOVERY_INSPECT = 'recovery_inspect'
    RECOVERY_ACT = 'recovery_act'
    RECOVERY_VERIFY = 'recovery_verify'
    FINALIZE = 'finalize'


FinalizeMode = Literal['none', 'task_state', 'completion', 'read_only']
ReadOnlyMode = Literal['none', 'inspect', 'task_query']


@dataclass(frozen=True, slots=True)
class PhaseResolution:
    phase: LoopPhase
    tools: list[dict[str, Any]] | None
    prompt_suffix: str
    enforce_declared_tools: bool


def resolve_phase(
    definitions: list[dict[str, Any]] | None,
    effect: Callable[[str], str],
    recovery: RecoveryState,
    *,
    finalize: FinalizeMode = 'none',
    read_only: ReadOnlyMode = 'none',
    finish_only: bool = False,
    preserve_active_task: bool = False,
) -> PhaseResolution:
    '''Return one coherent phase: its tools and its model-visible contract.'''
    available = list(definitions or ())
    if finalize != 'none':
        reason = {
            'task_state': 'Synthesize the requested task state from existing evidence.',
            'completion': 'Return the concise final outcome from existing evidence.',
            'read_only': (
                'Answer directly from the repository evidence already collected.'
            ),
        }[finalize]
        return PhaseResolution(LoopPhase.FINALIZE, None, reason, True)
    if finish_only:
        return PhaseResolution(
            LoopPhase.FINALIZE,
            _named(available, {'finish_task'}),
            'Declare the evidence-backed task outcome now; do not do more work.',
            True,
        )
    if read_only != 'none' or preserve_active_task:
        names = {'task_get'} if read_only == 'task_query' else None
        tools = (
            _named(available, names)
            if names is not None
            else _by_effect(available, effect, {'read_only'}, exclude=_TASK_WRITES)
        )
        return PhaseResolution(
            LoopPhase.READ_ONLY,
            tools,
            'Inspect only when needed and answer from concrete evidence.',
            False,
        )
    if recovery.active:
        if recovery.kind == 'protocol':
            return PhaseResolution(
                LoopPhase.NORMAL,
                available or None,
                'Retry the malformed tool call once with corrected arguments.',
                False,
            )
        action = recovery.required_next_action
        if action == 'inspect':
            tools = _recovery_inspect_tools(available, effect)
            return PhaseResolution(
                LoopPhase.RECOVERY_INSPECT,
                tools,
                'Inspect the exact failure once, then choose a corrected action.',
                True,
            )
        if action == 'verify':
            return PhaseResolution(
                LoopPhase.RECOVERY_VERIFY,
                _named(available, {'verify'}),
                'Run the exact required verification on the current revision.',
                True,
            )
        tools = _recovery_act_tools(available, effect)
        return PhaseResolution(
            LoopPhase.RECOVERY_ACT,
            tools,
            'Take one concrete corrective action; do not repeat unchanged reads.',
            True,
        )
    return PhaseResolution(LoopPhase.NORMAL, available or None, '', False)


_TASK_WRITES = {'finish_task', 'task_plan', 'task_update'}


def _name(definition: dict[str, Any]) -> str:
    return str(definition.get('name', ''))


def _named(
    definitions: list[dict[str, Any]],
    names: set[str],
) -> list[dict[str, Any]] | None:
    selected = [item for item in definitions if _name(item) in names]
    return selected or None


def _by_effect(
    definitions: list[dict[str, Any]],
    effect: Callable[[str], str],
    effects: set[str],
    *,
    exclude: set[str] | None = None,
) -> list[dict[str, Any]] | None:
    excluded = exclude or set()
    selected = [
        item
        for item in definitions
        if _name(item) not in excluded and effect(_name(item)) in effects
    ]
    return selected or None


def _recovery_inspect_tools(
    definitions: list[dict[str, Any]],
    effect: Callable[[str], str],
) -> list[dict[str, Any]] | None:
    selected = _by_effect(
        definitions,
        effect,
        {'read_only'},
        exclude={'finish_task', 'task_update'},
    ) or []
    selected.extend(
        item for item in definitions
        if _name(item) in {'task_plan'} and item not in selected
    )
    return selected or None


def _recovery_act_tools(
    definitions: list[dict[str, Any]],
    effect: Callable[[str], str],
) -> list[dict[str, Any]] | None:
    selected = _by_effect(definitions, effect, {'workspace_write'}) or []
    action_names = {'task_update', 'run_command', 'verify', 'finish_task'}
    selected.extend(
        item for item in definitions
        if _name(item) in action_names and item not in selected
    )
    return selected or None
