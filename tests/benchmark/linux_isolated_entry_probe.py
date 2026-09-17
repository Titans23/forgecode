"""Offline Linux installed-package import/supervision probe in a disposable container."""
import json
from pathlib import Path
import site
import subprocess
import sys
import tempfile

# A site-packages .pth models an installed editable package, without downloading
# dependencies or allowing task-controlled PYTHONPATH into the isolated child.
Path(site.getsitepackages()[0], 'forge-entry-probe.pth').write_text('/src\n')
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    for name in ('benchmark.py', 'forge.py'):
        (root / name).write_text('raise RuntimeError("task module imported")\n')
    code = ('import importlib.util; '
            'assert importlib.util.find_spec("benchmark.harbor.run_forge"); '
            'assert importlib.util.find_spec("forge.runtime.runner"); '
            'print("isolated child ready")')
    result = subprocess.run([sys.executable, '-I', '-m',
        'benchmark.harbor.process_supervisor', '--preserve-on-success', '--',
        sys.executable, '-I', '-c', code], cwd=root, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'isolated child ready' in result.stdout
    print(json.dumps({'linux_supervisor_isolated_child': 'passed', 'exit_code': result.returncode}))
