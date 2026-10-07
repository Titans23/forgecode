"""Real Git status must describe the selected workspace, not its parent."""
import asyncio
import subprocess

import pytest

from forge.runtime.workspace import WorkspaceTracker


@pytest.mark.parametrize('ignored', [False, True])
def test_parent_repository_changes_do_not_invalidate_workspace(tmp_path, ignored):
    def git(*args):
        return subprocess.run(['git', *args], cwd=tmp_path, capture_output=True,
                              text=True, check=True)

    git('init')
    project = tmp_path / 'project'
    project.mkdir()
    target = project / 'value.txt'
    target.write_text('baseline', encoding='utf-8')
    if ignored:
        (tmp_path / '.gitignore').write_text('project/\n', encoding='utf-8')
    else:
        git('add', '--', 'project/value.txt')
    tracker = WorkspaceTracker(project)

    async def run():
        await tracker.begin_turn()
        await tracker.watch_paths_async(('value.txt',))
        (tmp_path / 'unrelated-evidence.json').write_text('{}', encoding='utf-8')
        assert await tracker.refresh() is None
        assert tracker.revision == 0
        assert tracker.changed_paths == ()
        target.write_text('actual mutation', encoding='utf-8')
        changed = await tracker.refresh()
        assert changed.paths == ('value.txt',)
        assert tracker.revision == 1
        assert tracker.changed_paths == ('value.txt',)

    asyncio.run(run())


def test_ignored_workspace_detects_unwatched_external_edit(tmp_path):
    subprocess.run(['git', 'init'], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / '.gitignore').write_text('project/\n', encoding='utf-8')
    project = tmp_path / 'project'
    project.mkdir()
    target = project / 'input.txt'
    target.write_text('before', encoding='utf-8')
    tracker = WorkspaceTracker(project)
    async def run():
        await tracker.begin_turn()
        target.write_text('after', encoding='utf-8')
        change = await tracker.refresh()
        assert change is not None and change.paths == ('input.txt',)
        assert tracker.available and not tracker.git_available
    asyncio.run(run())
