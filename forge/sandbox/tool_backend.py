"""Harness file boundary. Native session creation/supervision is owned separately."""
from dataclasses import replace

from forge.application.models import ContractError, canonical_hash, validate
from forge.tools.base import ToolError, ToolResult


FILE_TOOLS = frozenset({'list_directory', 'find_files', 'read_file', 'grep', 'create_directory',
    'remove_directory', 'write_file', 'write_file_chunk', 'replace_text', 'apply_patch'})
PROCESS_TOOLS = frozenset({'run_command', 'verify'})


class FileToolBackend:
    def __init__(self, client):
        self.observer = client
        self.mode = 'strict' if client.native is not None else 'local-trusted'
        self._observed = {}

    def check_ready(self, policy, *, required_mode):
        if self.mode != required_mode or canonical_hash(policy) != self.observer.policy_hash:
            raise ContractError('Tool backend does not match the frozen execution policy', kind='SANDBOX_UNAVAILABLE', code=-32010)
        if self.mode == 'strict':
            prepared = self.observer.native._prepared
            if prepared is None or prepared.sha256 != self.observer.policy_hash:
                raise ContractError('Restricted helper requires a prepared native session', kind='SANDBOX_UNAVAILABLE', code=-32010)
        else:
            from forge.application.harness_adapter import LocalTrustedBackend
            LocalTrustedBackend().check_ready(policy, required_mode=required_mode)

    async def snapshot(self, paths, *, recursive=False):
        snapshot = await self.observer.snapshot(paths, recursive=recursive)
        if not snapshot['complete']:
            raise ContractError('Authorized checkpoint observation is incomplete', kind='INDETERMINATE', code=-32010)
        for path, value in snapshot['files'].items():
            self._observed.setdefault(path, value['sha256'])
        return snapshot

    async def execute(self, call, registry):
        if call.name in FILE_TOOLS | PROCESS_TOOLS:
            validation = registry.validate(call.name, call.arguments)
            if validation is not None:
                return validation
            try:
                from forge.runtime.agent_loop import mutation_target_paths
                import os
                from pathlib import Path
                targets = {Path(os.path.abspath(Path(self.observer.workspace['canonical_path']) / path))
                    for path in mutation_target_paths(call, maximum=None)}
                expected = {path: digest for path, digest in self._observed.items()
                    if Path(os.path.abspath(Path(self.observer.workspace['canonical_path']) / path)) in targets}
                reply = await self.observer.request('tool', name=call.name, arguments=call.arguments,
                    expected_hashes=expected)
                raw = reply['result']
                validate('file-worker.tool-result', raw)
                error = ToolError(**raw['error']) if raw['error'] is not None else None
                result = ToolResult(**{**raw, 'error': error})
                if call.name == 'verify':
                    tracker = registry.implementation(call.name).tracker
                    result = replace(result, metadata={**result.metadata,
                        'workspace_revision': tracker.revision, 'environment_epoch': tracker.environment_epoch})
                for path, value in reply['observations'].items():
                    self._observed[path] = value['sha256']
                return replace(result, metadata={**result.metadata, 'execution_boundary':
                    'srt-file-worker' if self.mode == 'strict' else 'local-trusted-file-worker'})
            except ContractError as error:
                return ToolResult.fail(error.kind, str(error), metadata={'execution_boundary': self.mode + '-file-worker'})
        # These tools mutate trusted task state or dispatch Explore with this same
        # backend. Hooks/MCP/skill extensions cannot inherit a sandbox label.
        safe_control = {'finish_task', 'review_delivery', 'explore_repository',
            'task_get', 'task_plan', 'task_update', 'task_revise_requirement', 'read_context_artifact'}
        if call.name in safe_control and registry.provenance(call.name).get('source') == 'builtin':
            return await registry.execute(call.name, call.arguments)
        if self.mode == 'local-trusted':
            result = await registry.execute(call.name, call.arguments)
            return replace(result, metadata={**result.metadata, 'execution_boundary': 'local-trusted-host',
                'sandbox_covered': False})
        return ToolResult.fail('CAPABILITY_UNSATISFIED', 'This tool has no restricted implementation for the prepared session.',
            metadata={'sandbox_covered': False, 'extension_provenance': registry.provenance(call.name)})

    async def close(self):
        return await self.observer.close()
