'''Tests for the compact loop phase and recovery state.'''

from forge.runtime.phase import LoopPhase, resolve_phase
from forge.runtime.recovery import RecoveryState, failure_fingerprint


def _definitions() -> list[dict[str, str]]:
    return [
        {'name': 'read_file'},
        {'name': 'apply_patch'},
        {'name': 'run_command'},
        {'name': 'verify'},
        {'name': 'finish_task'},
        {'name': 'task_plan'},
    ]


def _effect(name: str) -> str:
    if name == 'apply_patch':
        return 'workspace_write'
    if name in {'read_file', 'finish_task', 'task_plan'}:
        return 'read_only'
    return 'process'


def test_recovery_counts_only_same_failure_without_progress() -> None:
    state = RecoveryState()
    fingerprint = failure_fingerprint(
        'verify', 'pytest', 'tests/test_app.py', 'assert 1 == 2'
    )

    state.activate(
        'verify', 'inspect', fingerprint=fingerprint, revision=2
    )
    state.activate(
        'verify', 'inspect', fingerprint=fingerprint, revision=2
    )

    assert state.attempts == 2
    assert state.exhausted()
    assert state.required_next_action == 'act'
    state.note_progress(3)
    assert state.active is False


def test_failure_fingerprint_normalizes_equivalent_errors() -> None:
    left = failure_fingerprint(
        'edit', 'Apply_Patch', 'src/App.py', 'Context   NOT found\n'
    )
    right = failure_fingerprint(
        'edit', 'apply_patch', 'src/app.py', 'context not FOUND'
    )

    assert left == right


def test_phase_resolver_keeps_tools_and_prompt_in_one_decision() -> None:
    state = RecoveryState()
    state.activate(
        'verify',
        'inspect',
        fingerprint='verify|pytest|tests|failed',
        revision=1,
    )

    resolution = resolve_phase(_definitions(), _effect, state)

    assert resolution.phase == LoopPhase.RECOVERY_INSPECT
    assert resolution.enforce_declared_tools is True
    assert 'Inspect the exact failure' in resolution.prompt_suffix
    assert {tool['name'] for tool in resolution.tools or []} == {
        'read_file',
        'task_plan',
    }


def test_finalize_phase_closes_tools_and_explains_synthesis() -> None:
    resolution = resolve_phase(
        _definitions(),
        _effect,
        RecoveryState(),
        finalize='completion',
    )

    assert resolution.phase == LoopPhase.FINALIZE
    assert resolution.tools is None
    assert 'final outcome' in resolution.prompt_suffix
