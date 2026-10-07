"""Make native developer-preview installers; cross-platform absence is blocked."""
import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from forge.release.runtime import digest,verify_manifest
ROOT=Path(__file__).resolve().parents[1]

def make(output,target):
    if target!=sys.platform+'-x64':
        return {'status':'blocked','reason':'Native installer target OS is unavailable','checks':[],'eligible_for_native_pass':False}
    if target=='linux-x64':
        info=platform.freedesktop_os_release()
        if info.get('ID')!='ubuntu' or info.get('VERSION_ID')!='22.04':
            return {'status':'blocked','reason':'deb requires native Ubuntu 22.04 build baseline','checks':[],'eligible_for_native_pass':False}
        if not shutil.which('fakeroot') or not shutil.which('dpkg'):
            return {'status':'blocked','reason':'Native fakeroot/dpkg are unavailable','checks':[],'eligible_for_native_pass':False}
    resources=ROOT/'.local/desktop-resources'
    m=verify_manifest(resources)
    npm=shutil.which('npm')
    if not npm:return {'status':'blocked','reason':'Pinned build npm is unavailable','checks':[],'eligible_for_native_pass':False}
    command=[npm,'exec','--workspace','@forgecode/desktop','--','electron-forge','make']
    result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=600)
    (output.parent/'installer-build.stdout.log').write_text(result.stdout,encoding='utf-8')
    (output.parent/'installer-build.stderr.log').write_text(result.stderr,encoding='utf-8')
    if result.returncode:return {'status':'fail','reason':'Actual Forge maker failed','exit_code':result.returncode,'checks':[]}
    artifacts=[p for p in (ROOT/'.local/desktop-packages/make').rglob('*') if p.is_file() and p.suffix.lower() in ('.exe','.nupkg','.deb') or
        p.is_file() and p.name=='RELEASES']
    if not artifacts:return {'status':'fail','reason':'Maker produced no native distributable','checks':[]}
    return {'status':'pass','scope':'native developer-preview installer build','eligible_for_native_pass':False,
        'build_id':m['build_id'],'platform':target,'host':platform.platform(),'signature':m['signature'],
        'channel':m['channel'],'command':command,'checks':[{'name':'actual-forge-maker','status':'pass'}],
        'artifacts':[{'path':p.relative_to(ROOT).as_posix(),'sha256':digest(p),'size_bytes':p.stat().st_size,
            'signature_status':'unsigned'} for p in sorted(artifacts)],'production_acceptance':'blocked',
        'native_install_acceptance':'blocked','project_license_status':m['project_license_status']}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--target',choices=('win32-x64','linux-x64'),default=sys.platform+'-x64');a=p.parse_args()
    a.output=a.output.resolve();a.output.parent.mkdir(parents=True,exist_ok=True)
    try:r=make(a.output,a.target)
    except Exception as error:r={'status':'fail','reason':str(error),'checks':[],'eligible_for_native_pass':False}
    a.output.write_text(json.dumps(r,indent=2)+'\n',encoding='utf-8');print(json.dumps(r))
    return {'pass':0,'blocked':2,'fail':1}[r['status']]
if __name__=='__main__':raise SystemExit(main())
