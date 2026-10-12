"""Verify delivered wheel bytes, sandbox inventory and excluded client assets."""
import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import zipfile


def check(path):
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        prefix = 'forge/_sandbox/'
        manifest = json.loads(archive.read(prefix + 'sandbox_bridge/runtime-manifest.json'))
        if manifest['schema_version'] != 'forge.bridge.runtime.v1' or not manifest['files']:
            raise ValueError('Sandbox inventory is missing')
        for entry in manifest['files']:
            relative = PurePosixPath(entry['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Sandbox asset escapes its root')
            if sha256(archive.read(prefix + entry['path'])).hexdigest() != entry['sha256']:
                raise ValueError('Sandbox asset hash differs: ' + entry['path'])
        if sha256(archive.read(prefix + 'package-lock.json')).hexdigest() != manifest['package_lock_sha256']:
            raise ValueError('Sandbox dependency lock hash differs')
        if 'forge/engine/http_adapter.py' in names or any(
                '/node_modules/' + package + '/' in name for name in names
                for package in ('electron', 'react', 'react-dom', 'vite')):
            raise ValueError('Client assets must not be included in a terminal wheel')
    return {'status': 'pass', 'scope': 'wheel-contents', 'wheel': path.name,
        'sha256': sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size,
        'sandbox_files': len(manifest['files'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = check(args.wheel)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
        result = {'status': 'fail', 'scope': 'wheel-contents', 'reason': str(error)}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
