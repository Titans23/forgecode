"""Actual frozen Engine, helper, Bridge and project executables; no model API."""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from forge.engine.methods import manifest_hash
from forge.engine.persistence import Store,new_id
from forge.release.runtime import verify_manifest,verify_asset
from forge.testing.demo import seed_demo
from forge.testing.rpc_client import RpcClient

ROOT=Path(__file__).resolve().parents[1]
async def run_engine(executable,manifest,directory):
    demo=directory/'frozen-turn';demo.mkdir()
    params,fixture=seed_demo(demo)
    environment={k:os.environ[k] for k in ('SystemRoot','WINDIR','TEMP','TMP','HOME','USERPROFILE','HOMEDRIVE','HOMEPATH') if k in os.environ}
    environment['PATH']=str(Path(environment.get('SystemRoot','C:/Windows'))/'System32') if sys.platform=='win32' else '/usr/bin:/bin'
    project_git=verify_asset(ROOT/'.local/desktop-resources',manifest['tools']['git']['entry']) if sys.platform=='win32' else shutil.which('git')
    if not project_git:raise ValueError('Project Git is required for patch verification')
    environment['PYTHONDONTWRITEBYTECODE']='1'
    environment['PATH']+=os.pathsep+str(Path(project_git).resolve().parent)
    with (directory/'frozen-engine.log').open('wb') as diagnostics:
        process=await asyncio.create_subprocess_exec(str(executable),'--data-dir',str(demo/'data'),
            '--profile','test','--execution-mode','local-trusted','--scripted-fixture',str(fixture),cwd=directory,env=environment,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=diagnostics,limit=1048577)
        client=RpcClient(process)
        try:
            hello=await client.call('system.initialize',{'protocol':{'major':1,'minor':0},
                'client_build':'actual-frozen-smoke','expected_manifest_hash':manifest_hash(),'profile':'test'})
            if hello['engine_build']!=manifest['build_id']:raise ValueError('Frozen build identity differs')
            created=await client.call('session.create',params)
            turn={k:v for k,v in params.items() if k!='workspace_id'}
            turn.update(client_action_id=new_id('act'),session_id=created['session_id'],
                input=[{'type':'text','text':'Repair the calculator and verify the real tests.'}])
            await client.call('session.start_turn',turn)
            async with asyncio.timeout(120):
                while True:
                    session=await client.call('session.get',{'session_id':created['session_id']})
                    if session['turns'][0]['state']=='finished':break
                    await asyncio.sleep(.05)
            if session['turns'][0]['outcome']!='completed':raise ValueError('Actual frozen Harness turn did not complete')
            await client.call('system.shutdown',{'client_action_id':new_id('act'),'mode':'drain'})
            if await client.drain()!=0:raise ValueError('Frozen Engine failed to drain')
            with Store(demo/'data') as store:
                count=store.connection.execute('SELECT count(*) FROM turns').fetchone()[0]
                requests=store.connection.execute('SELECT count(*) FROM model_requests').fetchone()[0]
            if count!=1 or requests!=5:raise ValueError('Frozen turn/model ledger differs')
            return {'status':'pass','name':'frozen-stdio-harness-file-worker-real-repair-verification',
                'engine_build':hello['engine_build'],'turns':count,'model_requests':requests,'model_origin':'scripted'}
        finally:
            if process.returncode is None:process.kill()
            await process.wait()

async def clean_start(executable,manifest,directory):
    environment={k:os.environ[k] for k in ('SystemRoot','WINDIR','TEMP','TMP','HOME','USERPROFILE','HOMEDRIVE','HOMEPATH') if k in os.environ}
    environment['PATH']=str(Path(environment.get('SystemRoot','C:/Windows'))/'System32') if sys.platform=='win32' else '/nonexistent-project-toolchains'
    with (directory/'clean-start.log').open('wb') as diagnostics:
        process=await asyncio.create_subprocess_exec(str(executable),'--data-dir',str(directory/'clean-data'),
            '--profile','desktop',cwd=directory,env=environment,stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=diagnostics,limit=1048577)
        client=RpcClient(process)
        try:
            hello=await client.call('system.initialize',{'protocol':{'major':1,'minor':0},'client_build':'clean-toolchain-path',
                'expected_manifest_hash':manifest_hash(),'profile':'desktop'})
            if hello['engine_build']!=manifest['build_id'] or 'scripted-model' in hello['capabilities']['features']:raise ValueError('Clean frozen startup mismatch')
            await client.call('system.shutdown',{'client_action_id':new_id('act'),'mode':'drain'})
            if await client.drain()!=0:raise ValueError('Clean frozen shutdown failed')
            return {'name':'actual-frozen-start-no-global-python-node','status':'pass','readiness':hello['readiness']['status'],
                'model_calls':0,'project_toolchains_executed':False}
        finally:
            if process.returncode is None:process.kill()
            await process.wait()

def run_foreign(executable,command,directory,*,name,environment=None):
    env=dict(os.environ) if environment is None else environment
    # The worker must remove bootloader/private loader state before launching.
    env.update(PYTHONHOME='invalid-private-loader',NODE_OPTIONS='--this-private-option-must-not-reach-project')
    role='external-worker' if sys.platform=='win32' else 'process-worker'
    start=time.monotonic()
    result=subprocess.run([str(executable),role,json.dumps(command),'0'],cwd=directory,env=env,
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=40)
    if result.returncode:raise ValueError(name+' foreign launch failed: '+result.stderr[-500:])
    return {'name':name,'status':'pass','command':command,'exit_code':result.returncode,
        'output':result.stdout.strip()[:1024],'seconds':time.monotonic()-start}

def verify(output):
    resources=ROOT/'.local/desktop-resources'
    if not (resources/'release-manifest.json').is_file():
        return {'status':'blocked','reason':'Actual platform Engine resources are not built','checks':[],'eligible_for_native_pass':False}
    manifest=verify_manifest(resources)
    executable=verify_asset(resources,manifest['engine'])
    directory=output.parent/'frozen-smoke';directory.mkdir(parents=True,exist_ok=False)
    checks=[asyncio.run(clean_start(executable,manifest,directory)),asyncio.run(run_engine(executable,manifest,directory))]
    if sys.platform=='win32':checks.extend(verify_bundled_tools(resources,manifest,executable,directory))
    diagnosis=subprocess.run([str(executable),'doctor','--json'],cwd=directory,capture_output=True,text=True,encoding='utf-8',timeout=60)
    diagnosed=json.loads(diagnosis.stdout)
    if diagnosis.returncode!=2 or diagnosed.get('read_only') is not True or diagnosed['runtime']['status']!='pass':
        raise ValueError('Installed read-only doctor did not verify its fixed runtime')
    checks.append({'name':'actual-frozen-readonly-doctor','status':'pass','diagnosis_status':diagnosed['status'],
        'runtime_status':diagnosed['runtime']['status'],'project_toolchains':diagnosed['project_toolchains'],
        'native_setup_performed':False})
    cli=subprocess.run([str(executable),'cli','--help'],cwd=directory,capture_output=True,text=True,encoding='utf-8',timeout=60)
    if cli.returncode!=0 or 'Usage:' not in cli.stdout:raise ValueError('Frozen original CLI entry is unavailable')
    checks.append({'name':'actual-frozen-original-cli','status':'pass','exit_code':cli.returncode})
    if sys.platform=='win32':checks.append(verify_external_cleanup(executable,directory))
    python=Path(sys.executable).resolve()
    script="import json,sys,ctypes,os;"+("k=ctypes.WinDLL('kernel32');b=ctypes.create_unicode_buffer(32768);n=k.GetDllDirectoryW(len(b),b);" if sys.platform=='win32' else "n=0;")+ "print(json.dumps({'python':sys.version.split()[0],'dll_directory_length':n,'pythonhome':os.environ.get('PYTHONHOME'),'node_options':os.environ.get('NODE_OPTIONS'),'loader':os.environ.get('LD_LIBRARY_PATH')}))"
    probe=run_foreign(executable,[str(python),'-c',script],directory,name='actual-project-python-loader')
    value=json.loads(probe['output'])
    if value['dll_directory_length'] or value['pythonhome'] or value['node_options']:raise ValueError('Private loader state leaked to project Python')
    checks.append(probe)
    git=shutil.which('git');node=shutil.which('node')
    if not git or not node:raise ValueError('Actual project Git/Node toolchains are required for this smoke')
    checks.append(run_foreign(executable,[git,'--version'],directory,name='actual-project-git'))
    checks.append(run_foreign(executable,[node,'-e',"console.log(JSON.stringify({node:process.version,nodeOptions:process.env.NODE_OPTIONS??null}))"],
        directory,name='actual-project-node-loader'))
    if json.loads(checks[-1]['output'])['nodeOptions'] is not None:raise ValueError('Private Node loader option leaked')
    bridge=verify_asset(resources,manifest['bridge']);private_node=verify_asset(resources,manifest['node'])
    result=subprocess.run([str(private_node),str(bridge),'--setup-action','diagnose'],cwd=directory,
        capture_output=True,text=True,encoding='utf-8',timeout=40)
    if sys.platform=='win32':
        reply=json.loads(result.stdout)
        if reply.get('status') not in ('pass','blocked') or result.returncode not in (0,2):raise ValueError('Actual installed Bridge diagnosis failed')
        checks.append({'name':'actual-installed-bridge-locked-node','status':'pass','diagnosis_status':reply['status'],'native_setup_performed':False})
    return {'status':'pass','scope':'Windows 10 development frozen components' if sys.platform=='win32' else 'native build development components',
        'eligible_for_native_pass':False,'model_origin':'scripted','checks':checks,'manifest_sha256':sha256((resources/'release-manifest.json').read_bytes()).hexdigest(),
        'build_id':manifest['build_id'],'host':manifest['build_host']}

def verify_bundled_tools(resources,manifest,executable,directory):
    """Real installed binaries under a minimal PATH; does not assert isolation."""
    from forge.sandbox.launcher import bridge_environment
    fixture=directory/'bundled tools 中文';fixture.mkdir()
    environment=bridge_environment(fixture)
    environment.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=str(fixture/'empty.gitconfig'))
    (fixture/'empty.gitconfig').write_text('',encoding='utf-8')
    (fixture/'空 格.txt').write_text('installed-tool-needle\n',encoding='utf-8')
    tools={name:verify_asset(resources,manifest['tools'][name]['entry']) for name in ('powershell','git','ripgrep')}
    checks=[run_foreign(executable,[str(path),'--version'],fixture,name='actual-bundled-'+name,environment=dict(environment))
            for name,path in tools.items()]
    dispatcher=verify_asset(resources,next(a for a in manifest['bridge_dependencies'] if a['path'].endswith('/sandbox_bridge/dist/dispatcher.js')))
    payload={'command':{'mode':'shell_script','shell':'pwsh','cwd':str(fixture),'environment':{},
        'deadline_utc':(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat().replace('+00:00','Z'),
        'output_limit_bytes':65536,'script':"[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
            "git init --quiet --initial-branch=main; if ($LASTEXITCODE -ne 0) { exit 91 }; "
            "git -c core.quotepath=false status --porcelain; if ($LASTEXITCODE -ne 0) { exit 92 }; "
            "rg --fixed-strings installed-tool-needle '空 格.txt'; if ($LASTEXITCODE -ne 0) { exit 93 }; exit 23"},
        'shells':{'pwsh':str(tools['powershell'])},'tools':{'git':str(tools['git']),'rg':str(tools['ripgrep'])}}
    result=subprocess.run([str(verify_asset(resources,manifest['node'])),str(dispatcher)],input=json.dumps(payload),
        cwd=fixture,env=environment,capture_output=True,encoding='utf-8',timeout=60)
    if result.returncode!=23 or '空 格.txt' not in result.stdout or 'installed-tool-needle' not in result.stdout:
        raise ValueError('Installed private Shell/Git/ripgrep failed under the minimal system PATH: '+result.stderr[-500:])
    checks.append({'name':'actual-installed-dispatcher-bundled-shell-git-rg-minimal-path','status':'pass',
        'exit_code':23,'unicode_paths':True,'native_isolation_verified':False})
    return checks


def verify_external_cleanup(executable,directory):
    import ctypes
    from ctypes import wintypes
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];api.OpenProcess.restype=wintypes.HANDLE
    api.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];api.WaitForSingleObject.restype=wintypes.DWORD
    api.CloseHandle.argtypes=[wintypes.HANDLE];api.CloseHandle.restype=wintypes.BOOL
    script="import os,subprocess,sys,time;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);print(os.getpid(),p.pid,flush=True);time.sleep(120)"
    process=subprocess.Popen([str(executable),'external-worker',json.dumps([sys.executable,'-c',script]),'0'],
        cwd=directory,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW)
    handles=[]
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(process.stdout.readline)
            try:line=future.result(timeout=40)
            except TimeoutError:
                process.kill();raise ValueError('Owned external worker did not start')
        ids=[int(value) for value in line.split()]
        if len(ids)!=2:raise ValueError('Owned external child identities were not observed')
        for pid in ids:
            handle=api.OpenProcess(0x00100000,False,pid)
            if not handle:raise ValueError('Owned external process identity is unavailable')
            handles.append(handle)
        process.kill();process.wait(timeout=10)
        if any(api.WaitForSingleObject(handle,10000)!=0 for handle in handles):
            raise ValueError('Forced worker termination left a live descendant')
        return {'name':'actual-frozen-external-worker-owned-tree-termination','status':'pass','observed_processes':2}
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=10)
        for handle in handles:api.CloseHandle(handle)
        if process.stdout:process.stdout.close()
        if process.stderr:process.stderr.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output=args.output.resolve()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    try:report=verify(args.output)
    except Exception as error:report={'status':'fail','reason':str(error),'checks':[],'eligible_for_native_pass':False}
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report));return {'pass':0,'blocked':2,'fail':1}[report['status']]
if __name__=='__main__':raise SystemExit(main())
