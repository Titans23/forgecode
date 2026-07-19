'''Tests for model-visible optional task planning tools.'''

import asyncio
from pathlib import Path

from forge.tasks.manager import TaskManager
from forge.tools.base import ToolRegistry
from forge.tools.task import create_task_tools


def test_task_tools_create_and_advance_persistent_plan(
    tmp_path: Path,
) -> None:
    manager = TaskManager(tmp_path)
    manager.start('Implement and verify the feature')
    registry = ToolRegistry(create_task_tools(tmp_path, manager))

    planned = asyncio.run(
        registry.execute(
            'task_plan',
            {
                'steps': ['Inspect implementation', 'Implement fix', 'Test'],
                'scope_hints': ['forge/'],
            },
        )
    )
    updated = asyncio.run(
        registry.execute(
            'task_update',
            {
                'step_id': 'step-1',
                'status': 'completed',
                'evidence': ['Read the relevant runtime files.'],
            },
        )
    )

    assert planned.success is True
    assert updated.success is True
    assert manager.active is not None
    assert manager.active.current_step_id == 'step-2'
    assert manager.store.current_path.exists()


def test_task_plan_rejects_simple_one_step_plan(tmp_path: Path) -> None:
    manager = TaskManager(tmp_path)
    manager.start('Read one file')
    registry = ToolRegistry(create_task_tools(tmp_path, manager))

    result = asyncio.run(
        registry.execute('task_plan', {'steps': ['Read README']})
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'invalid_arguments'
    assert manager.active is not None
    assert manager.active.planned is False
    assert not manager.store.current_path.exists()
