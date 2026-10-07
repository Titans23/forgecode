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
    pending = ['node_modules/@anthropic-ai/sandbox-runtime', 'node_modules/ajv', 'node_modules/ajv-formats']
    seen = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        package = packages[path]
        for name in package.get('dependencies', {}):
            parent = path
            candidates = [parent + '/node_modules/' + name]
            while '/node_modules/' in parent:
                parent = parent.rsplit('/node_modules/', 1)[0]
                candidates.append(parent + '/node_modules/' + name)
            candidates.append('node_modules/' + name)
            candidate = next((value for value in candidates if value in packages), None)
            if candidate is None:
                raise ValueError(f'Locked runtime dependency is unresolved: {name}')
            pending.append(candidate)
    return seen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--no-build', action='store_true')
    args = parser.parse_args()
    if not args.no_build:
        node = shutil.which('node')
        if node is None:
            raise RuntimeError('Development compiler runtime is unavailable')
        subprocess.run([node,str(ROOT/'scripts/build_browser_contracts.mjs'),*(['--check'] if args.check else [])],cwd=ROOT,check=True)
        for project in ('packages/contracts', 'sandbox_bridge'):
            subprocess.run([node, str(ROOT / 'node_modules/typescript/bin/tsc'), '-p', str(ROOT / project / 'tsconfig.json')], cwd=ROOT, check=True)
    package_lock = json.loads((ROOT / 'package-lock.json').read_text(encoding='utf-8'))
    files = {'package.json', 'sandbox_bridge/package.json', 'packages/contracts/package.json'}
    for directory in (*dependency_paths(package_lock), 'sandbox_bridge/dist', 'packages/contracts/dist'):
        for path in (ROOT / directory).rglob('*'):
            if path.is_file():
                if not path.resolve().is_relative_to(ROOT):
                    raise ValueError('Runtime dependency points outside the installation')
                files.add(path.relative_to(ROOT).as_posix())
    inventory = {'schema_version': 'forge.bridge.runtime.v1', 'files': [
        {'path': path, 'sha256': digest(ROOT / path)} for path in sorted(files)]}
    manifest = ROOT / 'sandbox_bridge/runtime-manifest.json'
    encoded = (json.dumps(inventory, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    lock_path = ROOT / 'release-lock.json'
    release = json.loads(lock_path.read_text(encoding='utf-8'))
    asset = {'name': 'bridge-runtime-manifest', 'platform': 'all',
             'path': manifest.relative_to(ROOT).as_posix(), 'sha256': sha256(encoded).hexdigest(),
             'source': 'ForgeCode locked runtime code inventory', 'license': 'Apache-2.0', 'patches': []}
    if args.check:
        if manifest.read_bytes() != encoded or asset not in release['assets']:
            raise ValueError('Bridge runtime inventory differs from the actual compiled/dependency bytes')
        if release['lockfile_hashes']['package-lock.json'] != digest(ROOT / 'package-lock.json'):
            raise ValueError('Release package-lock hash is stale')
    else:
        manifest.write_bytes(encoded)
        release['assets'] = [value for value in release['assets'] if value['name'] != asset['name']] + [asset]
        release['lockfile_hashes']['package-lock.json'] = digest(ROOT / 'package-lock.json')
        lock_path.write_text(json.dumps(release, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({'status': 'pass', 'scope': 'bridge-runtime-inventory', 'files': len(files), 'check': args.check}))


if __name__ == '__main__':
    main()
