"""Build the npm archive for the current platform. Never publishes it."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_wheel import check as check_wheel
from scripts.evidence_gate import source_fingerprint


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def run_logged(command, cwd, log):
    with log.open('a', encoding='utf-8') as stream:
        result = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise ValueError(f'Build command failed with exit code {result.returncode}; see {log}')


def check_archive(archive, wheel_name):
    with tarfile.open(archive) as package_archive:
        names = package_archive.getnames()
        private_names = {'.npmrc', 'id_rsa', 'id_ed25519', 'credentials.json'}
        for name in names:
            parts = [part.casefold() for part in Path(name).parts]
            if any(part == '.env' or part.startswith('.env.') or part in private_names for part in parts) or '.venv' in parts or 'tests' in parts:
                raise ValueError('Private or development files leaked into the npm archive.')
        required = {'package/bin/forge.mjs', 'package/lib/install.mjs', 'package/assets/runtime.json',
                    'package/assets/constraints.txt', 'package/assets/' + wheel_name}
        if not required.issubset(names):
            raise ValueError('npm archive is incomplete.')
    return len(names)


def build(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    log = output / 'package-build.log'
    log.write_text('', encoding='utf-8')
    uv = shutil.which('uv')
    node = shutil.which('node')
    npm = shutil.which('npm')
    if not uv or not node or not npm:
        raise ValueError('Maintainer build requires uv and Node/npm.')
    npm = Path(npm).resolve()
    npm_cli = os.environ.get('npm_execpath') or (npm.parent / 'node_modules/npm/bin/npm-cli.js' if sys.platform == 'win32' else npm)
    npm_command = [node, str(npm_cli)]
    run_logged([*npm_command, 'ci', '--ignore-scripts'], ROOT, log)
    run_logged([sys.executable, str(ROOT / 'scripts/build_bridge.py'), '--bundle-python'], ROOT, log)
    native = ROOT / 'forge/_sandbox/node_modules/@koromix/koffi-win32-x64/win32_x64/koffi.node'
    if sys.platform == 'win32' and not native.is_file():
        raise ValueError('Windows archive is missing the locked Windows x64 Koffi asset.')
    prepared_source = source_fingerprint(ROOT)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    with tempfile.TemporaryDirectory(prefix='forge-npm-', dir=output) as temporary:
        stage = Path(temporary)
        if not stage.resolve().is_relative_to(output):
            raise ValueError('Package staging exceeds the selected output directory.')
        run_logged([uv, 'build', '--wheel', '--out-dir', str(stage / 'wheel')], ROOT, log)
        wheel, = (stage / 'wheel').glob('*.whl')
        wheel_report = check_wheel(wheel)
        package = stage / 'package'
        shutil.copytree(ROOT / 'packaging/npm', package)
        metadata = json.loads((package / 'package.json').read_text(encoding='utf-8'))
        if metadata['version'] != version:
            raise ValueError('npm and Python package versions differ.')
        metadata['os'] = [sys.platform]
        (package / 'package.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
        assets = package / 'assets'
        assets.mkdir()
        shutil.copyfile(wheel, assets / wheel.name)
        constraints = assets / 'constraints.txt'
        subprocess.run([uv, 'export', '--locked', '--no-dev', '--no-emit-project', '--no-hashes',
                        '--output-file', str(constraints)], cwd=ROOT, stdout=subprocess.DEVNULL, check=True)
        runtime = {'python_version': (ROOT / '.python-version').read_text(encoding='utf-8').strip(),
                   'wheel': {'file': wheel.name, 'sha256': digest(wheel)},
                   'constraints_sha256': digest(constraints), 'tools_sha256': digest(package / 'lib/tools.json')}
        (assets / 'runtime.json').write_text(json.dumps(runtime, indent=2) + '\n', encoding='utf-8')
        packed = subprocess.run([*npm_command, 'pack', '--json', '--pack-destination', str(output)],
                                cwd=package, check=True, capture_output=True, text=True, encoding='utf-8')
        archive = output / json.loads(packed.stdout)[0]['filename']
    files = check_archive(archive, wheel_report['wheel'])
    if source_fingerprint(ROOT) != prepared_source:
        raise ValueError('Source changed during package creation; rebuild the archive.')
    return {'status': 'pass', 'scope': 'npm-archive', 'archive': str(archive), 'sha256': digest(archive),
            'source_inventory_hash': prepared_source,
            'platforms': ['win32-x64'] if sys.platform == 'win32' else ['ubuntu-22.04-x64', 'ubuntu-24.04-x64'],
            'wheel': wheel_report, 'files': files, 'published': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    try:
        report = build(args.output_dir)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(json.dumps({'status': 'fail', 'reason': str(error)}))
        return 1
    (args.output_dir / 'npm-package-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
