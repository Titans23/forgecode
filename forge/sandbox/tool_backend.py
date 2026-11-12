"""Harness file boundary. Native session creation/supervision is owned separately."""
from dataclasses import replace

from forge.protocol import ContractError, canonical_hash, validate
from forge.tools.base import ToolError, ToolResult


FILE_TOOLS = frozenset({'list_directory', 'find_files', 'read_file', 'view_image', 'grep', 'create_directory',
    'remove_directory', 'write_file', 'write_file_chunk', 'replace_text', 'apply_patch', 'git_status', 'git_diff', 'git_log'})
PROCESS_TOOLS = frozenset({'run_command', 'verify'})


def sandbox_unavailable_result(message, boundary):
    """A confirmed pre-execution failure needs a different authority, not a different command."""
    next_step = ('The command did not start. This is a sandbox setup failure, not a command syntax failure. '
        'Do not repeat native commands with unchanged permissions. If approvals are available, retry this exact '
        'operation with sandbox_permissions="danger-full-access" and a non-empty justification; this requests '
        'one-call host approval and does not change the session default. File tools remain available. '
        'If approval is unavailable or denied, report the blocker instead of retrying. '
        'The user can explicitly select /permission danger-full-access for the session.')
    return ToolResult.fail('SANDBOX_UNAVAILABLE', message,
        summary='原生沙盒未就绪，命令未启动；可申请单次 full-access 授权。',
        content=message + '\n\n' + next_step,
        details={'command_started': False, 'retry_with_unchanged_permissions': False, 'next_step': next_step},
        metadata={'execution_boundary': boundary, 'sandbox_covered': False})


class FileToolBackend:
    def __init__(self, client):
        self.observer = client
        self.mode = client.native.mode if client.native is not None else 'local-trusted'
        self._observed = {}
        from forge.file_mutations import FileMutationLocks
        self.file_mutations = FileMutationLocks()

    def check_ready(self, policy, *, required_mode):
        if self.mode != required_mode or canonical_hash(policy) != self.observer.policy_hash:
            raise ContractError('Tool backend does not match the frozen execution policy', kind='SANDBOX_UNAVAILABLE', code=-32010)
        if self.mode != 'local-trusted':
            if self.observer.native.policy_hash != self.observer.policy_hash:
                raise ContractError('Restricted helper requires a prepared native session', kind='SANDBOX_UNAVAILABLE', code=-32010)
        else:
            from forge.runtime.local_backend import LocalTrustedBackend
            LocalTrustedBackend().check_ready(policy, required_mode=required_mode)

    async def snapshot(self, paths, *, recursive=False):
        snapshot = await self.observer.snapshot(paths, recursive=recursive, include_content=True)
        if not snapshot['complete']:
            raise ContractError('Authorized checkpoint observation is incomplete', kind='INDETERMINATE', code=-32010)
        return snapshot

    async def execute(self, call, registry):
        if call.name in {'job_output', 'job_kill'}:
            from forge.tools.jobs import JobOutputTool, JobKillTool
            if isinstance(registry.implementation(call.name), (JobOutputTool, JobKillTool)):
                return await registry.execute(call.name, call.arguments)
        if call.name in FILE_TOOLS | PROCESS_TOOLS:
            validation = registry.validate(call.name, call.arguments)
            if validation is not None:
                return validation
            try:
                from forge.tools.paths import mutation_target_paths
                import os
                from pathlib import Path
                def target_path(path):
                    candidate = Path(self.observer.workspace['canonical_path']) / path
                    return (candidate.resolve(strict=False) if self.observer.policy.get('execution_mode')
                            else Path(os.path.abspath(candidate)))
                targets = ({target_path(path) for path in mutation_target_paths(call, maximum=None)}
                           if registry.effect(call.name) == 'workspace_write' else set())
                expected = {path: digest for path, digest in self._observed.items()
                    if target_path(path) in targets}
                for target in targets:
                    if not any(target_path(path) == target for path in expected):
                        expected[str(target)] = None  # Unobserved targets may only be created, not overwritten.
                from forge.process_output import process_output
                output = process_output.get()
                reply = await self.observer.request('tool', name=call.name, arguments=call.arguments,
                    expected_hashes=expected, **({'on_output': output} if output is not None else {}))
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
                    'srt-file-worker' if self.mode == 'strict' else self.mode + '-file-worker'})
            except ContractError as error:
                if error.kind == 'SANDBOX_UNAVAILABLE' and self.observer.native is not None:
                    return sandbox_unavailable_result(str(error), self.mode + '-file-worker')
                return ToolResult.fail(error.kind, str(error),
                    details={'command_started': False} if error.kind == 'SANDBOX_UNAVAILABLE' else None,
                    metadata={'execution_boundary': self.mode + '-file-worker'})
        # These tools mutate trusted task state or dispatch Explore with this same
        # backend. Hooks/MCP/skill extensions cannot inherit a sandbox label.
        safe_control = {'finish_task', 'review_delivery', 'load_skill', 'read_skill_resource',
            'task_get', 'task_plan', 'task_update', 'task_revise_requirement', 'read_context_artifact'}
        if call.name == 'explore_repository':
            from forge.subagents.explore import ExploreRepositoryTool
            implementation = registry.implementation(call.name)
            if type(implementation) is ExploreRepositoryTool and implementation.tool_backend is self:
                return await registry.execute(call.name, call.arguments)
        if call.name in safe_control and registry.provenance(call.name).get('source') == 'builtin':
            return await registry.execute(call.name, call.arguments)
        if registry.provenance(call.name).get('source') == 'trusted_plugin':
            from forge.plugins import PluginTool
            if type(registry.implementation(call.name)) is PluginTool and self.mode != 'strict':
                # Explicitly trusted host code is not covered by the native worker.
                return await registry.execute(call.name, call.arguments)
        if self.mode == 'local-trusted':
            result = await registry.execute(call.name, call.arguments)
            return replace(result, metadata={**result.metadata, 'execution_boundary': 'local-trusted-host',
                'sandbox_covered': False})
        return ToolResult.fail('CAPABILITY_UNSATISFIED', 'This tool has no restricted implementation for the prepared session.',
            metadata={'sandbox_covered': False, 'extension_provenance': registry.provenance(call.name)})

    async def close(self):
        return await self.observer.close()
