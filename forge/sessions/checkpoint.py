'''File-level checkpoints for edits made by ForgeCode write tools.'''

from __future__ import annotations

from datetime import datetime
import base64
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable
from uuid import uuid4

from forge.storage_paths import private_storage_path


CHECKPOINT_PATTERN = re.compile(r'checkpoint-[0-9a-f]{20}')


class CheckpointError(RuntimeError):
    '''Base error for file checkpoint operations.'''


class CheckpointConflictError(CheckpointError):
    '''Raised when files changed outside the recorded edit sequence.'''

    def __init__(self, paths: tuple[str, ...]) -> None:
        self.paths = paths
        super().__init__(
            'Checkpoint restore would overwrite external changes: '
            + ', '.join(paths)
        )


class CheckpointStore:
    '''Capture pre-edit blobs and safely restore one session checkpoint.'''

    def __init__(self, root: Path, directory: Path) -> None:
        self.root = root.resolve()
        # Checkpoint hashes can push the private control path past MAX_PATH
        # even when the project path is short and Windows long paths are off.
        self.directory = private_storage_path(directory)
        self.blob_directory = self.directory / 'blobs'

    @classmethod
    def for_session(
        cls,
        root: Path,
        session_path: Path,
        session_id: str,
    ) -> CheckpointStore:
        project_directory = session_path.parent.parent
        return cls(
            root,
            project_directory / 'checkpoints' / session_id,
        )

    def begin(self) -> str:
        checkpoint_id = f'checkpoint-{uuid4().hex[:20]}'
        manifest = {
            'schema_version': 1,
            'checkpoint_id': checkpoint_id,
            'created_at': datetime.now().astimezone().isoformat(),
            'root': str(self.root),
            'files': {},
        }
        self._write_manifest(checkpoint_id, manifest)
        self.prune()
        return checkpoint_id

    def capture_before(
        self,
        checkpoint_id: str,
        paths: Iterable[str],
    ) -> tuple[str, ...]:
        manifest = self._load_manifest(checkpoint_id)
        files = manifest['files']
        captured: list[str] = []
        for value in paths:
            relative, target = self._resolve(value)
            if relative in files:
                continue
            entry = self._snapshot(target)
            if entry.get('skipped'):
                files[relative] = entry
                continue
            files[relative] = entry
            captured.append(relative)
        self._write_manifest(checkpoint_id, manifest)
        return tuple(captured)

    def capture_observations(self, checkpoint_id, observations):
        '''Persist authorized helper bytes without reading task paths on the host.'''
        manifest = self._load_manifest(checkpoint_id)
        manifest['restricted_io'] = True
        for path, value in observations.items():
            relative = self._observation_key(path)
            if relative in manifest['files']:
                continue
            if value['exists'] and value['is_file']:
                content = base64.b64decode(value['content_base64'], validate=True)
                digest = hashlib.sha256(content).hexdigest()
                if digest != value['sha256']:
                    raise CheckpointError('Authorized checkpoint content hash differs')
                self.blob_directory.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(self.blob_directory / digest, content)
            manifest['files'][relative] = {'before_exists': value['exists'], 'before_sha256': value['sha256'],
                'before_directory': value['exists'] and not value['is_file']}
        self._write_manifest(checkpoint_id, manifest)

    def record_observations(self, checkpoint_id, observations):
        manifest = self._load_manifest(checkpoint_id)
        for path, value in observations.items():
            entry = manifest['files'].get(self._observation_key(path))
            if entry is not None:
                entry.update(after_exists=value['exists'], after_sha256=value['sha256'])
        self._write_manifest(checkpoint_id, manifest)

    def _observation_key(self, raw):
        path = Path(os.path.abspath(self.root / raw))
        if not path.is_relative_to(self.root):
            raise CheckpointError('Restricted checkpoint target is outside the workspace')
        return path.relative_to(self.root).as_posix()

    def record_after(
        self,
        checkpoint_id: str,
        paths: Iterable[str],
    ) -> None:
        manifest = self._load_manifest(checkpoint_id)
        files = manifest['files']
        for value in paths:
            relative, target = self._resolve(value)
            entry = files.get(relative)
            if not isinstance(entry, dict) or entry.get('skipped'):
                continue
            after = file_identity(target)
            entry['after_exists'] = after['exists']
            entry['after_sha256'] = after['sha256']
        self._write_manifest(checkpoint_id, manifest)

    def restore(self, checkpoint_id: str) -> tuple[str, ...]:
        manifest = self._load_manifest(checkpoint_id)
        if manifest.get('restricted_io'):
            raise CheckpointError('Restricted checkpoint restore requires the authorized restore action; host restore is refused.')
        conflicts: list[str] = []
        restorable: list[tuple[str, Path, dict[str, Any]]] = []
        for relative, raw_entry in manifest['files'].items():
            if not isinstance(raw_entry, dict) or raw_entry.get('skipped'):
                continue
            target = self._resolve_manifest_target(relative, raw_entry)
            current = file_identity(target)
            expected_exists = raw_entry.get(
                'after_exists',
                raw_entry.get('before_exists'),
            )
            expected_hash = raw_entry.get(
                'after_sha256',
                raw_entry.get('before_sha256'),
            )
            if (
                current['exists'] != expected_exists
                or current['sha256'] != expected_hash
            ):
                conflicts.append(relative)
            else:
                restorable.append((relative, target, raw_entry))
        if conflicts:
            raise CheckpointConflictError(tuple(sorted(conflicts)))

        restored: list[str] = []
        for relative, target, entry in restorable:
            if entry.get('before_directory'):
                target.mkdir(parents=True, exist_ok=True)
            elif entry.get('before_exists'):
                blob_hash = str(entry.get('before_sha256', ''))
                blob = self.blob_directory / blob_hash
                try:
                    content = blob.read_bytes()
                except OSError as error:
                    raise CheckpointError(
                        f'Missing checkpoint blob for {relative}'
                    ) from error
                if hashlib.sha256(content).hexdigest() != blob_hash:
                    raise CheckpointError(
                        f'Checkpoint blob hash mismatch for {relative}'
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(target, content)
            elif target.exists():
                target.unlink()
            restored.append(relative)
        return tuple(restored)

    def list(self) -> tuple[str, ...]:
        if not self.directory.exists():
            return ()
        return tuple(
            path.stem
            for path in sorted(
                self.directory.glob('checkpoint-*.json'),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        )

    def latest_restorable(self) -> str | None:
        '''Return the newest checkpoint containing at least one file snapshot.'''
        for checkpoint_id in self.list():
            try:
                manifest = self._load_manifest(checkpoint_id)
            except CheckpointError:
                continue
            if any(
                isinstance(entry, dict) and not entry.get('skipped')
                for entry in manifest['files'].values()
            ):
                return checkpoint_id
        return None

    def prune(
        self,
        *,
        maximum: int = 100,
        retention_days: int = 30,
    ) -> tuple[str, ...]:
        if maximum < 1:
            raise ValueError('maximum must be positive')
        if retention_days < 1:
            raise ValueError('retention_days must be positive')
        if not self.directory.exists():
            return ()
        manifests = sorted(
            self.directory.glob('checkpoint-*.json'),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        now = datetime.now().astimezone().timestamp()
        retention_seconds = retention_days * 24 * 60 * 60
        removed: list[str] = []
        for index, path in enumerate(manifests):
            expired = now - path.stat().st_mtime > retention_seconds
            if index >= maximum or expired:
                path.unlink()
                removed.append(path.stem)

        referenced: set[str] = set()
        for path in self.directory.glob('checkpoint-*.json'):
            try:
                manifest = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            files = manifest.get('files', {})
            if not isinstance(files, dict):
                continue
            referenced.update(
                str(entry['before_sha256'])
                for entry in files.values()
                if isinstance(entry, dict)
                and entry.get('before_exists')
                and entry.get('before_sha256')
            )
        if self.blob_directory.exists():
            for blob in self.blob_directory.iterdir():
                if blob.is_file() and blob.name not in referenced:
                    blob.unlink()
        return tuple(removed)

    def _snapshot(self, target: Path) -> dict[str, Any]:
        if target.is_symlink():
            return {'skipped': 'symbolic links are not checkpointed'}
        if target.exists() and target.is_dir():
            return {
                'before_exists': True,
                'before_sha256': None,
                'before_directory': True,
            }
        if target.exists() and target.stat().st_nlink > 1:
            return {'skipped': 'hard-linked files are not checkpointed'}
        identity = file_identity(target)
        if identity['exists']:
            content = target.read_bytes()
            self.blob_directory.mkdir(parents=True, exist_ok=True)
            blob = self.blob_directory / str(identity['sha256'])
            if not blob.exists():
                atomic_write_bytes(blob, content)
        return {
            'before_exists': identity['exists'],
            'before_sha256': identity['sha256'],
            **(
                {'target_path': target.as_posix()}
                if not self._is_inside_root(target)
                else {}
            ),
        }

    def _resolve(self, value: str) -> tuple[str, Path]:
        raw = Path(value)
        target = (
            raw.resolve(strict=False)
            if raw.is_absolute()
            else (self.root / raw).resolve(strict=False)
        )
        if not raw.is_absolute() and not self._is_inside_root(target):
            raise CheckpointError(
                f'Checkpoint path escapes the repository: {value}'
            )
        if self._is_inside_root(target):
            return target.relative_to(self.root).as_posix(), target
        return self._external_key(target), target

    def _resolve_manifest_target(
        self,
        storage_key: str,
        entry: dict[str, Any],
    ) -> Path:
        raw_target = entry.get('target_path')
        if raw_target is not None:
            target = Path(str(raw_target))
            if not target.is_absolute():
                raise CheckpointError(
                    f'Invalid external checkpoint target: {raw_target}'
                )
            return target.resolve(strict=False)
        return self._resolve(storage_key)[1]

    def _is_inside_root(self, target: Path) -> bool:
        try:
            target.relative_to(self.root)
        except ValueError:
            return False
        return True

    @staticmethod
    def _external_key(target: Path) -> str:
        digest = hashlib.sha256(
            target.as_posix().encode('utf-8')
        ).hexdigest()[:32]
        return f'@external/{digest}/{target.name}'

    def _manifest_path(self, checkpoint_id: str) -> Path:
        if CHECKPOINT_PATTERN.fullmatch(checkpoint_id) is None:
            raise CheckpointError(
                f'Invalid checkpoint ID: {checkpoint_id}'
            )
        return self.directory / f'{checkpoint_id}.json'

    def _load_manifest(self, checkpoint_id: str) -> dict[str, Any]:
        path = self._manifest_path(checkpoint_id)
        try:
            manifest = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            raise CheckpointError(
                f'Cannot load checkpoint: {checkpoint_id}'
            ) from error
        if (
            not isinstance(manifest, dict)
            or not isinstance(manifest.get('files'), dict)
            or Path(str(manifest.get('root', ''))).resolve() != self.root
        ):
            raise CheckpointError(
                f'Invalid checkpoint manifest: {checkpoint_id}'
            )
        return manifest

    def _write_manifest(
        self,
        checkpoint_id: str,
        manifest: dict[str, Any],
    ) -> None:
        path = self._manifest_path(checkpoint_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        )
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(serialized + '\n', encoding='utf-8')
        temporary.replace(path)


def file_identity(path: Path) -> dict[str, str | bool | None]:
    if not path.exists():
        return {'exists': False, 'sha256': None}
    if not path.is_file():
        return {'exists': True, 'sha256': None}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'exists': True, 'sha256': digest}


def atomic_write_bytes(path: Path, content: bytes) -> None:
    # Keep the temporary in the destination directory for atomic replacement,
    # but do not repeat a potentially long content hash in its filename. This
    # avoids Windows MAX_PATH failures in deep project/session directories.
    temporary = path.with_name(f'.forge-{uuid4().hex[:12]}.tmp')
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
