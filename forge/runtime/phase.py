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
        # Finalization is a prompt hint, not a tool or authorization phase.
        # Closing tools here used to strand a model that discovered one last
        # necessary correction while synthesizing its answer.
        return PhaseResolution(
            LoopPhase.FINALIZE,
            available or None,
            reason,
            False,
        )
    if finish_only:
        return PhaseResolution(
            LoopPhase.FINALIZE,
            available or None,
            'The current evidence satisfies the completion contract; return the '
            'final outcome or make a necessary final correction.',
            False,
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
            # Recovery is guidance, not an authorization boundary.  Keeping a
            # second recovery allow-list caused legitimate reads and retries to
            # be hidden after a failure.  The executor remains responsible for
            # validation and authorization.
            tools = available or None
            prompt = 'Inspect the exact failure once, then choose a corrected action.'
            if recovery.kind == 'edit':
                # A failed edit often already includes the closest current text
                # and target path. Keep correction tools available so a model
                # can repair the payload immediately instead of getting stuck
                # behind an inspect-only phase.
                tools = available or None
                prompt = (
                    'Use the edit failure details to inspect the current target '
                    'if needed, then make one concrete corrected edit or retry '
                    'the appropriate command; do not repeat the rejected payload.'
                )
            return PhaseResolution(
                (
                    LoopPhase.RECOVERY_ACT
                    if recovery.kind == 'edit'
                    else LoopPhase.RECOVERY_INSPECT
                ),
                tools,
                prompt,
                False,
            )
        if action == 'verify':
            return PhaseResolution(
                LoopPhase.RECOVERY_VERIFY,
                available or None,
                'Run the exact required verification on the current revision.',
                False,
            )
        tools = available or None
        return PhaseResolution(
            LoopPhase.RECOVERY_ACT,
            tools,
            (
                'Use the process failure output to choose a concrete correction. '
                'Inspect relevant files if needed, then edit, retry an alternative '
                'command, or verify; do not repeat the failed command unchanged.'
                if recovery.kind == 'process'
                else 'Take one concrete corrective action; do not repeat unchanged reads.'
            ),
            False,
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
