"""Execute a command on a copied workspace; only validated output reaches the host."""
import asyncio
import base64
from dataclasses import replace
import json
from pathlib import Path

from forge.protocol import strict_loads
from forge.sandbox.docker_process import ContainerLimits
from forge.sandbox.workspace_transfer import WorkspaceSnapshot, rejected


async def run_workspace(runtime, policy, argv, *, stdin=b'', limits=None, transfer_limits=None):
    limits = limits or ContainerLimits()
    if not argv or any(not isinstance(value, str) or '\0' in value for value in argv):
        raise ValueError('Command must be a nonempty argument vector')
    snapshot = WorkspaceSnapshot(policy, limits=transfer_limits)
    request = {'tree': snapshot.encode(), 'argv': list(argv), 'stdin': base64.b64encode(stdin).decode('ascii'),
        'output_limit': limits.output_bytes, 'entries_limit': snapshot.limits.entries, 'bytes_limit': snapshot.limits.bytes}
    # File return transport has its own bound; command output retains the user's
    # smaller output limit. Nothing is silently clipped before writeback.
    transport_limit = 4 * ((snapshot.limits.bytes + 2) // 3) + snapshot.limits.entries * 5000 + 2 * limits.output_bytes + 65536
    source = Path(__file__).with_name('container_workspace.py').read_text(encoding='utf-8')
    result = await runtime.run(['python', '-I', '-B', '-c', source],
        stdin=json.dumps(request, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
        limits=replace(limits, output_bytes=transport_limit))
    if result['exit_code'] or result['oom_killed'] or result['discarded_bytes']:
        raise rejected('Sandbox did not return a complete workspace; no host changes applied', 'INDETERMINATE')
    value = strict_loads(result['stdout'], max_bytes=transport_limit)
    if not isinstance(value, dict) or set(value) != {'tree', 'exit_code', 'stdout', 'stderr', 'discarded_bytes'}:
        raise rejected('Invalid sandbox return envelope', 'INDETERMINATE')
    if type(value['exit_code']) is not int or type(value['discarded_bytes']) is not int or value['discarded_bytes'] < 0:
        raise rejected('Invalid sandbox exit or output counters', 'INDETERMINATE')
    output = {}
    for name in ('stdout', 'stderr'):
        encoded = value[name]
        if not isinstance(encoded, str) or len(encoded) > 4 * ((limits.output_bytes + 2) // 3):
            raise rejected('Sandbox command output exceeded quota', 'ARTIFACT_LIMIT')
        try:
            output[name] = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise rejected('Invalid sandbox command output encoding', 'INDETERMINATE') from error
    if sum(map(len, output.values())) > limits.output_bytes:
        raise rejected('Sandbox command output exceeded quota', 'ARTIFACT_LIMIT')
    if asyncio.current_task().cancelling():
        raise asyncio.CancelledError
    changed = snapshot.apply(value['tree'])
    return {**output, 'exit_code': value['exit_code'], 'discarded_bytes': value['discarded_bytes'],
        'changed_paths': changed, 'image': result['image']}
