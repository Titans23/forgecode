"""Requirements are separate from measured backend capabilities and their evidence."""
import json
import sys
from datetime import datetime, timezone

from forge.application.models import ContractError, validate


FEATURES = ('read_isolation', 'write_isolation', 'direct_network_isolation', 'dns_isolation',
            'socket_isolation', 'process_cleanup', 'memory', 'disk', 'pids')


class CapabilityReport:
    def __init__(self, value):
        validate('capability-report', value)
        value = json.loads(json.dumps(value))
        value.setdefault('verification', {name: {'status': 'unsupported', 'evidence_refs': []} for name in FEATURES})
        for name in FEATURES:
            if value['verification'][name]['status'] == 'verified' and not self._supported(value, name):
                raise ContractError('Verified capability conflicts with measured support')
        self._json = json.dumps(value, ensure_ascii=False, sort_keys=True)

    @property
    def value(self):
        return json.loads(self._json)

    @staticmethod
    def _supported(value, name):
        if name in ('memory', 'disk', 'pids'):
            return value['resource_enforcement'][name] != 'unavailable'
        if name == 'read_isolation':
            return value[name] != 'unavailable'
        return value[name]

    def require(self, policy):
        value = self.value
        if value['readiness'] != 'ready':
            raise ContractError('Sandbox capability report is not ready', kind='SANDBOX_UNAVAILABLE', code=-32010)
        required = ['read_isolation', 'write_isolation', 'direct_network_isolation', 'socket_isolation', 'process_cleanup']
        if policy['network']['dns_isolation_required']:
            required.append('dns_isolation')
        for key, name in (('memory_bytes', 'memory'), ('disk_bytes', 'disk'), ('pids', 'pids')):
            limit = policy['limits'][key]
            if limit is not None and limit['enforcement'] == 'hard_required':
                required.append(name)
                if value['resource_enforcement'][name] != 'hard':
                    raise ContractError(f'Hard {name} requirement is unavailable', kind='CAPABILITY_UNSATISFIED', code=-32010)
        if policy['filesystem']['read_mode'] == 'strict_allowlist_required' and value['read_isolation'] != 'strict_allowlist':
            raise ContractError('Requested strict read allowlist is unavailable', kind='CAPABILITY_UNSATISFIED', code=-32010)
        for name in required:
            proof = value['verification'][name]
            if not self._supported(value, name) or proof['status'] != 'verified' or not proof['evidence_refs']:
                raise ContractError(f'{name} is not verified for this backend', kind='CAPABILITY_UNSATISFIED', code=-32010)
        return {name: value['resource_enforcement'][name] for name in ('memory', 'disk', 'pids')}


def unavailable_report(*, platform=None, backend_version='unprobed', reason='Native backend has not been verified'):
    if platform is None:
        platform = 'windows-native' if sys.platform == 'win32' and sys.getwindowsversion().build >= 22000 else (
            'linux-native' if sys.platform.startswith('linux') else 'unsupported')
    return CapabilityReport({'platform': platform, 'backend': 'srt', 'backend_version': backend_version,
        'read_isolation': 'unavailable', 'write_isolation': False, 'direct_network_isolation': False,
        'dns_isolation': False, 'socket_isolation': False, 'process_cleanup': False,
        'resource_enforcement': {name: 'unavailable' for name in ('memory', 'disk', 'pids')},
        'readiness': 'unavailable', 'issues': [reason],
        'measured_at_utc': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'verification': {name: {'status': 'unsupported', 'evidence_refs': []} for name in FEATURES}})
