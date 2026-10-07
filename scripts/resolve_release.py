"""Resolve F01 assets from pinned official sources; no administrator setup."""
from __future__ import annotations

from hashlib import sha256
from importlib import metadata
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NODE_VERSION = '24.21.0'


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def resolve() -> dict:
    package = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))
    npm_lock = json.loads((ROOT / 'package-lock.json').read_text(encoding='utf-8'))
    components, assets = [], []
    for name, version in {**package['dependencies'], **package['devDependencies']}.items():
        entry = npm_lock['packages'][f'node_modules/{name}']
        installed = json.loads((ROOT / 'node_modules' / name / 'package.json').read_text(encoding='utf-8'))
        if entry['version'] != version or installed['version'] != version:
            raise ValueError(f'Version mismatch: {name}')
        components.append({'name': name, 'kind': 'npm', 'version': version,
                           'source': entry['resolved'], 'integrity': entry['integrity'],
                           'license': installed.get('license', entry.get('license')),
                           'minimum_runtime': installed.get('engines', {}), 'patches': [],
                           'compatibility_tests': ['npm run test:unit', 'npm run package:smoke']})
    source = f'https://nodejs.org/dist/v{NODE_VERSION}'
    checksums = dict(line.split()[::-1] for line in fetch(f'{source}/SHASUMS256.txt').decode().splitlines())
    for target, extension, executable in [('win-x64', 'zip', 'node.exe'), ('linux-x64', 'tar.xz', 'bin/node')]:
        filename = f'node-v{NODE_VERSION}-{target}.{extension}'
        archive = fetch(f'{source}/{filename}')
        if sha256(archive).hexdigest() != checksums[filename]:
            raise ValueError(f'Official Node checksum mismatch: {filename}')
        member = f'node-v{NODE_VERSION}-{target}/{executable}'
        if extension == 'zip':
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                binary = bundle.read(member)
        else:
            with tarfile.open(fileobj=io.BytesIO(archive), mode='r:xz') as bundle:
                info = bundle.getmember(member)
                if not info.isfile():
                    raise ValueError('Node runtime must be a regular archive member')
                binary = bundle.extractfile(info).read()
        # Only the fixed runtime member is extracted; never extractall arbitrary paths.
        directory = ROOT / '.local' / 'release-runtime' / f'node-{NODE_VERSION}-{target}'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / Path(executable).name
        path.write_bytes(binary)
        if target == 'linux-x64':
            path.chmod(0o755)
        assets.append({'name': 'node', 'platform': 'win32-x64' if target == 'win-x64' else target,
                       'path': path.relative_to(ROOT).as_posix(), 'sha256': digest(path),
                       'source': f'{source}/{filename}', 'archive_sha256': checksums[filename],
                       'license': 'MIT', 'patches': []})
    vendor = ROOT / 'node_modules' / '@anthropic-ai' / 'sandbox-runtime' / 'vendor'
    for name, relative, target in [('srt-win', 'srt-win/x64/srt-win.exe', 'win32-x64'),
                                    ('apply-seccomp', 'seccomp/x64/apply-seccomp', 'linux-x64'),
                                    ('java-proxy-agent', 'java-proxy-agent/srt-proxy-agent.jar', 'all')]:
        path = vendor / relative
        assets.append({'name': name, 'platform': target, 'path': path.relative_to(ROOT).as_posix(),
                       'sha256': digest(path), 'source': '@anthropic-ai/sandbox-runtime@0.0.78',
                       'license': 'Apache-2.0', 'patches': []})
    for name in ('pyinstaller', 'pyinstaller-hooks-contrib', 'aiohttp'):
        item = metadata.metadata(name)
        components.append({'name': name, 'kind': 'python', 'version': metadata.version(name),
                           'source': f'https://pypi.org/project/{name}/{metadata.version(name)}/',
                           'integrity': 'uv.lock (distribution SHA-256)',
                           'license': item.get('License-Expression') or item.get('License'),
                           'minimum_runtime': item.get('Requires-Python'), 'patches': [],
                           'compatibility_tests': ['python -m pytest tests/implementation/integration/test_http_adapter.py']
                           if name == 'aiohttp' else ['python -m PyInstaller packaging/forge_harness_smoke.spec']})
    lock = {'schema_version': 'forge.release.lock.v1', 'resolution_status': 'resolved',
            'python': {'version': platform.python_version(), 'minimum': '3.12',
                       'source': 'https://github.com/astral-sh/python-build-standalone',
                       'license': 'PSF-2.0', 'patches': []},
            'node': {'version': NODE_VERSION, 'source': source, 'license': 'MIT', 'patches': []},
            'components': components, 'assets': assets,
            'lockfile_hashes': {name: digest(ROOT / name) for name in ('uv.lock', 'package-lock.json')},
            'security': {'status': 'blocked', 'advisories': ['GHSA-86w9-cpqp-85rv'],
                         'reason': 'SRT depends on node-forge 1.4.0; no patched npm release as of 2026-10-06. Production release requires mitigation and regression evidence.'},
            'platform_requirements': {
                'win32-x64': ['Windows 11 x64', 'SRT windows-install explicitly authorized by user'],
                'linux-x64': ['Ubuntu 22.04/24.04 x64', 'bubblewrap', 'socat', 'ripgrep', 'fakeroot/dpkg for deb']}}
    (ROOT / 'release-lock.json').write_text(json.dumps(lock, indent=2) + '\n', encoding='utf-8')
    node = next(a for a in assets if a['name'] == 'node' and a['platform'] == ('win32-x64' if sys.platform == 'win32' else 'linux-x64'))
    actual = subprocess.check_output([str(ROOT / node['path']), '--version'], text=True).strip()
    if actual != f'v{NODE_VERSION}':
        raise ValueError('Pinned runtime returned an unexpected version')
    return {'status': 'pass', 'node': actual, 'assets': len(assets), 'components': len(components)}


if __name__ == '__main__':
    print(json.dumps(resolve()))
