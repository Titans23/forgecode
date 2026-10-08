"""The source launcher diagnoses missing prerequisites without building or opening windows."""
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def test_launcher_help_and_invalid_argument_do_not_start_desktop(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    launcher = scripts / 'start_desktop.mjs'
    shutil.copyfile(ROOT / 'scripts/start_desktop.mjs', launcher)
    result = subprocess.run(['node', str(launcher), '--help'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and 'desktop-quickstart.md' in result.stdout
    result = subprocess.run(['node', str(launcher), '--skip-readiness'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and 'Use --check, --help' in result.stderr
    assert list(tmp_path.iterdir()) == [scripts]


def test_launcher_check_reports_each_missing_prerequisite_without_installing(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    launcher = scripts / 'start_desktop.mjs'
    shutil.copyfile(ROOT / 'scripts/start_desktop.mjs', launcher)
    requirements = [
        ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python', 'uv sync --locked'),
        ('node_modules/typescript/bin/tsc', 'npm ci --ignore-scripts'),
        ('node_modules/vite/bin/vite.js', 'npm ci --ignore-scripts'),
        ('node_modules/electron/dist/' + ('electron.exe' if os.name == 'nt' else 'electron'), 'electron/install.js'),
    ]
    for relative, remedy in requirements:
        result = subprocess.run(['node', str(launcher), '--check'], capture_output=True, text=True, timeout=10)
        assert result.returncode == 1 and remedy in result.stderr
        target = tmp_path / relative
        assert not target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('not executable: the check must not launch this file', encoding='utf-8')
    result = subprocess.run(['node', str(launcher), '--check'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and 'prerequisites are present' in result.stdout
    assert not (tmp_path / 'apps').exists()
