"""Run the actual Electron development window and offline Harness, never a native acceptance substitute."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forge.testing.demo import seed_demo


def smoke(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    directory = output / 'fixture with spaces'
    directory.mkdir()
    params, fixture = seed_demo(directory)
    scripted = json.loads(fixture.read_text(encoding='utf-8'))
    scripted['responses'][0]['delay_seconds'] = 1
    fixture.write_text(json.dumps(scripted, indent=2), encoding='utf-8')
    config = output / 'config.json'
    config.write_text(json.dumps({'origin': 'scripted', 'directory': str(directory), 'fixture': str(fixture),
        'output': str(output), 'params': params}), encoding='utf-8')
    executable = ROOT / 'node_modules/electron/dist' / ('electron.exe' if os.name == 'nt' else 'electron')
    report_path = output / 'desktop-report.json'
    if not executable.is_file() or sys.platform == 'linux' and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        report = {'status': 'blocked', 'reason': 'Actual Electron runtime or graphical display is unavailable', 'checks': []}
    else:
        environment = dict(os.environ)
        for name in ('ELECTRON_RUN_AS_NODE', 'NODE_OPTIONS', 'NODE_PATH'):
            environment.pop(name, None)
        with (output / 'stdout.log').open('wb') as stdout, (output / 'stderr.log').open('wb') as stderr:
            result = subprocess.run([str(executable), str(ROOT / 'apps/desktop'), '--desktop-smoke-config', str(config)],
                cwd=output, env=environment, stdout=stdout, stderr=stderr, timeout=90)
        report = json.loads(report_path.read_text()) if report_path.is_file() else {
            'status': 'fail', 'reason': 'Actual Electron did not produce its report', 'checks': []}
        if result.returncode:
            report.update(status='fail', exit_code=result.returncode)
    report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Report path in a new evidence directory')
    args = parser.parse_args()
    report = smoke(args.output.parent / 'desktop-run')
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))
    return 0 if report['status'] == 'pass' else 2 if report['status'] == 'blocked' else 1


if __name__ == '__main__':
    raise SystemExit(main())
