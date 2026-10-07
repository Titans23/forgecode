import subprocess
from pathlib import Path


def test_actual_fixed_asset_security_checks():
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(['node', '--test', '--test-reporter=tap', 'tests/implementation/node/desktop-assets.test.mjs'], cwd=root,
        capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '# pass 3' in result.stdout and '# skipped 0' in result.stdout
