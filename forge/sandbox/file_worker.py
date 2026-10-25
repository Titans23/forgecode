"""One request, one restricted file helper. No credentials, server, hooks or models.

The launcher supplies the frozen policy over task stdin after OS restriction.
Path checks complement that restriction; portable invocation alone is not SRT.
"""
import asyncio
import base64
import configparser
from contextlib import redirect_stdout
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys
from time import monotonic

from forge.protocol import ContractError, MAX_FRAME_BYTES, canonical_hash, strict_loads, validate
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
        self.windows_acl = os.name == 'nt' and fs['read_mode'] == 'host_default'
        # File tools have an explicit read scope, even when the OS backend has
        # broader default reads. This does not promote its read capability.
        self.policy = PathPolicy(root, 'strict_allowlist_required',
            tuple(Path(os.path.abspath(p)) for p in fs['read_roots']),
            tuple(Path(os.path.abspath(p)) for p in fs['write_roots']),
            tuple(Path(os.path.abspath(p)) for p in fs['protected_paths']))
        self.root = root.path
        self.limits = policy['limits']
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

    async def scan(self):
        observed = await self._git_snapshot()
        return observed if observed is not None else self._scan_filesystem()

    async def _git_snapshot(self):
        # Reuse Git dirty-path observation; never invoke repository hooks,
        # filters/includes, worktree redirects or an executable from the task.
        deadline = monotonic() + 10
        metadata = self.root / '.git'
        if not metadata.is_dir() or (self.root / '.gitmodules').exists() or (metadata / 'config.worktree').exists():
            return None
        from forge.sandbox.path_policy import _read_marker
        try:
            parser = configparser.RawConfigParser(allow_no_value=True)
            parser.read_string(_read_marker(metadata / 'config'))
            core_keys = {'repositoryformatversion', 'filemode', 'bare', 'logallrefupdates',
                         'symlinks', 'ignorecase', 'autocrlf', 'eol', 'safecrlf', 'precomposeunicode', 'quotepath'}
            if parser.defaults() or any(section != 'core' and section != 'user'
                    and not section.startswith(('remote "', 'branch "')) for section in parser.sections()):
                return None
            if 'core' in parser and set(parser['core']) - core_keys:
                return None
            from forge.release.toolchains import core_tool
            git = await asyncio.to_thread(core_tool, 'git')
            if git.is_relative_to(self.root):
                return None
        except (OSError, ValueError, configparser.Error):
            return None
        from forge.tools.base import file_access_guard
        from forge.tools.shell import run_process
        from forge.runtime.workspace import parse_porcelain_paths, is_runtime_state_path
        inherited = {key: value for key, value in os.environ.items() if key.upper().startswith('GIT_')}
        for key in inherited:
            os.environ.pop(key)
        os.environ.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
            GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_CEILING_DIRECTORIES=str(self.root.parent))
        token = file_access_guard.set(self)
        try:
            result = await run_process([str(git), '--no-optional-locks', '-c', 'core.fsmonitor=false',
                '-c', 'core.untrackedCache=false', '-c', 'core.hooksPath=' + os.devnull,
                '-c', 'status.relativePaths=true', 'status', '--short', '-z',
                '--untracked-files=all', '--ignored=no', '--', '.'], cwd=self.root,
                timeout_seconds=max(0.001, deadline - monotonic()), max_output_bytes=65536)
            # .gitignore is not an authorization boundary. Include ordinary
            # ignored files so arbitrary commands cannot hide their edits.
            others = await run_process([str(git), '--no-optional-locks', '-c', 'core.fsmonitor=false',
                '-c', 'core.hooksPath=' + os.devnull, 'ls-files', '--others', '-z', '--', '.'],
                cwd=self.root, timeout_seconds=max(0.001, deadline - monotonic()), max_output_bytes=65536)
        except (OSError, ValueError):
            return None
        finally:
            file_access_guard.reset(token)
            for key in tuple(os.environ):
                if key.upper().startswith('GIT_'):
                    os.environ.pop(key)
            os.environ.update(inherited)
        for output in (result, others):
            if (output.exit_code != 0 or output.timed_out or output.output_incomplete
                    or output.stdout_truncated or output.stderr_truncated
                    or '\ufffd' in output.stdout or (output.stdout and not output.stdout.endswith('\0'))):
                return None
        paths = tuple(dict.fromkeys((*parse_porcelain_paths(result.stdout), *others.stdout.split('\0')[:-1])))
        files = {}
        for path in paths:
            if is_runtime_state_path(path):
                continue
            if len(files) >= 2000 or monotonic() > deadline:
                return {'files': files, 'complete': False, 'reason': 'scan_limit'}
            try:
                files[path] = self.observation(path)
            except (OSError, ValueError):
                # The bounded filesystem path retains the existing handling
                # of protected paths, unsafe links and incomplete observations.
                return None
        return {'files': files, 'complete': True}

    def _scan_filesystem(self):
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
        return await access.scan(), {}
    if operation == 'snapshot':
        return access.snapshot(request['paths'], request.get('recursive', False)), {}
    from forge.tools.base import file_access_guard
    from forge.tools.filesystem import (CreateDirectoryTool, ListDirectoryTool, ReadFileTool,
        RemoveDirectoryTool, ReplaceTextTool, WriteFileChunkTool, WriteFileTool)
    from forge.tools.search import FindFilesTool, GrepTool
    from forge.tools.git import GitStatusTool, GitDiffTool, GitLogTool
    from forge.tools.image import ViewImageTool
    tools = {tool.name: tool(access.root) for tool in (CreateDirectoryTool, ListDirectoryTool,
        ReadFileTool, RemoveDirectoryTool, ReplaceTextTool, WriteFileChunkTool, WriteFileTool, FindFilesTool, GrepTool,
        GitStatusTool, GitDiffTool, GitLogTool, ViewImageTool)}
    name, arguments = request['name'], request['arguments']
    if name in ('run_command', 'verify'):
        from forge.tools.shell import RunCommandTool
        if name == 'run_command':
            tools[name] = RunCommandTool(access.root, allow_container_writes=bool(access.policy.write_roots))
        else:
            from forge.tools.verify import VerifyTool
            from forge.runtime.workspace import WorkspaceTracker
            tools[name] = VerifyTool(access.root, WorkspaceTracker(access.root))
    if name != 'apply_patch' and name not in tools:
        fail('POLICY_DENIED', 'Only fixed file and process tools are available in this helper')
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
    if result.metadata.get('path') is not None:
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
