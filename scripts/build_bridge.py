"""Build and inventory the fixed Bridge dependency closure. --check never rewrites manifests."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def dependency_paths(lock):
    packages = lock['packages']
    if any(path.endswith(('/node-forge', '/@anthropic-ai/sandbox-runtime')) for path in packages):
        raise ValueError('Removed sandbox dependencies must not re-enter the delivery lockfile')
    pending = ['node_modules/ajv', 'node_modules/ajv-formats', 'node_modules/@deepseek-ai/dsh-sandbox-windows-acl']
    seen = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        package = packages[path]
        for name in package.get('dependencies', {}) | package.get('peerDependencies', {}):
            parent = path
            candidates = [parent + '/node_modules/' + name]
            while '/node_modules/' in parent:
                parent = parent.rsplit('/node_modules/', 1)[0]
                candidates.append(parent + '/node_modules/' + name)
            candidates.append('node_modules/' + name)
            candidate = next((value for value in candidates if value in packages), None)
            if candidate is None and package.get('peerDependenciesMeta', {}).get(name, {}).get('optional'):
                continue
            if candidate is None:
                raise ValueError(f'Locked runtime dependency is unresolved: {name}')
            pending.append(candidate)
    return seen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--no-build', action='store_true')
    parser.add_argument('--bundle-python', action='store_true', help='Include verified sandbox assets in a Python wheel')
    args = parser.parse_args()
    if not args.no_build:
        node = shutil.which('node')
        if node is None:
            raise RuntimeError('Development compiler runtime is unavailable')
        for project in ('packages/contracts', 'sandbox_bridge'):
            # Retired sources must not survive as executable build outputs.
            directory = ROOT / project / 'dist'
            for compiled in directory.rglob('*.js'):
                if not (ROOT / project / 'src' / compiled.relative_to(directory).with_suffix('.ts')).is_file():
                    compiled.unlink()
                    compiled.with_suffix('.d.ts').unlink(missing_ok=True)
            subprocess.run([node, str(ROOT / 'node_modules/typescript/bin/tsc'), '-p', str(ROOT / project / 'tsconfig.json')], cwd=ROOT, check=True)
    package_lock = json.loads((ROOT / 'package-lock.json').read_text(encoding='utf-8'))
    files = {'package.json', 'sandbox_bridge/package.json', 'packages/contracts/package.json'}
    for directory in (*dependency_paths(package_lock), 'sandbox_bridge/dist', 'packages/contracts/dist'):
        for path in (ROOT / directory).rglob('*'):
            if path.is_file():
                if not path.resolve().is_relative_to(ROOT):
                    raise ValueError('Runtime dependency points outside the installation')
                files.add(path.relative_to(ROOT).as_posix())
    inventory = {'schema_version': 'forge.bridge.runtime.v1', 'package_lock_sha256': digest(ROOT / 'package-lock.json'), 'files': [
        {'path': path, 'sha256': digest(ROOT / path)} for path in sorted(files)]}
    manifest = ROOT / 'sandbox_bridge/runtime-manifest.json'
    encoded = (json.dumps(inventory, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if args.check:
        if manifest.read_bytes() != encoded:
            raise ValueError('Bridge runtime inventory differs from the actual compiled/dependency bytes')
    else:
        manifest.write_bytes(encoded)
    if args.bundle_python:
        bundle_python(inventory)
    print(json.dumps({'status': 'pass', 'scope': 'bridge-runtime-inventory', 'files': len(files), 'check': args.check}))


def bundle_python(inventory):
    """A wheel carries the same verified closure, with a fixed workspace-package alias."""
    destination = ROOT / 'forge' / '_sandbox'
    destination.mkdir(parents=True, exist_ok=True)
    files = {}
    for entry in inventory['files']:
        source = ROOT / entry['path']
        target = destination / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        files[entry['path']] = digest(target)
    for name, content in {
        'node_modules/@forgecode/contracts/package.json': '{"type":"module","exports":"./index.js"}\n',
        'node_modules/@forgecode/contracts/index.js': 'export * from "../../../packages/contracts/dist/index.js";\n',
    }.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        files[name] = digest(target)
    shutil.copyfile(ROOT / 'package-lock.json', destination / 'package-lock.json')
    manifest = destination / 'sandbox_bridge/runtime-manifest.json'
    manifest.write_text(json.dumps({**inventory, 'files': [
        {'path': path, 'sha256': value} for path, value in sorted(files.items())]}, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'scope': 'python-sandbox-bundle', 'files': len(files)}))


if __name__ == '__main__':
    main()
