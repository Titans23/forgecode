"""Direct tool dispatch; explicit host execution with no isolation claim."""
from forge.protocol import ContractError


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
