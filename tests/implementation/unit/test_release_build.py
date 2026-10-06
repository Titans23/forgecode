from pathlib import Path
import importlib.util
import shutil
import subprocess


def test_real_node_release_loader_behavior():
    root = Path(__file__).resolve().parents[3]
    node = shutil.which('node')
    assert node, 'F01 unit suite requires Node; use doctor to report unavailable prerequisites as blocked.'
    result = subprocess.run([node, '--test', 'tests/implementation/node/release-lock.test.mjs'],
                            cwd=root, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    assert '# pass 4' in result.stdout
    assert '# skipped 0' in result.stdout


def test_build_rejects_unpinned_python_before_packaging(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location('build_smoke', root / 'scripts' / 'build_smoke.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.platform, 'python_version', lambda: '0.0.0')
    result = module.verify()
    assert result['status'] == 'blocked'
    assert 'Build requires Python' in result['reason']
    assert result['checks'] == []
