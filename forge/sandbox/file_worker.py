"""One request, one restricted file helper. No credentials, server, hooks or models.

The launcher supplies the frozen policy over task stdin after OS restriction.
Path checks complement that restriction; portable invocation alone is not SRT.
"""
import asyncio
import base64
from contextlib import redirect_stdout
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys
from time import monotonic

from forge.application.models import ContractError, MAX_FRAME_BYTES, canonical_hash, strict_loads, validate
from forge.sandbox.path_policy import PathPolicy, SENSITIVE, inspect_path


def fail(kind, message):
    raise ContractError(message, kind=kind, code=-32010)


class FileAccess:
    def __init__(self, request):
        validate('file-worker.request', request)
        policy, workspace = request['policy'], request['workspace']
        if canonical_hash(policy) != request['policy_hash'] or workspace['id'] != policy['workspace_id']:
            fail('POLICY_DENIED', 'Frozen workspace or policy binding differs')
        root = inspect_path(workspace['canonical_path'], absolute=True)
        info = root.path.stat()
        if not root.path.is_dir() or f'{info.st_dev}:{info.st_ino}' != workspace['file_identity']:
            fail('STALE_FILE', 'Workspace identity changed')
        fs = policy['filesystem']
        # File tools have an explicit read scope, even when the OS backend has
        # broader default reads. This does not promote its read capability.
        self.policy = PathPolicy(root, 'strict_allowlist_required',
            tuple(Path(os.path.abspath(p)) for p in fs['read_roots']),
            tuple(Path(os.path.abspath(p)) for p in fs['write_roots']),
            tuple(Path(os.path.abspath(p)) for p in fs['protected_paths']))
        self.root = root.path
        self.write = False
        self.before = {}
        self.expected = {}
        for raw, digest in request.get('expected_hashes', {}).items():
            path = self.policy.authorize(raw, write=True).path
            self.expected[path] = digest

    def observation(self, raw, *, content=False):
        authorized = self.policy.authorize(raw)
        path = authorized.path
        if not path.exists():
            authorized.assert_current()
            return {'exists': False, 'is_file': False, 'sha256': None, 'file_identity': None}
        info = path.lstat()
        value = {'exists': True, 'is_file': stat.S_ISREG(info.st_mode), 'sha256': None,
                 'file_identity': f'{info.st_dev}:{info.st_ino}'}
        if not value['is_file']:
            authorized.assert_current()
            return value
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or opened.st_nlink != 1:
                fail('STALE_FILE', 'File identity changed before read')
            authorized.assert_current()
            digest, chunks, length = sha256(), [], 0
            while chunk := stream.read(65536):
                digest.update(chunk)
                length += len(chunk)
                if content:
                    if length > 524288:
                        fail('ARTIFACT_LIMIT', 'Checkpoint file exceeds helper transport quota')
                    chunks.append(chunk)
            finished = os.fstat(stream.fileno())
            if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (finished.st_size, finished.st_mtime_ns, finished.st_ctime_ns):
                fail('STALE_FILE', 'File changed during observation')
            authorized.assert_current()
        value.update(sha256=digest.hexdigest(), size_bytes=length)
        if content:
            value['content_base64'] = base64.b64encode(b''.join(chunks)).decode('ascii')
        return value

    def __call__(self, raw):
        return self.guard(raw)

    def read_bytes(self, raw, limit=16777216):
        from forge.tools.base import ToolExecutionError
        try:
            authorized = self.policy.authorize(raw)
            info = authorized.path.lstat()
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
            with os.fdopen(os.open(authorized.path, flags), 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    fail('STALE_FILE', 'Opened file differs from its authorized identity')
                authorized.assert_current()
                data = stream.read(limit)
                finished = os.fstat(stream.fileno())
                if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (finished.st_size, finished.st_mtime_ns, finished.st_ctime_ns):
                    fail('STALE_FILE', 'File changed while reading')
                authorized.assert_current()
                return data
        except ContractError as error:
            raise ToolExecutionError(error.kind, str(error)) from error

    def read_text(self, raw):
        from forge.tools.base import ToolExecutionError
        limit = 16777216
        data = self.read_bytes(raw, limit + 1)
        if len(data) > limit:
            raise ToolExecutionError('ARTIFACT_LIMIT', 'File read exceeds helper byte quota')
        return data.decode('utf-8')

    def guard(self, raw):
        from forge.tools.base import ToolExecutionError
        try:
            authorized = self.policy.authorize(raw, write=self.write)
            path = authorized.path
            if self.write:
                current = self.observation(path)
                if path in self.expected and current['sha256'] != self.expected[path]:
                    fail('STALE_FILE', 'File changed after the approved observation')
                previous = self.before.setdefault(path, current)
                if previous != current:
                    fail('STALE_FILE', 'File changed while preparing the edit')
            authorized.assert_current()
            return path
        except ContractError as error:
            raise ToolExecutionError(error.kind, str(error)) from error

    def scan(self):
        files, pending, visited, complete = {}, [self.root], 0, True
        deadline = monotonic() + 10
        while pending:
            directory = pending.pop()
            try:
                self.policy.authorize(directory).assert_current()
                with os.scandir(directory) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > 20000 or monotonic() > deadline:
                            return {'files': files, 'complete': False, 'reason': 'scan_limit'}
                        candidate = Path(entry.path)
                        try:
                            authorized = self.policy.authorize(candidate)
                        except ContractError as error:
                            # Protected scope is intentionally excluded. Unsafe
                            # links are excluded as well and cannot yield content.
                            if error.kind == 'POLICY_DENIED':
                                if not (any(part.casefold() in SENSITIVE or part.casefold().startswith('.env.') for part in candidate.parts)
                                    or any(candidate == path or candidate.is_relative_to(path) for path in self.policy.protected_paths)):
                                    complete = False
                                continue
                            raise
                        authorized.assert_current()
                        if entry.is_dir(follow_symlinks=False):
                            if len(candidate.relative_to(self.root).parts) <= 32:
                                pending.append(candidate)
                            else:
                                complete = False
                        elif entry.is_file(follow_symlinks=False):
                            files[candidate.relative_to(self.root).as_posix()] = self.observation(candidate)
                            if len(files) >= 2000:
                                return {'files': files, 'complete': False, 'reason': 'transport_limit'}
            except OSError:
                complete = False
        return {'files': files, 'complete': complete}

    def snapshot(self, paths, recursive=False):
        files, pending = {}, list(paths)
        while pending:
            raw = pending.pop()
            path = self.policy.authorize(raw).path
            key = path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else path.as_posix()
            if key in files:
                continue
            files[key] = self.observation(path, content=True)
            if len(files) > 1000:
                fail('ARTIFACT_LIMIT', 'Recursive checkpoint exceeds helper path quota')
            if recursive and files[key]['exists'] and not files[key]['is_file']:
                with os.scandir(path) as entries:
                    pending.extend(entry.path for entry in entries)
        return {'files': files, 'complete': True}


def apply_patch(access, arguments):
    from forge.tools.base import ToolResult
    from forge.tools.filesystem import atomic_write_text
    from forge.tools.patch import (ApplyPatchInput, is_codex_envelope, parse_codex_envelope,
        build_unified_patch, parse_unified_patch_changes, validate_unified_patch_paths, _EnvelopeError)
    patch = ApplyPatchInput.model_validate(arguments).patch
    kind = 'codex_envelope' if is_codex_envelope(patch) else 'unified_diff'
    if kind == 'codex_envelope':
        # Check expected hashes before reading/materializing patch context.
        for operation in parse_codex_envelope(patch):
            access.guard(operation.path)
        try:
            patch = build_unified_patch(access.root, parse_codex_envelope(patch))
        except _EnvelopeError as error:
            if error.code != 'patch_already_applied':
                raise
            return ToolResult.ok(str(error), metadata={'format': kind, 'backend': 'file-worker',
                'status': 'already_completed', 'resolution_checkpoint': True,
                'target_paths': [str(error.details['path'])]})
    paths = validate_unified_patch_paths(access.root, patch)
    for path in paths:
        access.guard(path)
    changes = parse_unified_patch_changes(access.root, patch)
    # Materialize and authorize every change before the first write. Individual
    # replacements are atomic; a multi-file operation is not a filesystem transaction.
    for path, before, _ in changes:
        observed = access.observation(path)
        if observed['sha256'] != (sha256(before.encode('utf-8')).hexdigest() if before is not None else None):
            fail('STALE_FILE', 'Patch context changed before application')
    applied = []
    try:
        for raw, _, after in changes:
            target = access.guard(raw)
            if after is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(target, after)
            applied.append(raw)
    except Exception:
        if applied:
            fail('INDETERMINATE', 'Multi-file patch stopped after a partial change; inspect before continuing')
        raise
    return ToolResult.ok(f'Applied patch to {len(paths)} target path(s).', metadata={
        'format': kind, 'backend': 'file-worker', 'target_paths': list(paths), 'changed_files': list(paths)})


async def execute(request):
    access = FileAccess(request)
    operation = request['operation']
    if operation == 'bootstrap':
        return {'loaded_modules': sorted(sys.modules)}, {}
    if operation == 'scan':
        return access.scan(), {}
    if operation == 'snapshot':
        return access.snapshot(request['paths'], request.get('recursive', False)), {}
    from forge.tools.base import file_access_guard
    from forge.tools.filesystem import (CreateDirectoryTool, ListDirectoryTool, ReadFileTool,
        RemoveDirectoryTool, ReplaceTextTool, WriteFileChunkTool, WriteFileTool)
    from forge.tools.search import FindFilesTool, GrepTool
    tools = {tool.name: tool(access.root) for tool in (CreateDirectoryTool, ListDirectoryTool,
        ReadFileTool, RemoveDirectoryTool, ReplaceTextTool, WriteFileChunkTool, WriteFileTool, FindFilesTool, GrepTool)}
    name, arguments = request['name'], request['arguments']
    if name != 'apply_patch' and name not in tools:
        fail('POLICY_DENIED', 'Only fixed file tools are available in this helper')
    access.write = name == 'apply_patch' or tools[name].effect == 'workspace_write'
    token = file_access_guard.set(access)
    try:
        if name == 'apply_patch':
            from forge.tools.base import ToolResult, ToolExecutionError
            from forge.tools.patch import _EnvelopeError
            try:
                result = apply_patch(access, arguments)
            except (ToolExecutionError, _EnvelopeError) as error:
                result = ToolResult.fail(error.code, str(error), details=error.details)
        else:
            result = await tools[name].run(arguments)
    finally:
        file_access_guard.reset(token)
    if not result.success and result.error.code in ('POLICY_DENIED', 'STALE_FILE'):
        fail(result.error.code, result.error.message)
    paths = result.metadata.get('target_paths', [])
    if 'path' in result.metadata:
        paths = [*paths, result.metadata['path']]
    observations = {str(path): access.observation(path) for path in dict.fromkeys(paths)}
    return asdict(result), observations


def main(argv=None):
    output = sys.stdout.buffer
    request = {}
    try:
        if argv:
            fail('INVALID_PARAMS', 'file-worker accepts no command line payload')
        request = strict_loads(sys.stdin.buffer.read(MAX_FRAME_BYTES + 1))
        if not isinstance(request, dict):
            request = {}
            fail('INVALID_PARAMS', 'File helper request must be an object')
        with redirect_stdout(sys.stderr):
            result, observations = asyncio.run(execute(request))
        response = {'schema_version': 'forge.file-worker.response.v1', 'request_id': request['request_id'],
            'policy_hash': request['policy_hash'], 'status': 'ok', 'result': result, 'observations': observations}
        code = 0
    except Exception as error:
        response = {'schema_version': 'forge.file-worker.response.v1', 'request_id': request.get('request_id'),
            'policy_hash': request.get('policy_hash'), 'status': 'error', 'error': {
                'code': getattr(error, 'kind', getattr(error, 'code', 'INDETERMINATE')),
                'message': str(error) if isinstance(error, ContractError) else 'File helper operation failed',
                'exception_type': type(error).__name__}}
        code = 2
    encoded = json.dumps(response, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(encoded) > MAX_FRAME_BYTES:
        response = {'schema_version': 'forge.file-worker.response.v1', 'request_id': request.get('request_id'),
            'policy_hash': request.get('policy_hash'), 'status': 'error',
            'error': {'code': 'ARTIFACT_LIMIT', 'message': 'File helper response exceeds transport quota'}}
        encoded, code = json.dumps(response).encode(), 2
    output.write(encoded + b'\n')
    output.flush()
    return code
