"""Compile the actual Main boundary and inventory fixed development Engine/UI assets."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def asset(path):
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-only', action='store_true')
    parser.add_argument('--check', action='store_true', help='Compare inventories without changing their contents')
    args = parser.parse_args()
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Development Node compiler is unavailable')
    compiler = ROOT / 'node_modules/typescript/bin/tsc'
    for project in ('packages/contracts/tsconfig.json', 'apps/desktop/tsconfig.main.json'):
        subprocess.run([node, str(compiler), '-p', str(ROOT / project)], cwd=ROOT, check=True)
    python = ROOT / '.venv' / ('Scripts/python.exe' if __import__('os').name == 'nt' else 'bin/python')
    manifest = {'schema_version': 'forge.desktop.development-assets.v1', 'python': asset(python),
        'contracts': asset(ROOT / 'forge/application/_generated_contracts.json'),
        'locks': [asset(ROOT / name) for name in ('uv.lock', 'package-lock.json')],
        'engine_sources': [asset(path) for path in sorted((ROOT / 'forge').rglob('*'))
            if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py', '.json', '.sql', '.md')]}
    def inventory(path, value):
        encoded = (json.dumps(value, indent=2) + '\n').encode('utf-8')
        if args.check:
            if path.read_bytes() != encoded:
                raise ValueError(f'Desktop inventory differs from actual assets: {path.name}')
        else:
            path.write_bytes(encoded)
    inventory(ROOT / 'apps/desktop/development-assets.json', manifest)
    if not args.main_only:
        for name in ('main', 'preload', 'renderer'):
            subprocess.run([node, str(ROOT / 'node_modules/vite/bin/vite.js'), 'build', '--config',
                str(ROOT / f'apps/desktop/vite.{name}.config.mjs')], cwd=ROOT / 'apps/desktop', check=True)
        ui = ROOT / 'apps/desktop/.vite/renderer/ui'
        files = {'/' + path.relative_to(ui).as_posix(): {
            'path': path.relative_to(ROOT / 'apps/desktop').as_posix(),
            'sha256': sha256(path.read_bytes()).hexdigest()}
            for path in sorted(ui.rglob('*')) if path.is_file()}
        if '/index.html' not in files:
            raise RuntimeError('Built UI entry is missing')
        inventory(ROOT / 'apps/desktop/ui-assets.json', files)
    print(json.dumps({'status': 'pass', 'scope': 'desktop-development-assets', 'main_only': args.main_only,
        'engine_files': len(manifest['engine_sources'])}))


if __name__ == '__main__':
    main()
