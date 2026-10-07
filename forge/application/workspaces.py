"""Bounded, read-only project views and immutable task baselines.

Inspection executes no project code. Reverse patches are previews, never writes.
The authorization revision and observed content revision are independent.
"""
import asyncio
import base64
import difflib
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import time

from forge.application.models import ContractError
from forge.engine.persistence import encoded
from forge.sandbox.path_policy import PathPolicy, inspect_path, _stat_identity


MAX_FILES = 20000
TEXT_BYTES = 131072
SNAPSHOT_BYTES = 16777216


def text_kind(data):
    try:
        if b'\0' in data:
            return 'binary'
        data.decode('utf-8')
        return 'text'
    except UnicodeError:
        return 'binary'


class WorkspaceService:
    def __init__(self, service, cursors=None):
        self.service = service
        self.store = service.store
        self.cursors = cursors
        self.locks = {}

    def paths(self, workspace_id):
        root = Path(self.service._workspace(workspace_id)['canonical_path'])
        return PathPolicy(inspect_path(root), 'strict_allowlist_required', (root,), (), (self.store.data_dir,))

    @staticmethod
    def read(paths, relative, *, offset=0, length=TEXT_BYTES):
        authorized = paths.authorize(relative)
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
        fd = os.open(authorized.path, flags)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or _stat_identity(before) != authorized.anchors[-1][1]:
                raise ContractError('File changed before read', kind='STALE_FILE', code=-32010)
            authorized.assert_current()
            stream.seek(offset)
            data = stream.read(length)
            after = os.fstat(stream.fileno())
            authorized.assert_current()
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ContractError('File changed during read', kind='STALE_FILE', code=-32010)
        return data, before

    def scan(self, paths):
        files, contents, complete, budget = {}, {}, True, SNAPSHOT_BYTES
        started = time.monotonic()
        pending = [paths.workspace.path]
        while pending:
            directory = pending.pop()
            try:
                paths.authorize(directory).assert_current()
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if len(files) >= MAX_FILES or time.monotonic() - started > 5:
                            return files, contents, False
                        path = Path(entry.path)
                        relative = path.relative_to(paths.workspace.path).as_posix()
                        try:
                            if len(relative) > 4096:
                                raise ValueError('path length')
                            authorized = paths.authorize(path)
                            identity = path.lstat()
                            authorized.assert_current()
                            if stat.S_ISDIR(identity.st_mode):
                                files[relative] = {'kind': 'directory', 'size_bytes': None, 'classification': 'directory',
                                    'sha256': None, 'token': [identity.st_dev, identity.st_ino]}
                                pending.append(path)
                                continue
                            item = {'kind': 'file', 'size_bytes': identity.st_size, 'classification': 'large', 'sha256': None,
                                'token': [identity.st_dev, identity.st_ino, identity.st_mtime_ns, identity.st_ctime_ns]}
                            if identity.st_size <= TEXT_BYTES and identity.st_size <= budget:
                                data, observed = self.read(paths, relative)
                                if len(data) != identity.st_size or observed.st_mtime_ns != identity.st_mtime_ns:
                                    raise ContractError('File changed while scanning', kind='STALE_FILE', code=-32010)
                                item['sha256'] = sha256(data).hexdigest()
                                item['classification'] = text_kind(data)
                                item['token'] = [observed.st_dev, observed.st_ino, observed.st_mtime_ns, observed.st_ctime_ns]
                                contents[relative] = base64.b64encode(data).decode()
                                budget -= len(data)
                            elif identity.st_size <= TEXT_BYTES:
                                item['classification'] = 'quota'
                                complete = False
                            files[relative] = item
                        except (ContractError, OSError, ValueError):
                            # Sensitive and protected trees intentionally never appear.
                            # An unsafe or racing object cannot become a patch target.
                            if entry.name.casefold() not in ('.git', '.forge', '.env', 'credentials', 'credentials.json') and not entry.name.casefold().startswith('.env.'):
                                complete = False
            except (OSError, ContractError):
                complete = False
        return files, contents, complete

    async def refresh(self, workspace_id):
        async with self.locks.setdefault(workspace_id, asyncio.Lock()):
            paths = self.paths(workspace_id)
            files, contents, complete = await asyncio.to_thread(self.scan, paths)
            self.service._workspace(workspace_id)
            row = self.store.connection.execute('SELECT * FROM workspace_content WHERE workspace_id=?', (workspace_id,)).fetchone()
            previous = json.loads(row['snapshot_json']) if row else {}
            revision = row['revision'] if row else 0
            changed = sorted(path for path in files.keys() | previous.keys() if files.get(path) != previous.get(path))
            if changed or row is None:
                revision += 1
                with self.store.transaction():
                    self.store.connection.execute('INSERT INTO workspace_content VALUES(?,?,?) ON CONFLICT(workspace_id) '
                        'DO UPDATE SET revision=excluded.revision,snapshot_json=excluded.snapshot_json', (workspace_id, revision, encoded(files)))
                    self.store.connection.executemany('INSERT INTO workspace_change_log VALUES(?,?,?,?)',
                        [(workspace_id, revision, path, 'created' if path not in previous else 'deleted' if path not in files else 'modified') for path in changed])
                    self.store.connection.execute('DELETE FROM workspace_change_log WHERE workspace_id=? AND revision<?', (workspace_id, max(0, revision-100)))
            return revision, files, contents, complete

    def page(self, items, params, scope, *, gap=False):
        after = self.cursors.decode_cursor(params['cursor'], scope) if params.get('cursor') else 0
        limit = params.get('limit', 100)
        return {'items': items[after:after+limit], 'next_cursor': self.cursors.cursor(scope, after+limit) if after+limit < len(items) else None,
            'history_gap': gap}

    async def files(self, params):
        revision, files, _, complete = await self.refresh(params['workspace_id'])
        prefix = params.get('relative_path', '')
        if prefix:
            self.paths(params['workspace_id']).authorize(prefix)
        items = [{'relative_path': path, **{k: value[k] for k in ('kind', 'size_bytes')}, 'revision': revision}
            for path, value in sorted(files.items()) if not prefix or path == prefix or path.startswith(prefix.rstrip('/') + '/')]
        result = self.page(items, params, {'collection': 'workspace.files', 'workspace': params['workspace_id'], 'revision': revision, 'prefix': prefix}, gap=not complete)
        return {**result, 'revision': revision}

    async def changes(self, params):
        revision, _, _, complete = await self.refresh(params['workspace_id'])
        if params['after_revision'] > revision:
            raise ContractError('Content revision is in the future', kind='STALE_REVISION', code=-32010)
        items = [dict(row) for row in self.store.connection.execute('SELECT relative_path,change,revision FROM workspace_change_log '
            'WHERE workspace_id=? AND revision>? ORDER BY revision,relative_path', (params['workspace_id'], params['after_revision']))]
        return {**self.page(items, params, {'collection': 'workspace.changes', 'workspace': params['workspace_id'],
            'revision': revision, 'after': params['after_revision']}, gap=not complete or params['after_revision'] < revision-100), 'revision': revision}

    async def read_file(self, params):
        revision, files, _, _ = await self.refresh(params['workspace_id'])
        if revision != params['expected_revision']:
            raise ContractError('Content revision changed; refresh the file view', kind='STALE_REVISION', code=-32010)
        item = files.get(params['relative_path'])
        if not item or item['kind'] != 'file':
            raise ContractError('Inspectable file not found', kind='NOT_FOUND', code=-32010)
        small = item['sha256'] is not None
        data, identity = await asyncio.to_thread(self.read, self.paths(params['workspace_id']), params['relative_path'],
            offset=0 if small else params['offset'], length=TEXT_BYTES if small else params['length'])
        same = (sha256(data).hexdigest() == item['sha256'] and item['token'][:2] == [identity.st_dev, identity.st_ino]) if small else (
            item['token'] == [identity.st_dev, identity.st_ino, identity.st_mtime_ns, identity.st_ctime_ns])
        if not same or item['size_bytes'] != identity.st_size:
            raise ContractError('File changed; refresh before reading', kind='STALE_FILE', code=-32010)
        if small:
            data = data[params['offset']:params['offset']+params['length']]
        artifact = self.store.publish_artifact(data, origin='workspace.read_file', classification='source', max_bytes=262144)
        return {'revision': revision, 'data_base64': base64.b64encode(data).decode(), 'eof': params['offset']+len(data) >= identity.st_size,
            'artifact': {'artifact_id': artifact['id'], 'size_bytes': artifact['size'], 'sha256': artifact['sha256'],
                'media_type': 'application/octet-stream', 'redaction_version': 'none', 'available': True}}

    async def original_dirty(self, workspace_id):
        root = self.paths(workspace_id).workspace.path
        git = shutil.which('git')
        if not git or Path(git).resolve().is_relative_to(root):
            return {'status': 'unavailable', 'items': []}
        environment = {k: v for k, v in os.environ.items() if k in ('PATH', 'SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP')}
        environment.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'})
        environment['GIT_CEILING_DIRECTORIES'] = str(root.parent)
        from forge.release.processes import external_argv, external_options
        process = await asyncio.create_subprocess_exec(*external_argv([str(Path(git).resolve()), '--no-optional-locks', '-c', 'core.fsmonitor=false',
            '-c', 'core.untrackedCache=false', '-c', 'core.hooksPath=' + str(self.store.data_dir / 'disabled-hooks'),
            'status', '--porcelain=v1', '-z', '--untracked-files=all']), cwd=root, env=environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,**external_options())
        async def collect():
            data = bytearray()
            while chunk := await process.stdout.read(65536):
                data.extend(chunk)
                if len(data) > 1048576:
                    raise ValueError('dirty list quota')
            await process.wait()
            return bytes(data)
        try:
            raw = await asyncio.wait_for(collect(), 3)
            if process.returncode:
                return {'status': 'unavailable', 'items': []}
            records = iter(raw.decode('utf-8', errors='strict').split('\0'))
            items = []
            for record in records:
                if len(record) < 4:
                    continue
                code, path = record[:2], record[3:]
                source = next(records, '') if 'R' in code or 'C' in code else None
                try:
                    self.paths(workspace_id).authorize(path)
                    if source:
                        self.paths(workspace_id).authorize(source)
                    items.append({'relative_path': path, 'status': code, 'previous_path': source})
                except ContractError:
                    continue
            return {'status': 'available', 'items': items[:MAX_FILES]}
        except (TimeoutError, UnicodeError, ValueError):
            return {'status': 'unavailable', 'items': []}
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def capture(self, turn_id, workspace_id):
        if self.store.connection.execute('SELECT 1 FROM turn_baselines WHERE turn_id=?', (turn_id,)).fetchone():
            raise ContractError('Turn baseline already exists; never replay a turn', kind='INDETERMINATE', code=-32010)
        revision, files, contents, complete = await self.refresh(workspace_id)
        dirty = await self.original_dirty(workspace_id)
        artifact = self.store.publish_artifact(encoded(contents).encode(), origin='turn.baseline', classification='source', max_bytes=24000000)
        with self.store.transaction():
            self.store.connection.execute('INSERT INTO turn_baselines VALUES(?,?,?,?,?)',
                (turn_id, revision, encoded({'files': files, 'artifact_id': artifact['id']}), encoded(dirty), int(complete)))

    def baseline(self, turn_id):
        row = self.store.connection.execute('SELECT b.*,s.workspace_id FROM turn_baselines b JOIN turns t ON t.id=b.turn_id '
            'JOIN sessions s ON s.id=t.session_id WHERE b.turn_id=?', (turn_id,)).fetchone()
        if not row:
            raise ContractError('Task baseline has not been captured', kind='NOT_FOUND', code=-32010)
        return row, json.loads(row['manifest_json'])

    async def diff(self, params):
        baseline, manifest = self.baseline(params['turn_id'])
        revision, current, _, complete = await self.refresh(baseline['workspace_id'])
        old = manifest['files']
        removed = {p: v for p, v in old.items() if p not in current and v['kind'] == 'file'}
        new = {p: v for p, v in current.items() if p not in old and v['kind'] == 'file'}
        renames = {}
        for path, value in new.items():
            matches = [p for p, v in removed.items() if value['sha256'] is not None and v['sha256'] == value['sha256']]
            if len(matches) == 1 and sum(v['sha256'] == value['sha256'] for v in new.values()) == 1:
                renames[path] = matches[0]
        items = []
        for path in sorted(old.keys() | current.keys()):
            if old.get(path) == current.get(path) or path in renames.values():
                continue
            value = current.get(path) or old[path]
            if value['kind'] == 'directory':
                continue
            change = 'renamed' if path in renames else 'created' if path not in old else 'deleted' if path not in current else 'modified'
            items.append({'relative_path': path, 'previous_path': renames.get(path), 'change': change,
                'classification': value['classification'], 'before_sha256': old.get(renames.get(path, path), {}).get('sha256'),
                'current_sha256': current.get(path, {}).get('sha256'), 'size_bytes': value['size_bytes']})
        original = json.loads(baseline['original_dirty_json'])
        page = self.page(items, params, {'collection': 'workspace.diff', 'turn': params['turn_id'], 'revision': revision},
            gap=not complete or not baseline['complete'])
        return {**page, 'revision': revision, 'baseline_revision': baseline['revision'],
            'original_dirty_status': original['status'], 'original_dirty': original['items'][:100], 'original_dirty_has_more': len(original['items']) > 100}

    async def diff_file(self, params):
        baseline, manifest = self.baseline(params['turn_id'])
        revision, files, contents, complete = await self.refresh(baseline['workspace_id'])
        if revision != params['expected_revision']:
            raise ContractError('File changed since Diff was loaded; refresh the preview', kind='STALE_REVISION', code=-32010)
        self.paths(baseline['workspace_id']).authorize(params['relative_path'])
        path = params['relative_path']
        old_files = manifest['files']
        old_content = json.loads(self.store.read_artifact(manifest['artifact_id']))
        current_item = files.get(path)
        candidates = [name for name, value in old_files.items() if name not in files and value['sha256'] is not None
            and current_item and value['sha256'] == current_item['sha256']] if path not in old_files else []
        previous_path = candidates[0] if len(candidates) == 1 else path
        before_item = old_files.get(previous_path)
        if before_item is None and current_item is None:
            raise ContractError('Diff file not found', kind='NOT_FOUND', code=-32010)
        if any(v and v['classification'] != 'text' for v in (before_item, current_item)) or not complete or not baseline['complete']:
            return {'revision': revision, 'relative_path': path, 'before': None, 'current': None, 'reverse_patch': None,
                'current_sha256': current_item['sha256'] if current_item else None, 'preview_only': True, 'reason': 'binary_large_or_incomplete_baseline'}
        before = base64.b64decode(old_content.get(previous_path, '')).decode('utf-8')
        current = base64.b64decode(contents.get(path, '')).decode('utf-8')
        def reverse(old_text, new_text, source, target):
            lines = difflib.unified_diff(old_text.splitlines(keepends=True), new_text.splitlines(keepends=True), fromfile=source, tofile=target)
            return ''.join(line if line.endswith('\n') else line+'\n\\ No newline at end of file\n' for line in lines)
        patch = (reverse(current, '', 'a/'+path, '/dev/null') + reverse('', before, '/dev/null', 'b/'+previous_path)) if previous_path != path else (
            reverse(current, before, 'a/'+path if current_item else '/dev/null', 'b/'+path if before_item else '/dev/null'))
        result = {'revision': revision, 'relative_path': path, 'before': before, 'current': current, 'reverse_patch': patch,
            'current_sha256': current_item['sha256'] if current_item else None, 'preview_only': True, 'reason': 'manual_review_required'}
        if len(encoded(result).encode()) > 750000:
            return {**result, 'before': None, 'current': None, 'reverse_patch': None, 'reason': 'preview_frame_quota'}
        return result
