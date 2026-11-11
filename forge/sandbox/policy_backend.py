"""DSH-style file policy with on-demand native confinement for command tools."""
import asyncio
from dataclasses import replace

from forge.protocol import ContractError, canonical_hash
from forge.sandbox.tool_backend import FileToolBackend, FILE_TOOLS, PROCESS_TOOLS, sandbox_unavailable_result
from forge.sandbox.workspace_backend import require_workspace_policy
from forge.tools.base import ToolResult


# Git can execute repository-configured helpers; it belongs with command tools.
NATIVE_TOOLS = PROCESS_TOOLS | {'git_status', 'git_diff', 'git_log'}
MUTATION_TOOLS = {'create_directory', 'remove_directory', 'write_file', 'write_file_chunk', 'replace_text', 'apply_patch'}


class PolicyToolBackend(FileToolBackend):
    def __init__(self, client, process_factory, *, mode_factory=None):
        super().__init__(client)
        self.mode = client.policy['execution_mode']
        self._process_factory = process_factory
        self._mode_factory = mode_factory
        self._temporary = set()
        self._temporary_reports = []
        self._opening = None
        self._closing = None

    def check_ready(self, policy, *, required_mode):
        # This admits the trusted file policy, not an untested OS capability.
        require_workspace_policy(policy)
        if (required_mode not in ('read-only', 'workspace-write') or self.mode != required_mode
                or canonical_hash(policy) != self.observer.policy_hash):
            raise ContractError('File policy differs from the requested execution mode',
                                kind='CAPABILITY_UNSATISFIED', code=-32010)

    async def _open_process(self):
        backend = await self._process_factory()
        # Closing during initialization must not leave a late native session.
        if self._closing is not None:
            await backend.close()
        return backend

    async def prepare_process(self):
        """Prepare once on first command; explicit evaluators may preflight it."""
        if self._closing is not None:
            raise ContractError('Execution session is closed', kind='SANDBOX_UNAVAILABLE')
        if self._opening is None:
            self._opening = asyncio.create_task(self._open_process())
        backend = await asyncio.shield(self._opening)
        if self._closing is not None:
            raise ContractError('Execution session closed during initialization', kind='SANDBOX_UNAVAILABLE')
        return backend

    async def execute(self, call, registry):
        if self._closing is not None:
            return ToolResult.fail('SANDBOX_UNAVAILABLE', 'Execution session is closed')
        if call.name in NATIVE_TOOLS:
            invalid = registry.validate(call.name, call.arguments)
            if invalid is not None:
                return invalid
            try:
                backend = await self.prepare_process()
            except Exception as error:
                if getattr(error, 'kind', 'SANDBOX_UNAVAILABLE') == 'SANDBOX_UNAVAILABLE':
                    return sandbox_unavailable_result(str(error), 'native-initialization-failed')
                return ToolResult.fail(getattr(error, 'kind', 'SANDBOX_UNAVAILABLE'), str(error),
                    details={'command_started': False,
                        'next_step': 'For this exact command, request sandbox_permissions="danger-full-access" '
                            'with justification if host execution is acceptable. Explicit one-call approval is required.'},
                    metadata={'execution_boundary': 'native-initialization-failed', 'sandbox_covered': False})
            return await backend.execute(call, registry)
        result = await super().execute(call, registry)
        if call.name in FILE_TOOLS:
            result = replace(result, metadata={**result.metadata,
                'execution_boundary': self.mode + '-file-policy', 'sandbox_covered': False})
        return result

    async def probe_process(self):
        """Evaluators explicitly request OS evidence before spending model budget."""
        backend = await self.prepare_process()
        return await backend.observer.native.probe()

    async def execute_approved(self, call, registry, mode):
        """Called only by ToolExecutor after an exact, one-call widening approval."""
        if self._closing is not None:
            return ToolResult.fail('SANDBOX_UNAVAILABLE', 'Execution session is closed')
        if (call.name not in NATIVE_TOOLS | MUTATION_TOOLS or self._mode_factory is None
                or mode not in {'read-only': ('workspace-write', 'full-access'),
                               'workspace-write': ('full-access',)}.get(self.mode, ())):
            return ToolResult.fail('CAPABILITY_UNSATISFIED', 'This backend cannot apply the approved command mode')
        invalid = registry.validate(call.name, call.arguments)
        if invalid is not None:
            return invalid
        # Constructing a temporary file-policy backend does not initialize native grants.
        backend = self.open_approved(mode)
        try:
            result = await backend.execute(call, registry)
        finally:
            report = await self.close_approved(backend)
        metadata = {**result.metadata, 'execution_mode': mode, 'sandbox_override': 'allow_once', 'cleanup': report}
        if mode == 'full-access':
            metadata['sandbox_covered'] = False
        if report.get('state') != 'clean':
            return ToolResult.fail('INDETERMINATE', 'Command cleanup was not proven clean; inspect before retrying.',
                content=result.content, metadata=metadata)
        return replace(result, metadata=metadata)

    def open_approved(self, mode):
        if self._closing is not None or self._mode_factory is None:
            raise ContractError('Execution provider is closed or cannot widen', kind='SANDBOX_UNAVAILABLE')
        if mode not in {'read-only': ('workspace-write', 'full-access'), 'workspace-write': ('full-access',)}.get(self.mode, ()):
            raise ContractError('Requested mode is not wider than the current mode', kind='POLICY_DENIED')
        backend = self._mode_factory(mode)
        if hasattr(backend, '_observed'):
            backend._observed.update(self._observed)
        self._temporary.add(backend)
        return backend

    async def close_approved(self, backend):
        try:
            report = await backend.close()
        except Exception as error:
            report = {'state': 'unknown', 'reason': type(error).__name__}
        self._temporary_reports.append(report)
        if hasattr(backend, '_observed'):
            self._observed.update(backend._observed)
        self._temporary.discard(backend)
        return report

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        return await asyncio.shield(self._closing)

    async def _close(self):
        reports = [await self.observer.close()]
        for backend in list(self._temporary):
            reports.append(await backend.close())
        reports.extend(self._temporary_reports)
        if self._opening is not None:
            try:
                backend = await asyncio.wait_for(asyncio.shield(self._opening), 35)
            except BaseException as error:
                reports.append(getattr(error, 'cleanup_report', {'state': 'unknown'}))
            else:
                reports.append(await backend.close())
        states = {report.get('state', 'unknown') for report in reports}
        return {'state': 'unknown' if 'unknown' in states else 'residual' if 'residual' in states else 'clean',
                'scope': 'file-policy-and-native-process', 'reports': reports}
