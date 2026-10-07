"""Hydrate pinned private assets without changing existing content or global setup."""
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.materialize_release import materialize


def hydrate_node(root,platform):
    materialize(root,target=platform+'-x64')
    lock=json.loads((root/'release-lock.json').read_bytes())
    asset=next(a for a in lock['assets'] if a['name']=='node' and a['platform']==platform+'-x64')
    return (root/asset['path']).resolve(strict=True)


def hydrate_electron(root,node):
    """Electron 44 installs its binary explicitly; npm ci alone no longer downloads it."""
    root=Path(root)
    package=root/'node_modules/electron'
    lock=json.loads((root/'package-lock.json').read_bytes())
    expected=lock['packages']['node_modules/electron']['version']
    if json.loads((package/'package.json').read_bytes())['version']!=expected:
        raise ValueError('Electron package differs from the locked version')
    environment={key:value for key,value in os.environ.items() if not key.upper().startswith('ELECTRON') and
        not key.lower().startswith('npm_config_electron_') and
        key.lower() not in {'npm_config_platform','npm_config_arch','force_no_cache','node_options','node_path'}}
    subprocess.run([str(node),str(package/'install.js')],cwd=root,env=environment,check=True,timeout=300)
    executable='electron.exe' if sys.platform=='win32' else 'electron'
    if ((package/'dist/version').read_text().strip().removeprefix('v')!=expected or
        (package/'path.txt').read_text()!=executable or not (package/'dist'/executable).is_file()):
        raise ValueError('Installed Electron binary does not match the locked platform/version')
    return package/'dist'/executable


def main():
    node=hydrate_node(ROOT,sys.platform)
    hydrate_electron(ROOT,node)
    for script in ('scripts/check_contracts.py','scripts/build_bridge.py','scripts/build_desktop.py'):
        subprocess.run([sys.executable,str(ROOT/script)],cwd=ROOT,check=True)
    print(json.dumps({'status':'pass','scope':'locked private build assets; no system setup'}))
if __name__=='__main__':main()
