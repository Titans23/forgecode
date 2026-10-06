"""F01 real toolchain/packaging verifier; never opens a model connection."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(argv: list[str], env: dict | None = None, timeout: int = 300) -> dict:
    result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{argv[0]} exited {result.returncode}: {result.stderr[-3000:]} {result.stdout[-1000:]}')
    return {'command': argv, 'exit_code': result.returncode,
            'stdout': result.stdout[-3000:], 'stderr': result.stderr[-3000:]}


def verify() -> dict:
    checks = []
    npm = shutil.which('npm')
    if npm is None:
        return {'status': 'blocked', 'reason': 'npm is unavailable', 'checks': checks}
    target = f'{sys.platform}-x64'
    lock = json.loads((ROOT / 'release-lock.json').read_text(encoding='utf-8'))
    if platform.python_version() != lock['python']['version']:
        return {'status': 'blocked', 'reason': f"Build requires Python {lock['python']['version']}", 'checks': checks}
    if platform.machine().lower() not in {'amd64', 'x86_64'} or target not in {'win32-x64', 'linux-x64'}:
        return {'status': 'blocked', 'reason': 'Requires a supported x64 build host', 'checks': checks}
    node_asset = next(a for a in lock['assets'] if a['name'] == 'node' and a['platform'] == target)
    node = str(ROOT / node_asset['path'])
    checks.append(run([node, str(ROOT / 'packaging' / 'verify-release.mjs')]))
    node_probe = run([node, '--input-type=module', '-e',
                      "import {SandboxRuntimeConfigSchema} from '@anthropic-ai/sandbox-runtime';"
                      "const config=SandboxRuntimeConfigSchema.safeParse({network:{allowedDomains:[],deniedDomains:[]},"
                      "filesystem:{denyRead:[],allowWrite:[],denyWrite:[]}});"
                      "if(!config.success)process.exit(1);console.log(JSON.stringify({node:process.version,config:true}));"])
    assert json.loads(node_probe['stdout'])['node'] == 'v' + lock['node']['version']
    checks.append(node_probe)
    checks.append(run([sys.executable, '-X', 'utf8', '-m', 'PyInstaller', '--noconfirm',
                       '--distpath', '.local/build-smoke/python', '--workpath', '.local/build-smoke/pyinstaller',
                       'packaging/forge_harness_smoke.spec']))
    suffix = '.exe' if sys.platform == 'win32' else ''
    frozen = ROOT / '.local' / 'build-smoke' / 'python' / 'forge-harness-smoke' / f'forge-harness-smoke{suffix}'
    harness_probe = run([str(frozen)])
    harness = json.loads(harness_probe['stdout'])
    assert harness['frozen'] and 'prompt' in harness['conversation_stream']
    checks.append(harness_probe)
    checks.append(run([npm, 'run', 'package:smoke']))
    executable = ROOT / '.local' / 'build-smoke' / 'electron' / f'ForgeCode Build Smoke-{target}' / f'ForgeCode Build Smoke{suffix}'
    env = {**os.environ, 'FORGE_BUILD_SMOKE_HARNESS': str(frozen)}
    if sys.platform == 'linux' and not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
        return {'status': 'blocked', 'reason': 'Native Electron requires a graphical session', 'checks': checks}
    electron_probe = run([str(executable)], env, timeout=30)
    # Electron may emit platform diagnostics before the one JSON result.
    payload = next(json.loads(line) for line in reversed(electron_probe['stdout'].splitlines()) if line.startswith('{'))
    assert payload['status'] == 'pass' and payload['harness']['frozen']
    assert payload['electron'] == next(c['version'] for c in lock['components'] if c['name'] == 'electron')
    checks.append(electron_probe)
    native_windows = sys.platform == 'win32' and int(platform.version().split('.')[-1]) >= 22000
    ubuntu = platform.freedesktop_os_release() if sys.platform == 'linux' else {}
    supported_linux = ubuntu.get('ID') == 'ubuntu' and ubuntu.get('VERSION_ID') in {'22.04', '24.04'}
    accepted = native_windows or supported_linux
    return {'status': 'pass' if accepted else 'blocked', 'development_smoke': 'pass',
            'reason': None if accepted else 'Build smoke passed; this host is outside the declared Windows 11 / Ubuntu acceptance matrix',
            'platform': platform.platform(), 'checks': checks,
            'security_status': lock['security']['status']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        report = verify()
    except (OSError, RuntimeError, ValueError, AssertionError, subprocess.TimeoutExpired, StopIteration) as error:
        report = {'status': 'fail', 'reason': str(error)}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit({'pass': 0, 'fail': 1, 'blocked': 2}[report['status']])
