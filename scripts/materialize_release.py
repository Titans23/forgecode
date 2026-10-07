"""Restore pinned private runtimes without resolving versions or changing lock files."""
import argparse
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import urllib.request
from uuid import uuid4
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from forge.release.runtime import verify_asset
from forge.release.processes import foreign_environment


def locked_asset(root,asset):
    # Source locks bind the complete content hash; installed inventories also
    # bind an explicit size. Adapt the former to the existing ownership/link guard.
    path=(root/asset['path']).resolve(strict=True)
    if not path.is_relative_to(root):raise ValueError('Locked asset escapes repository ownership')
    return verify_asset(root,{**asset,'size_bytes':asset.get('size_bytes',path.stat().st_size)})


def runtime_member(archive,version,target,archive_hash,binary_hash):
    if sha256(archive).hexdigest()!=archive_hash:raise ValueError('Pinned Node archive hash differs')
    directory='node-v'+version+('-win-x64' if target=='win32-x64' else '-linux-x64')
    member=directory+('/node.exe' if target=='win32-x64' else '/bin/node')
    if target=='win32-x64':
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            matches=[info for info in bundle.infolist() if info.filename==member]
            if len(matches)!=1 or matches[0].is_dir() or stat.S_IFMT(matches[0].external_attr>>16) not in (0,stat.S_IFREG):
                raise ValueError('Pinned runtime must be a unique regular member')
            content=bundle.read(matches[0])
    else:
        with tarfile.open(fileobj=io.BytesIO(archive),mode='r:xz') as bundle:
            info=bundle.getmember(member)
            if not info.isfile():raise ValueError('Pinned runtime must be a regular member')
            content=bundle.extractfile(info).read()
    if sha256(content).hexdigest()!=binary_hash:raise ValueError('Pinned Node member hash differs')
    return content


def materialize(root=ROOT,*,target=None):
    root=root.resolve();lock_path=root/'release-lock.json';original=lock_path.read_bytes();lock=json.loads(original)
    target=target or sys.platform+'-x64';version=lock['node']['version']
    if target not in ('win32-x64','linux-x64') or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version):raise ValueError('Unsupported native runtime target/version')
    assets=[asset for asset in lock['assets'] if asset['platform'] in (target,'all')]
    node=next(asset for asset in assets if asset['name']=='node')
    suffix='win-x64' if target=='win32-x64' else 'linux-x64';name='node.exe' if target=='win32-x64' else 'node'
    url='https://nodejs.org/dist/v'+version+'/node-v'+version+'-'+suffix+('.zip' if target=='win32-x64' else '.tar.xz')
    if node['source']!=url:raise ValueError('Runtime source is not the pinned official Node archive')
    if target!=sys.platform+'-x64':raise ValueError('Private runtime restoration requires the actual target OS')
    expected='.local/release-runtime/node-'+version+'-'+suffix+'/'+name
    if node['path']!=expected:raise ValueError('Pinned runtime destination differs from owned layout')
    path=root/expected;downloaded=False
    if path.exists() or path.is_symlink():locked_asset(root,node)
    else:
        parent=path.parent
        if not parent.resolve().is_relative_to(root/'.local/release-runtime'):raise ValueError('Runtime destination escapes private assets')
        with urllib.request.urlopen(url,timeout=60) as response:archive=response.read(134217729)
        if len(archive)>134217728:raise ValueError('Runtime download exceeds fixed quota')
        content=runtime_member(archive,version,target,node['archive_sha256'],node['sha256'])
        if not parent.resolve().is_relative_to(root/'.local/release-runtime'):raise ValueError('Runtime directory changed during download')
        parent.mkdir(parents=True,exist_ok=True);temporary=parent/('.node-'+str(uuid4())+'.tmp')
        try:
            with temporary.open('xb') as output:output.write(content);output.flush();os.fsync(output.fileno())
            if path.exists() or path.is_symlink():raise ValueError('Runtime destination changed during download')
            os.link(temporary,path)  # Atomic no-replace publication; concurrent user assets are never overwritten.
            if target=='linux-x64':path.chmod(0o755)
        finally:temporary.unlink(missing_ok=True)
        downloaded=True
    for asset in assets:locked_asset(root,asset)
    actual=subprocess.check_output([str(path),'--version'],env=foreign_environment(),text=True,timeout=15).strip()
    if actual!='v'+version:raise ValueError('Private Node runtime returned another version')
    if lock_path.read_bytes()!=original:raise ValueError('Release lock changed during materialization')
    return {'status':'pass','scope':'actual pinned private runtime/assets; no setup or native capability promotion',
        'eligible_for_native_pass':False,'target':target,'node':actual,'downloaded':downloaded,
        'lock_sha256':sha256(original).hexdigest(),'public_model_calls':0,
        'checks':[{'id':'locked-native-assets-hash','status':'pass'},{'id':'actual-private-node-version','status':'pass'},
            {'id':'release-lock-unchanged','status':'pass'}]}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output=args.output.resolve()
    if not args.output.is_relative_to(ROOT/'.local'):
        print(json.dumps({'status':'invalid_configuration','reason':'Runtime evidence output requires owned .local storage'}));return 3
    try:result=materialize()
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:result={'status':'fail','reason':str(error),'checks':[]}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result));return 0 if result['status']=='pass' else 1
if __name__=='__main__':raise SystemExit(main())
