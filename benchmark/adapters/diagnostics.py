"""Read-only local runner readiness; never starts Docker or installs dependencies."""
from importlib.metadata import PackageNotFoundError, version
import json
import shutil
import subprocess
import sys


def doctor():
    from benchmark.harbor.run_dataset import harbor_executable
    result={'host_platform':sys.platform,'execution_label':'official-environment',
        'required_environment':'Linux Docker task environment; Windows client must not label it windows-native',
        'harbor':None,'docker':{'available':False,'server_os':None},'issues':[]}
    try:
        installed=version('harbor')
        result['harbor']={'version':installed,'executable':harbor_executable()}
        if installed!='0.18.0':
            result['issues'].append('Validated adapter requires Harbor 0.18.0')
    except (PackageNotFoundError,RuntimeError):
        result['issues'].append('Harbor executable or pinned package unavailable')
    docker=shutil.which('docker')
    if docker:
        try:
            from forge.release.processes import external_argv, external_options
            process=subprocess.run(external_argv([docker,'version','--format','{{json .}}']),capture_output=True,
                text=True,encoding='utf-8',errors='replace',timeout=8,check=False,**external_options())
            value=json.loads(process.stdout) if process.stdout.strip() else {}
            server=value.get('Server') or {}
            result['docker']={'available':process.returncode==0 and server.get('Os')=='linux',
                'server_os':server.get('Os'),'exit_code':process.returncode,
                'client_version':(value.get('Client') or {}).get('Version')}
            if not result['docker']['available']:
                result['issues'].append('Linux Docker daemon unavailable; no environment was started')
        except (OSError,subprocess.TimeoutExpired,ValueError):
            result['issues'].append('Docker readiness probe failed or timed out')
    else:
        result['issues'].append('Docker executable unavailable')
    return result
