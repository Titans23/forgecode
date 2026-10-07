"""Build and inspect fixed installed resources; missing assembly is blocked, never a source fallback."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def verify(output):
    resources = ROOT / '.local/desktop-resources'
    required = [resources / 'engine', resources / 'release-manifest.json']
    missing = [path.relative_to(ROOT).as_posix() for path in required if not path.exists()]
    if missing:
        return {'status': 'blocked', 'reason': 'Native fixed frozen Engine resource group is unavailable', 'missing_assets': missing,
            'eligible_for_native_pass': False, 'checks': []}
    npm = shutil.which('npm')
    if not npm:
        return {'status': 'blocked', 'reason': 'Forge packager is unavailable', 'checks': []}
    built = subprocess.run([npm, 'run', 'package', '--workspace', '@forgecode/desktop'], cwd=ROOT,
        capture_output=True, text=True, encoding='utf-8', timeout=300)
    if built.returncode:
        return {'status': 'fail', 'reason': 'Actual Forge packaging failed', 'exit_code': built.returncode, 'checks': []}
    executable = ROOT / '.local/desktop-packages' / f'ForgeCode-{sys.platform}-x64' / ('ForgeCode.exe' if sys.platform == 'win32' else 'ForgeCode')
    result = subprocess.run([str(executable), '--desktop-package-smoke-report', str(output.resolve())], cwd=output.parent,
        capture_output=True, text=True, encoding='utf-8', timeout=90)
    report = json.loads(output.read_text()) if output.is_file() else {'status': 'fail', 'reason': 'Installed app produced no inspection report', 'checks': []}
    if result.returncode:
        report.update(status='fail', exit_code=result.returncode)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.output)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))
    raise SystemExit(0 if report['status'] == 'pass' else 2 if report['status'] == 'blocked' else 1)
