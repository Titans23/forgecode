"""Hydrate pinned private assets without changing existing content or global setup."""
import json
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


def main():
    hydrate_node(ROOT,sys.platform)
    for script in ('scripts/check_contracts.py','scripts/build_bridge.py','scripts/build_desktop.py'):
        subprocess.run([sys.executable,str(ROOT/script)],cwd=ROOT,check=True)
    print(json.dumps({'status':'pass','scope':'locked private build assets; no system setup'}))
if __name__=='__main__':main()
