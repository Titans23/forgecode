'''Read-only execution facts; quotas describe the environment, never enlarge it.'''
import os
from pathlib import Path
import sys


def _text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def _positive(value):
    try:
        number = int(value)
        return number if 0 < number < 2**60 else None
    except ValueError:
        return None


def cgroup_limits(root=Path('/sys/fs/cgroup'), membership=Path('/proc/self/cgroup')):
    """Include ancestor limits; a container's host CPU count is not its quota."""
    cpu, memory = [], []
    for line in _text(membership).splitlines():
        fields = line.split(':', 2)
        if len(fields) != 3:
            continue
        _, controllers, relative = fields
        if not controllers:
            locations = [(root, 'v2')]
        else:
            locations = [(root/controllers if (root/controllers).is_dir() else root/name, name) for name in controllers.split(',')
                         if name in {'cpu', 'memory'}]
        for mount, kind in locations:
            path = mount / relative.lstrip('/')
            # A cgroup namespace may expose a host-relative path. The mounted
            # root is still observable; never traverse outside that mount.
            if '..' in path.parts:
                path = mount
            while path.is_relative_to(mount):
                if kind == 'v2':
                    values = _text(path/'cpu.max').split()
                    quota, period = ([_positive(v) for v in values] if len(values) == 2 else (None, None))
                    limit = _positive(_text(path/'memory.max'))
                else:
                    quota = _positive(_text(path/'cpu.cfs_quota_us')) if kind == 'cpu' else None
                    period = _positive(_text(path/'cpu.cfs_period_us')) if kind == 'cpu' else None
                    limit = _positive(_text(path/'memory.limit_in_bytes')) if kind == 'memory' else None
                if quota and period:
                    cpu.append(quota / period)
                if limit:
                    memory.append(limit)
                if path == mount:
                    break
                path = path.parent
    return {'cpu_quota': min(cpu) if cpu else None, 'memory_limit_bytes': min(memory) if memory else None}


def resource_facts():
    try:
        available = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        available = os.cpu_count()
    facts = {'cpu_visible_count': available, 'cpu_quota': None, 'memory_limit_bytes': None}
    if sys.platform.startswith('linux'):
        facts.update(cgroup_limits())
    quotas = [v for v in (available, facts.get('cpu_quota')) if v is not None]
    facts['effective_cpu_capacity'] = min(quotas) if quotas else None
    facts['note'] = 'CPU quota is capacity, not nproc. Size parallel work to the effective capacity; unknown limits are null.'
    return facts
