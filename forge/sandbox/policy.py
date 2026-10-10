"""Compile trusted policy inputs; never treat upstream allowRead as a whitelist."""
from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import re

from forge.application.models import ContractError, canonical_hash, validate
from forge.sandbox.path_policy import PathPolicy, audit_workspace_links, denied, git_metadata_paths, inspect_path


DOMAIN = re.compile(r'^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$')
UNSAFE_ENV = re.compile(r'^(?:NODE_|PYTHON|LD_|DYLD_|ELECTRON_)|(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)', re.I)


@dataclass(frozen=True)
class PolicySnapshot:
    normalized_json: str
    sha256: str
    paths: PathPolicy

    @property
    def value(self):
        return json.loads(self.normalized_json)


def normalize_domain(value):
    try:
        domain = value.encode('idna').decode('ascii').lower()
    except UnicodeError as error:
        raise ContractError('Invalid policy domain') from error
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        denied('IP literals require a separate trusted local-service capability')
    if not DOMAIN.fullmatch(domain) or domain.endswith(('.localhost', '.local', '.internal')):
        denied('Policy domains must be explicit public hostnames without URL, port or wildcard')
    return domain


def compile_policy(policy, workspace, *, control_roots=(), temporary_roots=()):
    validate('sandbox-policy', policy)
    value = json.loads(json.dumps(policy))
    if value['workspace_id'] != workspace['id']:
        denied('Policy is bound to another workspace')
    root = inspect_path(workspace['canonical_path'], absolute=True)
    identity = root.anchors[-1][1]
    if identity is None or not root.path.is_dir() or f'{identity[0]}:{identity[1]}' != workspace['file_identity']:
        raise ContractError('Workspace identity changed', kind='STALE_REVISION', code=-32010)
    audit_workspace_links(root.path)
    filesystem = value['filesystem']
    for name in ('read_roots', 'write_roots', 'protected_paths'):
        filesystem[name] = sorted(set(str(inspect_path(path, absolute=True).path) for path in filesystem[name]))
    trusted_temp = [inspect_path(path, absolute=True).path for path in temporary_roots]
    for path in filesystem['read_roots']:
        if not Path(path).is_dir():
            denied('Read root must be an existing directory')
    for path in filesystem['write_roots']:
        candidate = Path(path)
        if not any(candidate == allowed or candidate.is_relative_to(allowed) for allowed in (root.path, *trusted_temp)):
            denied('Write root is outside workspace or trusted temporary roots')
    protected = [*map(Path, filesystem['protected_paths']), root.path / '.forge', *git_metadata_paths(root.path)]
    for path in control_roots:
        candidate = inspect_path(path, absolute=True).path
        if not any(candidate == parent or candidate.is_relative_to(parent) for parent in protected):
            protected.append(candidate)
    filesystem['protected_paths'] = sorted(set(map(str, protected)))
    value['network']['allowed_domains'] = sorted(set(normalize_domain(name) for name in value['network']['allowed_domains']))
    if any(UNSAFE_ENV.search(name) for name in value['environment_keys']):
        denied('Unsafe control or credential environment key')
    value['environment_keys'] = sorted(set(value['environment_keys']))
    paths = PathPolicy(root, filesystem['read_mode'], tuple(map(Path, filesystem['read_roots'])),
                       tuple(map(Path, filesystem['write_roots'])), tuple(map(Path, filesystem['protected_paths'])))
    return PolicySnapshot(json.dumps(value, ensure_ascii=False, sort_keys=True), canonical_hash(value), paths)
