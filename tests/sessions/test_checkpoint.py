'''Tests for M4 file-level edit checkpoints.'''

from pathlib import Path

import pytest

from forge.sessions.checkpoint import (
    CheckpointConflictError,
    CheckpointError,
    CheckpointStore,
)


def test_checkpoint_restores_modified_and_created_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    existing = root / 'existing.txt'
    existing.write_text('before', encoding='utf-8')
    created = root / 'created.txt'
    store = CheckpointStore(root, tmp_path / 'checkpoints')
    checkpoint_id = store.begin()
    store.capture_before(
        checkpoint_id,
        ('existing.txt', 'created.txt'),
    )
    existing.write_text('after', encoding='utf-8')
    created.write_text('new', encoding='utf-8')
    store.record_after(
        checkpoint_id,
        ('existing.txt', 'created.txt'),
    )

    restored = store.restore(checkpoint_id)

    assert restored == ('existing.txt', 'created.txt')
    assert existing.read_text(encoding='utf-8') == 'before'
    assert not created.exists()


def test_checkpoint_refuses_to_overwrite_external_change(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    target = root / 'app.py'
    target.write_text('before', encoding='utf-8')
    store = CheckpointStore(root, tmp_path / 'checkpoints')
    checkpoint_id = store.begin()
    store.capture_before(checkpoint_id, ('app.py',))
    target.write_text('agent edit', encoding='utf-8')
    store.record_after(checkpoint_id, ('app.py',))
    target.write_text('user edit', encoding='utf-8')

    with pytest.raises(CheckpointConflictError) as captured:
        store.restore(checkpoint_id)

    assert captured.value.paths == ('app.py',)
    assert target.read_text(encoding='utf-8') == 'user edit'


def test_checkpoint_rejects_path_outside_repository(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = CheckpointStore(root, tmp_path / 'checkpoints')
    checkpoint_id = store.begin()

    with pytest.raises(CheckpointError):
        store.capture_before(checkpoint_id, ('../outside.txt',))


def test_checkpoint_deduplicates_original_blob(tmp_path: Path) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    first = root / 'first.txt'
    second = root / 'second.txt'
    first.write_text('same', encoding='utf-8')
    second.write_text('same', encoding='utf-8')
    store = CheckpointStore(root, tmp_path / 'checkpoints')
    checkpoint_id = store.begin()

    store.capture_before(checkpoint_id, ('first.txt', 'second.txt'))

    assert len(tuple(store.blob_directory.iterdir())) == 1


def test_checkpoint_prune_keeps_only_latest_manifests(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = CheckpointStore(root, tmp_path / 'checkpoints')
    checkpoint_ids = [store.begin() for _ in range(4)]

    removed = store.prune(maximum=2)

    assert set(removed) == set(checkpoint_ids[:2])
    assert len(store.list()) == 2
