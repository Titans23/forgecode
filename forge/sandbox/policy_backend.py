"""DSH-style file policy with on-demand native confinement for command tools."""
import asyncio
from dataclasses import replace

from forge.protocol import ContractError, canonical_hash
from forge.sandbox.tool_backend import FileToolBackend, FILE_TOOLS, PROCESS_TOOLS
from forge.sandbox.workspace_backend import require_workspace_policy
from forge.tools.base import ToolResult


# Git can execute repository-configured helpers; it belongs with command tools.
NATIVE_TOOLS = PROCESS_TOOLS | {'git_status', 'git_diff', 'git_log'}


class PolicyToolBackend(FileToolBackend):
    def __init__(self, client, process_factory):
        super().__init__(client)
        self.mode = client.policy['execution_mode']
        self._process_factory = process_factory
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
                return ToolResult.fail(getattr(error, 'kind', 'SANDBOX_UNAVAILABLE'), str(error),
                    metadata={'execution_boundary': 'native-initialization-failed', 'sandbox_covered': False})
            return await backend.execute(call, registry)
        result = await super().execute(call, registry)
        if call.name in FILE_TOOLS:
            result = replace(result, metadata={**result.metadata,
                'execution_boundary': self.mode + '-file-policy', 'sandbox_covered': False})
        return result

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        return await asyncio.shield(self._closing)

    async def _close(self):
        reports = [await self.observer.close()]
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
