"""Inspect and run the actual fuse-hardened installed artifact, independently of development E2E."""
import argparse
from hashlib import sha256
import json
import mmap
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
SENTINEL=b'dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX'
NAMES=('RunAsNode','EnableCookieEncryption','EnableNodeOptionsEnvironmentVariable',
       'EnableNodeCliInspectArguments','EnableEmbeddedAsarIntegrityValidation','OnlyLoadAppFromAsar',
       'LoadBrowserProcessSpecificV8Snapshot','GrantFileProtocolExtraPrivileges','WasmTrapHandlers')


def read_fuses(path):
    with Path(path).open('rb') as file:
        if not file.seek(0,2):raise ValueError('Electron fuse wire is missing')
        with mmap.mmap(file.fileno(),0,access=mmap.ACCESS_READ) as binary:
            position=binary.find(SENTINEL)
            if position<0 or binary.find(SENTINEL,position+1)>=0:raise ValueError('Electron fuse sentinel must be unique for x64')
            start=position+len(SENTINEL)
            header=binary[start:start+2]
            if len(header)!=2 or header[0]!=1 or header[1]!=len(NAMES):raise ValueError('Unsupported or truncated Electron fuse wire')
            values=binary[start+2:start+2+header[1]]
            if len(values)!=len(NAMES) or any(v not in (48,49,114) for v in values):raise ValueError('Invalid Electron fuse state')
            return dict(zip(NAMES,values))


def hardened_checks(wire,platform):
    expected={name:48 for name in ('RunAsNode','EnableNodeOptionsEnvironmentVariable',
        'EnableNodeCliInspectArguments','GrantFileProtocolExtraPrivileges')}
    expected.update(EnableCookieEncryption=49,OnlyLoadAppFromAsar=49)
    if platform=='win32':expected['EnableEmbeddedAsarIntegrityValidation']=49
    return [{'id':'fuse-'+name,'status':'pass' if wire[name]==value else 'fail','actual':wire[name],'expected':value}
            for name,value in expected.items()]


def verify(output):
    output=Path(output).resolve()
    if not output.is_relative_to(ROOT/'.local'):raise ValueError('Hardened smoke reports require owned local evidence storage')
    output.parent.mkdir(parents=True,exist_ok=True)
    executable=ROOT/'.local/desktop-packages'/('ForgeCode-'+sys.platform+'-x64')/('ForgeCode.exe' if sys.platform=='win32' else 'forgecode')
    if not executable.is_file():return {'status':'blocked','reason':'Actual installed desktop artifact is unavailable','checks':[],'eligible_for_native_pass':False}
    from forge.release.runtime import digest,verify_asset
    wire=read_fuses(executable);checks=hardened_checks(wire,sys.platform)
    report={'status':'fail','scope':'actual hardened unsigned developer artifact; not supported-platform acceptance',
        'eligible_for_native_pass':False,'executable':executable.relative_to(ROOT).as_posix(),
        'sha256':digest(executable),'wire':wire,'checks':checks,'public_model_calls':0}
    if any(c['status']!='pass' for c in checks):return report
    marker=output.parent/'node-options-executed.txt';marker.unlink(missing_ok=True)
    hook=output.parent/'node-options-probe.cjs'
    hook.write_text("require('node:fs').writeFileSync("+json.dumps(str(marker))+",'unexpected injection');\n",encoding='utf-8')
    environment=dict(os.environ,ELECTRON_RUN_AS_NODE='1',NODE_OPTIONS='--require='+json.dumps(str(hook)))
    resources=ROOT/'.local/desktop-resources'
    manifest=json.loads((resources/'release-manifest.json').read_bytes())
    node=verify_asset(resources,manifest['node'])
    control=subprocess.run([str(node),'-e','process.exit(0)'],env=environment,capture_output=True,timeout=30)
    checks.append({'id':'node-options-positive-control','status':'pass' if control.returncode==0 and marker.is_file() else 'fail'})
    marker.unlink(missing_ok=True)
    inspection=output.parent/'hardened-installed-inspection.json';inspection.unlink(missing_ok=True)
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}
    child=subprocess.run([str(executable),'--desktop-package-smoke-report',str(inspection)],cwd=output.parent,
        env=environment,capture_output=True,text=True,encoding='utf-8',timeout=90,**options)
    actual=json.loads(inspection.read_bytes()) if inspection.is_file() else None
    checks.append({'id':'installed-app-survives-node-injection','status':'pass' if child.returncode==0 and actual and actual.get('status')=='pass'
        and actual.get('checks') and all(c.get('status')=='pass' for c in actual['checks']) else 'fail','exit_code':child.returncode,
        'report_sha256':sha256(inspection.read_bytes()).hexdigest() if inspection.is_file() else None,'report':actual})
    checks.append({'id':'node-options-hook-never-runs','status':'pass' if not marker.exists() else 'fail'})
    for flag in ('--inspect=0','--remote-debugging-port=0','--remote-debugging-pipe'):
        rejected=output.parent/('forbidden-'+flag[2:].replace('=','-')+'.json');rejected.unlink(missing_ok=True)
        result=subprocess.run([str(executable),flag,'--desktop-package-smoke-report',str(rejected)],cwd=output.parent,
            capture_output=True,text=True,encoding='utf-8',timeout=30,**options)
        checks.append({'id':'installed-refuses-'+flag,'status':'pass' if result.returncode==2 and
            'FORGE_INSTALLED_DEBUG_DENIED' in result.stderr and not rejected.exists() else 'fail','exit_code':result.returncode,
            'stderr_sha256':sha256(result.stderr.encode()).hexdigest()})
    report['status']='pass' if all(c['status']=='pass' for c in checks) else 'fail'
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try:report=verify(args.output)
    except (OSError,ValueError,subprocess.TimeoutExpired) as error:report={'status':'fail','reason':str(error),'checks':[],'eligible_for_native_pass':False}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False));return 0 if report['status']=='pass' else 2 if report['status']=='blocked' else 1
if __name__=='__main__':raise SystemExit(main())
