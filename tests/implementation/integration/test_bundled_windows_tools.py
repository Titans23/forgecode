"""Run the shipped tools with no developer PATH; this is not an isolation test."""
import json
from datetime import datetime, timedelta, timezone
import subprocess
import sys

from forge.sandbox.launcher import bridge_environment, verify_runtime


def test_private_windows_tools_launch_without_global_installations(tmp_path):
    runtime = verify_runtime()
    if sys.platform != 'win32':
        # Linux uses deb-owned system tools; this portable branch does not claim
        # to execute Windows binaries or replace Windows native acceptance.
        assert runtime.tools == {}
        return
    environment = bridge_environment(tmp_path)
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=str(tmp_path / 'empty.gitconfig'))
    (tmp_path / 'empty.gitconfig').write_text('', encoding='utf-8')

    def run(argv, *, expected=0):
        result = subprocess.run([str(arg) for arg in argv], cwd=tmp_path, env=environment,
                                capture_output=True, encoding='utf-8', timeout=60)
        assert result.returncode == expected, result.stdout + result.stderr
        return result.stdout.strip()

    lock = json.loads((runtime.root / 'release-lock.json').read_text(encoding='utf-8'))
    versions = {bundle['name']: bundle['version'] for bundle in lock['tool_bundles']}
    assert run([runtime.tools['powershell'], '--version']) == 'PowerShell ' + versions['powershell']
    assert run([runtime.tools['git'], '--version']).startswith('git version ')
    assert run([runtime.tools['ripgrep'], '--version']).startswith('ripgrep ' + versions['ripgrep'])
    run([runtime.tools['git'], 'init', '--quiet', '--initial-branch=main'])
    (tmp_path / '空 格.txt').write_text('private-tool-needle\n', encoding='utf-8')
    assert '空 格.txt' in run([runtime.tools['git'], '-c', 'core.quotepath=false', 'status', '--porcelain'])
    assert run([runtime.tools['ripgrep'], '--fixed-strings', 'private-tool-needle', '空 格.txt']) == 'private-tool-needle'

    payload = {
        'command': {'mode': 'shell_script', 'shell': 'pwsh', 'cwd': str(tmp_path), 'environment': {},
                    'deadline_utc': (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace('+00:00', 'Z'),
                    'output_limit_bytes': 65536, 'script':
                    "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
                    "Write-Output '空 格'; git --version; rg --version; exit 23"},
        'shells': {'pwsh': str(runtime.tools['powershell'])},
        'tools': {'git': str(runtime.tools['git']), 'rg': str(runtime.tools['ripgrep'])},
    }
    result = subprocess.run([str(runtime.node), str(runtime.root / 'sandbox_bridge/dist/dispatcher.js')],
                            input=json.dumps(payload), cwd=tmp_path, env=environment,
                            capture_output=True, encoding='utf-8', timeout=60)
    assert result.returncode == 23, result.stdout + result.stderr
    assert '空 格' in result.stdout and 'git version ' in result.stdout and 'ripgrep ' in result.stdout
