"""Standard ZIP/source delivery with explicit files, dependency pins and receipts."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit
import zipfile

from forge.agents import AgentDefinition
from forge.validation import bindings, dependency_versions, digest, plugin_files, relative_file, validate_acceptance


def portable(value):
    """Reject literal credentials in configuration. References remain ordinary strings."""
    if isinstance(value, dict):
        for key, item in value.items():
            secret = re.search(r'(^|_)(api_key|password|secret|token|credential|authorization)($|_)', key, re.I)
            if secret and not key.endswith(('_env', '_ref')) and item:
                raise ValueError(f'Use a credential environment reference for {key}')
            portable(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            portable(item)
    elif isinstance(value, str) and value.startswith(('https://', 'http://')):
        url = urlsplit(value)
        if url.username or url.password or url.query or url.fragment:
            raise ValueError('Portable endpoints must not embed credentials or query parameters')


def load_definition(path, *, trust=False):
    path = Path(path).resolve(strict=True)
    value = json.loads(path.read_text(encoding='utf-8'))
    agent = AgentDefinition.from_dict(value.get('agent', value))
    specs = []
    for spec in agent.plugins:
        source = spec.source if spec.source.startswith('entrypoint:') else str((path.parent / spec.source).resolve(strict=True))
        specs.append(replace(spec, source=source, trusted=trust))
    return replace(agent, plugins=tuple(specs))


def export_agent(definition, acceptance, destination, *, receipt=None, models=None, sdk_wheel=None):
    acceptance = validate_acceptance(acceptance)
    portable(definition.as_dict())
    portable(models or {})
    portable(acceptance)
    current = bindings(definition, acceptance)
    files, specs = {}, []
    for index, spec in enumerate(definition.plugins):
        if spec.source.startswith('entrypoint:'):
            specs.append(replace(spec, trusted=False))
            continue
        root = f'plugins/{index}'
        for name, raw in plugin_files(spec.source).items():
            # Selected source is public delivery material; common embedded key forms are rejected.
            if re.search(rb'(?:api[_-]?key|password|secret|access[_-]?token)\s*[=:]\s*[\x22\x27][^\x22\x27\r\n]{4,}', raw, re.I):
                raise ValueError(f'Possible embedded credential in {name}; use environment references')
            files[f'{root}/{name}'] = raw
        specs.append(replace(spec, source=root, trusted=False))
    agent = replace(definition, plugins=tuple(specs))
    delivery_dependencies = dependency_versions(definition, sdk_extras=('cli', 'validation') if sdk_wheel else ())
    requirements = ''.join(f'{name}=={version}\n' for name, version in delivery_dependencies.items())
    if sdk_wheel:
        sdk_wheel = Path(sdk_wheel)
        if sdk_wheel.suffix != '.whl':
            raise ValueError('SDK artifact must be a standard wheel')
        with zipfile.ZipFile(sdk_wheel) as wheel:
            sdk_files = {name.removeprefix('forge/'): sha256(wheel.read(name)).hexdigest()
                         for name in wheel.namelist() if name.startswith('forge/') and
                         Path(name).suffix in {'.py', '.md', '.json', '.sql'} and
                         '_sandbox' not in PurePosixPath(name).parts and '__pycache__' not in PurePosixPath(name).parts}
        if digest(sdk_files) != current['sdk']:
            raise ValueError('SDK wheel source does not match the checked runtime; rebuild the wheel')
        name = 'artifacts/' + sdk_wheel.name
        relative_file(name)
        files[name] = sdk_wheel.read_bytes()
        requirements = requirements.replace(f"forge-code=={current['dependencies']['forge-code']}\n", './' + name + '\n')
    files['requirements.lock'] = requirements.encode()
    files['agent.json'] = json.dumps(agent.as_dict(), indent=2).encode()
    files['acceptance.json'] = json.dumps(acceptance, indent=2).encode()
    files['models.json'] = json.dumps(models or {}, indent=2).encode()
    if receipt:
        if receipt.get('bindings') != current:
            raise ValueError('Receipt is stale for this source/configuration/acceptance')
        # No raw candidate output, sessions or environment values are exported.
        public_receipt = {k: receipt.get(k) for k in ('schema_version', 'status', 'bindings', 'environment', 'source_unchanged')}
        public_receipt.update(interface={'status': receipt.get('interface', {}).get('status', 'unverified')},
                              business={'status': receipt.get('business', {}).get('status', 'unverified')},
                              model_evaluation={'status': 'unverified', 'api_calls': 0})
        files['validation.json'] = json.dumps(public_receipt, indent=2).encode()
    manifest = {'schema_version': 'forge.agent.delivery.v1', 'agent': 'agent.json', 'acceptance': 'acceptance.json',
        'dependencies': 'requirements.lock', 'sdk_source_hash': current['sdk'],
        'sdk_artifact': 'included' if sdk_wheel else 'requires matching preinstalled SDK; version pin alone does not identify development source',
        'files': {name: sha256(raw).hexdigest() for name, raw in files.items()},
        'validation': 'source-environment evidence only; import requires fresh checks',
        'credentials': 'environment references only; no settings, environment files or sessions collected'}
    files['manifest.json'] = json.dumps(manifest, indent=2).encode()
    destination = Path(destination)
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return manifest


def import_agent(archive_path, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Import requires a new directory')
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        names = [i.filename for i in entries]
        if len(set(n.casefold() for n in names)) != len(names) or len(names) > 2000 or sum(i.file_size for i in entries) > 50_000_000:
            raise ValueError('Duplicate or oversized delivery entries')
        for item in entries:
            relative_file(item.filename)
            if item.is_dir() or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Delivery entries must be regular files')
        files = {name: archive.read(name) for name in names}
    manifest = json.loads(files.pop('manifest.json'))
    if manifest.get('schema_version') != 'forge.agent.delivery.v1' or manifest.get('files') != {
            name: sha256(raw).hexdigest() for name, raw in files.items()}:
        raise ValueError('Delivery manifest does not match artifact bytes')
    agent = AgentDefinition.from_dict(json.loads(files['agent.json']))
    portable(agent.as_dict())
    portable(json.loads(files['models.json']))
    validate_acceptance(json.loads(files['acceptance.json']))
    for spec in agent.plugins:
        if spec.trusted:
            raise ValueError('Imported plugins must remain disabled/untrusted')
        if not spec.source.startswith('entrypoint:'):
            relative_file(spec.source)
            if spec.source + '/forge-plugin.json' not in files:
                raise ValueError('Plugin source is missing from delivery')
    destination.mkdir(parents=True)
    for name, raw in files.items():
        target = destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return {'status': 'imported', 'directory': str(destination), 'plugins_enabled': False,
            'validation': 'unverified', 'next': 'Install requirements.lock with pip/uv; configure referenced credentials; agent check --trust'}
