'''Tests for current-task anchoring and optional persistent plans.'''

from pathlib import Path

import pytest

from forge.tasks.manager import TaskManager


def test_simple_task_stays_in_memory_without_creating_files(
    tmp_path: Path,
) -> None:
    manager = TaskManager(tmp_path)

    task = manager.start('Can you see the play directory?')

    assert task.planned is False
    assert 'Can you see the play directory?' in manager.system_suffix()
    assert not (tmp_path / '.forge' / 'tasks').exists()


def test_complex_plan_persists_updates_and_resumes(tmp_path: Path) -> None:
    manager = TaskManager(tmp_path)
    manager.start('Fix all six block faces and verify the game.')

    planned = manager.plan(
        ['Inspect geometry', 'Fix UVs', 'Verify'],
        constraints=['Focus on play'],
        scope_hints=['play/**'],
    )
    updated = manager.update_step(
        'step-1',
        'completed',
        evidence=['Read play/js/world.js'],
    )

    assert planned.planned is True
    assert updated.current_step_id == 'step-2'
    assert updated.steps[0].evidence == ('Read play/js/world.js',)
    assert (tmp_path / '.forge' / 'tasks' / f'{planned.id}.json').exists()

    restarted = TaskManager(tmp_path)
    resumed = restarted.resume(planned.id)

    assert resumed.goal == planned.goal
    assert resumed.current_step_id == 'step-2'
    assert 'Fix UVs' in restarted.system_suffix()

    continued = restarted.begin_turn('Continue from the saved task')
    following = restarted.begin_turn('Start a separate task')

    assert continued.id == planned.id
    assert continued.goal == planned.goal
    assert following.id != planned.id
    assert following.goal == 'Start a separate task'


def test_plan_is_optional_and_cannot_be_recreated_accidentally(
    tmp_path: Path,
) -> None:
    manager = TaskManager(tmp_path)
    manager.start('Complex task')
    manager.plan(['Inspect', 'Implement'])

    with pytest.raises(ValueError, match='already has a plan'):
        manager.plan(['Start over', 'Finish'])


def test_completion_and_blocking_are_persisted_for_planned_tasks(
    tmp_path: Path,
) -> None:
    manager = TaskManager(tmp_path)
    task = manager.start('Implement and verify')
    manager.plan(['Implement', 'Verify'])

    blocked = manager.block(('Verification failed.',))

    assert blocked is not None and blocked.status == 'blocked'
    assert manager.store.load(task.id).blocked_reasons == (
        'Verification failed.',
    )

    manager.resume(task.id)
    completed = manager.complete()

    assert completed is not None and completed.status == 'completed'
    assert manager.store.load(task.id).status == 'completed'


def test_resume_rejects_invalid_task_id(tmp_path: Path) -> None:
    manager = TaskManager(tmp_path)

    with pytest.raises(ValueError, match='Invalid task ID'):
        manager.resume('../../outside')
