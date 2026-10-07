"""Hydrate only pinned local build assets; no global setup or resolution of latest."""
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def hydrate_node(root, platform):
    lock=json.loads((root/'release-lock.json').read_bytes())
    asset=next(a for a in lock['assets'] if a['name']=='node' and a['platform']==platform+'-x64')
    path=(root/asset['path']).absolute()
    if not path.is_relative_to(root/'.local') or path.resolve()!=path:raise ValueError('Private runtime target is not owned')
    if not path.exists() or sha256(path.read_bytes()).hexdigest()!=asset['sha256']:
        url=asset['source'];version=lock['node']['version']
        target='win-x64' if platform=='win32' else 'linux-x64'
        suffix='zip' if platform=='win32' else 'tar.xz'
        expected=f'https://nodejs.org/dist/v{version}/node-v{version}-{target}.{suffix}'
        if url!=expected:raise ValueError('Runtime source is not the pinned official Node archive')
        with urllib.request.urlopen(url,timeout=120) as response:archive=response.read()
        if sha256(archive).hexdigest()!=asset['archive_sha256']:raise ValueError('Locked archive checksum differs')
        member=f'node-v{version}-{target}/'+('node.exe' if platform=='win32' else 'bin/node')
        if platform=='win32':
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:binary=bundle.read(member)
        else:
            with tarfile.open(fileobj=io.BytesIO(archive),mode='r:xz') as bundle:
                info=bundle.getmember(member)
                if not info.isfile():raise ValueError('Node archive member is not a regular file')
                binary=bundle.extractfile(info).read()
        if sha256(binary).hexdigest()!=asset['sha256']:raise ValueError('Locked Node executable checksum differs')
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_bytes(binary);os.replace(temporary,path)
        if platform=='linux':path.chmod(0o755)
    actual=subprocess.check_output([str(path),'--version'],text=True).strip()
    if actual!='v'+lock['node']['version']:raise ValueError('Private Node reports another version')
    return path


def main():
    hydrate_node(ROOT,sys.platform)
    for script in ('scripts/check_contracts.py','scripts/build_bridge.py','scripts/build_desktop.py'):
        subprocess.run([sys.executable,str(ROOT/script)],cwd=ROOT,check=True)
    print(json.dumps({'status':'pass','scope':'locked private build assets; no system setup'}))
if __name__=='__main__':main()
