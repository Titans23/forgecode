"""Controller-owned deterministic acceptance. No model credentials or model requests.

Candidate Python runs in an owned process with a private working directory. This
is not a sandbox for malicious plugins. Expected values never enter that process.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import importlib.metadata as metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import sys
import tempfile
import tomllib

from forge.agents import AgentDefinition, PluginSpec


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                             allow_nan=False).encode()).hexdigest()


def relative_file(name):
    if not isinstance(name, str) or not name:
        raise ValueError('Artifact path must be a nonempty string')
    path = PurePosixPath(name)
    if '\\' in name or ':' in name or path.is_absolute() or any(
            p in {'.', '..'} or p.startswith('.') for p in path.parts):
        raise ValueError(f'Expected a public relative artifact path: {name}')
    return path


def plugin_files(source):
    """Explicit distributable source files; never sweep a workspace or session store."""
    root = Path(source).resolve(strict=True)
    manifest = json.loads((root / 'forge-plugin.json').read_text(encoding='utf-8'))
    selected = manifest.get('files')
    if not selected or not isinstance(selected, list):
        raise ValueError('forge-plugin.json must list distributable files for validation/export')
    files = {}
    for name in dict.fromkeys(['forge-plugin.json', *selected]):
        relative = relative_file(name)
        path = root.joinpath(*relative.parts)
        if any(part.is_symlink() or part.is_junction() for part in [path, *path.parents] if part != root.parent):
            raise ValueError('Artifact links are not supported')
        if not path.resolve(strict=True).is_relative_to(root) or not path.is_file():
            raise ValueError('Artifact must be an in-package file')
        raw = path.read_bytes()
        if len(raw) > 10_000_000:
            raise ValueError('Plugin artifact exceeds 10 MB')
        files[relative.as_posix()] = raw
    entry = manifest.get('entry_point', 'plugin.py:activate').split(':')[0]
    if entry not in files:
        raise ValueError('Plugin entry point must be included in files')
    return files


def dependency_versions(definition, *, sdk_extras=()):
    """Resolve declared dependencies with standard PEP 508 parsing, never install."""
    from packaging.requirements import Requirement
    requested = ['forge-code' + ('[' + ','.join(sdk_extras) + ']' if sdk_extras else ''), 'packaging']
    for spec in definition.plugins:
        if spec.source.startswith('entrypoint:'):
            entries = list(metadata.entry_points(group='forge.plugins.v1', name=spec.source.split(':', 1)[1]))
            if len(entries) != 1 or entries[0].dist is None:
                raise ValueError('Installed plugin distribution is unavailable')
            requested.append(entries[0].dist.metadata['Name'])
        else:
            path = Path(spec.source) / 'pyproject.toml'
            if path.is_file():
                requested.extend(tomllib.loads(path.read_text(encoding='utf-8')).get('project', {}).get('dependencies', []))
    pending = [(item, ('',)) for item in requested]
    found, expanded = {}, set()
    while pending:
        raw, extras = pending.pop()
        req = Requirement(raw)
        if req.marker and not any(req.marker.evaluate({'extra': extra}) for extra in extras):
            continue
        if req.url:
            raise ValueError('Validation requires installed, versioned dependencies; direct URL references are not portable')
        dist = metadata.distribution(req.name)
        if req.specifier and dist.version not in req.specifier:
            raise ValueError(f'Installed dependency does not satisfy {req}')
        name = dist.metadata['Name'].lower().replace('_', '-')
        found[name] = dist.version
        variant = (name, tuple(sorted(req.extras)))
        if variant not in expanded:
            expanded.add(variant)
            pending.extend((item, ('', *req.extras)) for item in dist.requires or [])
    return dict(sorted(found.items()))


def bindings(definition, acceptance):
    plugins = []
    for spec in definition.plugins:
        if spec.source.startswith('entrypoint:'):
            entry = next(iter(metadata.entry_points(group='forge.plugins.v1', name=spec.source.split(':', 1)[1])), None)
            if entry is None or entry.dist is None:
                raise ValueError('Installed plugin is unavailable')
            files = {str(f): sha256(entry.dist.locate_file(f).read_bytes()).hexdigest()
                     for f in entry.dist.files or () if str(f).endswith(('.py', '.json', '.toml'))}
        else:
            files = {name: sha256(raw).hexdigest() for name, raw in plugin_files(spec.source).items()}
        plugins.append({'code': files, 'config': spec.config, 'requires': spec.requires})
    sdk = Path(__file__).parent
    sdk_hash = digest({p.relative_to(sdk).as_posix(): sha256(p.read_bytes()).hexdigest()
                       for p in sorted(sdk.rglob('*')) if p.is_file() and p.suffix in {'.py', '.md', '.json', '.sql'}
                       and '_sandbox' not in p.parts and '__pycache__' not in p.parts})
    versions = dependency_versions(definition)
    interface = digest({'plugins': [{k: v for k, v in p.items() if k != 'config'} for p in plugins],
                        'sdk': sdk_hash, 'dependencies': versions})
    composition = definition.as_dict()
    composition.pop('role_models')  # Deterministic checks never exercise model selection.
    composition['plugins'] = plugins
    return {'interface': interface, 'business': digest({'interface': interface, 'agent': composition, 'cases': acceptance}),
            'sdk': sdk_hash, 'dependencies': versions}


def validate_acceptance(value):
    value = deepcopy(value)
    if not isinstance(value, dict) or set(value) - {'cases', 'files'} or not isinstance(value.get('cases'), list):
        raise ValueError('Acceptance must contain cases and optional fixture files')
    ids = set()
    for case in value['cases']:
        if not isinstance(case, dict) or set(case) - {'id', 'tool', 'arguments', 'expect', 'cancel_after_ms'}:
            raise ValueError('Invalid acceptance case fields')
        identity = case.get('id')
        if not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError('Acceptance case IDs must be unique nonempty strings')
        ids.add(identity)
        if not isinstance(case.get('tool'), str) or not isinstance(case.get('arguments'), dict):
            raise ValueError('A case requires a tool and arguments')
        expect = case.get('expect')
        if not isinstance(expect, dict) or len(expect) != 1 or not set(expect) <= {'content', 'json', 'error', 'cancelled'}:
            raise ValueError('Expected result must specify exactly one of content/json/error/cancelled')
        if 'cancel_after_ms' in case and (type(case['cancel_after_ms']) is not int or not 1 <= case['cancel_after_ms'] <= 5000):
            raise ValueError('Cancellation delay must be 1..5000 milliseconds')
    files = value.get('files', {})
    if not isinstance(files, dict) or not all(isinstance(content, str) for content in files.values()):
        raise ValueError('Fixtures must map relative paths to UTF-8 text')
    for name in files:
        relative_file(name)
    return value


def match_result(actual, expected):
    if actual is None:
        return False
    if 'cancelled' in expected:
        return actual.get('cancelled') is expected['cancelled']
    if 'error' in expected:
        return not actual.get('success') and (actual.get('error') or {}).get('code') == expected['error']
    if not actual.get('success'):
        return False
    if 'content' in expected:
        return actual.get('content') == expected['content']
    try:
        return json.loads(actual['content']) == expected['json']
    except (ValueError, KeyError, TypeError):
        return False


async def owned_command(argv, *, cwd, environment, payload=b'', timeout=60):
    from forge.hooks.runner import _communicate_bounded, HookOutputLimitError
    from forge.sandbox.process_owner import LocalProcessOwner
    owner = await LocalProcessOwner.start(argv, cwd=cwd, environment=environment, output=True, capture_stderr=True)
    process = owner.process
    failure = None
    stdout = stderr = b''
    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await _communicate_bounded(process, payload)
    except (TimeoutError, HookOutputLimitError) as error:
        failure = type(error).__name__
    finally:
        for descriptor in (1, 2):
            pipe = process._transport.get_pipe_transport(descriptor)
            if pipe:
                pipe.close()
        cleanup = await owner.close()
    return {'exit_code': process.returncode, 'failure': failure, 'cleanup': cleanup,
            'stdout': stdout.decode('utf-8', errors='replace'), 'stderr': stderr.decode('utf-8', errors='replace')}


async def check_agent(definition, acceptance, *, work_dir=None, timeout=60):
    if any(not spec.trusted for spec in definition.plugins):
        raise ValueError('Validation executes plugin code and self-tests; explicit trust is required')
    acceptance = validate_acceptance(acceptance)
    before = bindings(definition, acceptance)
    parent = Path(work_dir).resolve() if work_dir else None
    if parent:
        parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='forge-check-', dir=parent)).resolve()
    retained = True
    environment = {k: v for k, v in os.environ.items() if k.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE', 'LANG'}}
    environment.update(TEMP=str(scratch), TMP=str(scratch), HOME=str(scratch), USERPROFILE=str(scratch),
                       FORGE_DATA_DIR=str(scratch / 'settings'), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    specs, self_tests, copied = [], [], {}
    try:
        for i, spec in enumerate(definition.plugins):
            if spec.source.startswith('entrypoint:'):
                specs.append(spec)
                self_tests.append({'plugin': spec.source, 'status': 'unverified', 'reason': 'installed package has no source test directory'})
                continue
            root = scratch / f'plugin-{i}'
            for name, raw in plugin_files(spec.source).items():
                target = root.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                copied[target] = sha256(raw).hexdigest()
            specs.append(replace(spec, source=str(root)))
            if (root / 'tests').is_dir():
                result = await owned_command([sys.executable, '-I', '-B', '-m', 'unittest', 'discover', '-s', 'tests'],
                                             cwd=root, environment=environment, timeout=timeout)
                clean = result['cleanup']['state'] == 'clean'
                if not clean:
                    return {'status': 'failed', 'reason': 'self-test process cleanup unconfirmed', 'retained_directory': str(scratch)}
                no_tests = 'Ran 0 tests' in result['stderr'] and result['exit_code'] in (0, 5)
                self_tests.append({'plugin': i, 'status': 'unverified' if no_tests else
                                   'passed' if result['exit_code'] == 0 and not result['failure'] else 'failed',
                                   'output': (result['stdout'] + result['stderr'])[-4000:]})
            else:
                self_tests.append({'plugin': i, 'status': 'unverified', 'reason': 'no unittest test directory'})
        probe = replace(definition, plugins=tuple(specs), role_models={})
        payload = {'agent': probe.as_dict(), 'cases': [{k: v for k, v in c.items() if k != 'expect'} for c in acceptance['cases']],
                   'files': acceptance.get('files', {}), 'directory': str(scratch)}
        run = await owned_command([sys.executable, '-I', '-B', str(Path(__file__).with_name('_check_worker.py'))],
                                 cwd=scratch, environment=environment, payload=json.dumps(payload).encode(), timeout=timeout)
        retained = run['cleanup']['state'] != 'clean'
        try:
            observed = json.loads(run['stdout']) if run['exit_code'] == 0 and not run['failure'] else {}
        except ValueError:
            observed = {}
        after = bindings(definition, acceptance)
        stable = before == after and all(p.is_file() and not p.is_symlink() and
            sha256(p.read_bytes()).hexdigest() == expected for p, expected in copied.items())
        framework = observed.get('framework', {})
        passed = not retained and stable and framework.get('status') == 'passed'
        actual = observed.get('results', {})
        cases = [{'id': c['id'], 'status': 'passed' if match_result(actual.get(c['id']), c['expect']) else 'failed',
                  'actual': actual.get(c['id'])} for c in acceptance['cases']]
        business = 'unverified' if not cases else 'passed' if passed and all(c['status'] == 'passed' for c in cases) else 'failed'
        return {'schema_version': 'forge.validation.v1', 'status': 'passed' if business == 'passed' and
                all(t['status'] != 'failed' for t in self_tests) else 'failed' if business == 'failed' or not passed or
                any(t['status'] == 'failed' for t in self_tests) else 'unverified',
                'bindings': before, 'environment': {'python': platform.python_version(), 'platform': sys.platform},
                'interface': {'status': 'passed' if passed else 'failed', 'observed': framework},
                'business': {'status': business, 'cases': cases}, 'self_tests': self_tests,
                'source_unchanged': stable, 'process_cleanup': run['cleanup'], 'model_evaluation': {'status': 'unverified', 'api_calls': 0},
                'failure': run['failure'] or observed.get('error'),
                'retained_directory': str(scratch) if retained else None}
    finally:
        if not retained:
            shutil.rmtree(scratch)


def receipt_status(receipt, definition, acceptance):
    current = bindings(definition, validate_acceptance(acceptance))
    return {layer: receipt.get(layer, {}).get('status', 'unverified') if receipt.get('bindings', {}).get(layer) == current[layer]
            else 'stale' for layer in ('interface', 'business')}
