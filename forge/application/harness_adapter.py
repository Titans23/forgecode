"""Translate structured input into the original Harness, without another agent loop."""
from pathlib import Path
from contextlib import aclosing
import asyncio

from forge.application.models import ContractError
from forge.permissions.policy import PermissionManager
from forge.runtime.dependencies import RuntimeBindings
from forge.runtime.factory import create_runtime


class LocalTrustedBackend:
    """Explicit native tool execution. This backend makes no sandbox claim."""
    mode = 'local-trusted'

    def check_ready(self, policy, *, required_mode):
        if required_mode != self.mode:
            raise ContractError('strict sandbox backend is not ready; local-trusted cannot satisfy it',
                                kind='SANDBOX_UNAVAILABLE', code=-32010)
        if policy['network']['dns_isolation_required'] or policy['filesystem']['read_mode'] == 'strict_allowlist_required' or any(
                policy['limits'][name] and policy['limits'][name]['enforcement'] == 'hard_required'
                for name in ('memory_bytes', 'disk_bytes', 'pids')):
            raise ContractError('Local-trusted cannot satisfy requested isolation or hard resource requirements',
                                kind='CAPABILITY_UNSATISFIED', code=-32010)

    async def execute(self, call, registry):
        return await registry.execute(call.name, call.arguments)


class HarnessAdapter:
    def __init__(self, root: Path, *, config, data_root, backend, budget,
                 model_client_factory=None, recorder=None, approval_handler=None, task_relation=None,
                 resume_identifier=None, fork_session=False, turn_baseline_handler=None):
        permissions = PermissionManager(root, mode='supervised', approval_handler=approval_handler,
                                        load_stored_rules=False)
        bindings = RuntimeBindings(config=config, data_root=data_root, backend=backend, recorder=recorder,
            model_client_factory=model_client_factory, permission_manager=permissions, trusted_extensions=False,
            task_relation=task_relation, max_model_calls=budget['max_model_calls'],
            max_tool_calls=budget['max_tool_calls'], wall_seconds=budget['wall_seconds'],
            turn_baseline_handler=turn_baseline_handler)
        self.conversation, self.journal, _ = create_runtime(root, bindings=bindings,
            resume_identifier=resume_identifier, fork_session=fork_session,
            model_override=config.model_id if fork_session else None)

    async def stream(self, inputs):
        prompt = '\n'.join(part['text'] for part in inputs)
        if not prompt.strip():
            raise ContractError('Turn input must contain non-whitespace text')
        async with aclosing(self.conversation.stream(prompt)) as stream:
            async for event in stream:
                yield event
                # Some legacy tool adapters return an indeterminate result after
                # cancellation. Preserve it, then stop instead of replanning.
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError

    async def close(self):
        await self.conversation.runtime_close()
