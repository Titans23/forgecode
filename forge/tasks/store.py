'''JSON persistence for planned ForgeCode tasks.'''

from __future__ import annotations

import json
from pathlib import Path
import re

from forge.tasks.state import ActiveTask


class TaskStore:
    '''Persist only planned tasks under the repository-local .forge folder.'''

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.directory = self.root / '.forge' / 'tasks'
        self.current_path = self.directory / 'current.json'

    def save(self, task: ActiveTask) -> Path:
        if not task.planned:
            raise ValueError('Only planned tasks are persisted.')
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f'{task.id}.json'
        serialized = json.dumps(
            task.as_dict(),
            ensure_ascii=False,
            indent=2,
        )
        self._write(path, serialized)
        self._write(self.current_path, serialized)
        return path

    def load(self, task_id: str) -> ActiveTask:
        if re.fullmatch(r'task-[0-9a-f]{12}', task_id) is None:
            raise ValueError(f'Invalid task ID: {task_id}')
        return self._read(self.directory / f'{task_id}.json')

    def load_current(self) -> ActiveTask | None:
        if not self.current_path.exists():
            return None
        return self._read(self.current_path)

    def list(self) -> tuple[ActiveTask, ...]:
        if not self.directory.exists():
            return ()
        tasks: list[ActiveTask] = []
        for path in sorted(self.directory.glob('task-*.json')):
            try:
                tasks.append(self._read(path))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return tuple(tasks)

    @staticmethod
    def _read(path: Path) -> ActiveTask:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError(f'Invalid task file: {path}')
        return ActiveTask.from_dict(data)

    @staticmethod
    def _write(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(content + '\n', encoding='utf-8')
        temporary.replace(path)
