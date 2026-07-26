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
    manager.start(
        'Fix all six block faces and verify the game.',
        requires_change=True,
    )

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
    assert resumed.requires_change is True
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


def test_followup_after_stuck_keeps_root_goal_and_latest_directive(
    tmp_path: Path,
) -> None:
    manager = TaskManager(tmp_path)
    original = manager.start('Fix the rendering bug in play/js/world.js')
    manager.stuck(('Repeated actions did not make progress.',))

    continued = manager.begin_turn('你直接帮我修复')

    assert continued.id == original.id
    assert continued.goal == original.goal
    assert continued.status == 'in_progress'
    suffix = manager.system_suffix()
    assert original.goal in suffix
    assert '你直接帮我修复' in suffix


def test_new_anaphoric_task_resolves_previous_directory_scope(
    tmp_path: Path,
) -> None:
    manager = TaskManager(tmp_path)
    visibility = manager.start('能看到play目录吗')
    manager.complete()

    game = manager.begin_turn('帮我在里面写一个高级版本的雷霆战机')

    assert game.id != visibility.id
    assert game.goal == (
        '在 play 目录下：帮我在里面写一个高级版本的雷霆战机'
    )
    assert game.scope_hints == ('play/**',)


@pytest.mark.parametrize(
    'directive',
    [
        '帮我将其工程化，并升级为超级复杂的植物大战僵尸',
        '把它升级为企业级版本',
    ],
)
def test_anaphoric_project_reference_inherits_previous_directory_scope(
    tmp_path: Path,
    directive: str,
) -> None:
    manager = TaskManager(tmp_path)
    previous = manager.start('详细介绍 play 目录中的内容')
    manager.complete()

    task = manager.begin_turn(directive, requires_change=True)

    assert task.id != previous.id
    assert task.scope_hints == ('play/**',)
    assert task.scope_source == 'inherited'
    assert task.goal.startswith('在 play 目录下：')
    assert manager.outside_scope(('play/src/game.js',)) == ()
    assert manager.outside_scope(('scratch.txt',)) == ('scratch.txt',)


def test_unresolved_anaphoric_scope_fails_closed(tmp_path: Path) -> None:
    manager = TaskManager(tmp_path)

    task = manager.start('帮我将其升级为复杂版本', requires_change=True)

    assert task.scope_hints == ()
    assert task.scope_source == 'unresolved'
    assert manager.outside_scope(('scratch.txt', 'play/game.js')) == (
        'scratch.txt',
        'play/game.js',
    )


def test_plan_without_scope_keeps_inferred_scope(tmp_path: Path) -> None:
    manager = TaskManager(tmp_path)
    manager.start('在 play 目录实现复杂游戏', requires_change=True)

    task = manager.plan(['分析结构', '实现功能'], scope_hints=[])

    assert task.scope_hints == ('play/**',)
    assert task.scope_source == 'explicit'


def test_continuation_after_completed_keeps_goal_and_inferred_scope(
    tmp_path: Path,
) -> None:
    manager = TaskManager(tmp_path)
    original = manager.start('Build an advanced game inside play')
    manager.observe_mutation_paths(('play/src/core/.gitkeep',))
    manager.complete()

    continued = manager.begin_turn('继续，允许你执行文件写入')

    assert continued.id == original.id
    assert continued.goal == original.goal
    assert continued.status == 'in_progress'
    assert continued.scope_hints == ('play/**',)
    assert manager.outside_scope(('play/index.html',)) == ()
    assert manager.outside_scope(('forge/runtime/state.py',)) == (
        'forge/runtime/state.py',
    )
    suffix = manager.system_suffix()
    assert original.goal in suffix
    assert '继续，允许你执行文件写入' in suffix
    assert 'play/**' in suffix


def test_legacy_task_can_be_upgraded_to_change_contract(
    tmp_path: Path,
) -> None:
    manager = TaskManager(tmp_path)
    legacy = manager.start(
        '当前play下的植物大战僵尸还是太简单了，我想复刻原版'
    )

    upgraded = manager.require_workspace_change()

    assert legacy.requires_change is False
    assert upgraded is not None
    assert upgraded.id == legacy.id
    assert upgraded.requires_change is True


@pytest.mark.parametrize(
    'directive',
    [
        '要求动画、贴图还有功能都需要',
        '按顺序',
        '可以开始工作吧',
    ],
)
def test_change_followup_keeps_goal_and_change_contract(
    tmp_path: Path,
    directive: str,
) -> None:
    manager = TaskManager(tmp_path)
    original = manager.start(
        '当前play下的植物大战僵尸还是太简单了，我想复刻原版',
        requires_change=True,
    )
    manager.complete()

    continued = manager.begin_turn(directive)

    assert continued.id == original.id
    assert continued.goal == original.goal
    assert continued.requires_change is True
    assert continued.status == 'in_progress'
    assert directive in manager.system_suffix()
    assert 'Requires workspace change: true' in manager.system_suffix()


def test_non_continuation_after_completed_starts_a_new_task(
    tmp_path: Path,
) -> None:
    manager = TaskManager(tmp_path)
    original = manager.start('Build a game')
    manager.complete()

    following = manager.begin_turn('更新 README')

    assert following.id != original.id
    assert following.goal == '更新 README'


def test_resume_rejects_invalid_task_id(tmp_path: Path) -> None:
    manager = TaskManager(tmp_path)

    with pytest.raises(ValueError, match='Invalid task ID'):
        manager.resume('../../outside')


def test_observed_workspace_paths_are_limited_to_task_scope(
    tmp_path: Path,
) -> None:
    manager = TaskManager(tmp_path)
    manager.start('在 play 目录实现复杂游戏', requires_change=True)

    manager.observe_mutation_paths(
        ('play/src/main.js', 'scratch.txt', '@tool:apply_patch')
    )

    assert manager.active is not None
    assert manager.active.workspace_paths == ('play/src/main.js',)
