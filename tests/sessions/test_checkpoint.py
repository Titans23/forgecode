'''Tests for M4 file-level edit checkpoints.'''

from pathlib import Path
import shutil

import pytest

from forge.sessions.checkpoint import (
    CheckpointConflictError,
    CheckpointError,
    CheckpointStore,
)


def test_checkpoint_capture_and_restore_in_long_control_directory(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    target = root / 'app.py'
    target.write_bytes(b'before\r\n')
    directory = tmp_path / 'long-control'
    while len(str(directory)) < 280:
        directory /= 'nested-checkpoint-storage-12345678'
    store = CheckpointStore(root, directory)
    try:
        checkpoint = store.begin()
        store.capture_before(checkpoint, ('app.py',))
        target.write_bytes(b'after\r\n')
        store.record_after(checkpoint, ('app.py',))
        assert store.latest_restorable() == checkpoint
        assert store.restore(checkpoint) == ('app.py',)
        assert target.read_bytes() == b'before\r\n'
        assert not list(store.directory.rglob('.forge-*.tmp'))
    finally:
        assert directory.resolve().is_relative_to(tmp_path.resolve())
        if store.directory.exists():
            shutil.rmtree(store.directory)


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


def test_checkpoint_restores_deleted_directory_tree(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    nested = root / 'play' / '.tmp' / 'nested'
    nested.mkdir(parents=True)
    file = nested / 'probe.txt'
    file.write_text('before', encoding='utf-8')
    paths = (
        'play/.tmp',
        'play/.tmp/nested',
        'play/.tmp/nested/probe.txt',
    )
    store = CheckpointStore(root, tmp_path / 'checkpoints')
    checkpoint_id = store.begin()
    store.capture_before(checkpoint_id, paths)
    file.unlink()
    nested.rmdir()
    (root / 'play' / '.tmp').rmdir()
    store.record_after(checkpoint_id, paths)

    restored = store.restore(checkpoint_id)

    assert restored == paths
    assert file.read_text(encoding='utf-8') == 'before'


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


def test_checkpoint_accepts_absolute_path_inside_repository(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    target = root / 'output.txt'
    store = CheckpointStore(root, tmp_path / 'checkpoints')
    checkpoint_id = store.begin()

    captured = store.capture_before(checkpoint_id, (str(target),))
    target.write_text('created', encoding='utf-8')
    store.record_after(checkpoint_id, (str(target),))

    assert captured == ('output.txt',)
    assert store.restore(checkpoint_id) == ('output.txt',)
    assert not target.exists()


def test_checkpoint_rejects_relative_path_outside_repository(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    store = CheckpointStore(root, tmp_path / 'checkpoints')
    checkpoint_id = store.begin()

    with pytest.raises(CheckpointError, match='escapes the repository'):
        store.capture_before(checkpoint_id, ('../outside.txt',))


def test_checkpoint_restores_explicit_absolute_path_outside_repository(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    outside = tmp_path / 'external' / 'output.txt'
    outside.parent.mkdir()
    outside.write_text('before', encoding='utf-8')
    store = CheckpointStore(root, tmp_path / 'checkpoints')
    checkpoint_id = store.begin()

    captured = store.capture_before(checkpoint_id, (str(outside),))
    outside.write_text('after', encoding='utf-8')
    store.record_after(checkpoint_id, (str(outside),))

    assert captured[0].startswith('@external/')
    assert store.restore(checkpoint_id) == captured
    assert outside.read_text(encoding='utf-8') == 'before'


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
