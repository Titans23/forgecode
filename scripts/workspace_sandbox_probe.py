"""Real, model-free workspace-write acceptance. Never establishes strict sandbox readiness."""
import argparse
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import platform
import sys
import subprocess
import tempfile
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forge.application.models import ContractError
from forge.engine.persistence import new_id
from forge.sandbox.file_client import FileWorkerClient
from forge.sandbox.workspace_backend import WorkspaceWriteBackend


def fixture():
    root = Path(tempfile.mkdtemp(prefix='forge-workspace-acceptance-')).resolve()
    if sys.platform == 'win32':
        from forge.sandbox.windows_worker import grant_controller_access
        grant_controller_access(root)
    project = root / 'project'; project.mkdir()
    info = project.stat()
    workspace = {'id': new_id('ws'), 'canonical_path': str(project), 'file_identity': f'{info.st_dev}:{info.st_ino}'}
    policy = json.loads((ROOT/'contracts/v1/examples/sandbox-policy.valid.json').read_bytes())
    policy['workspace_id'] = workspace['id']
    policy['filesystem'].update(read_mode='host_default',read_roots=[str(project)],write_roots=[str(project)],protected_paths=[])
    policy['network'].update(mode='inherit',allowed_domains=[],dns_isolation_required=False)
    policy['environment_keys'] = ['PATH', 'FORGE_PROBE']
    owner = {'engine_epoch':new_id('epoch'),'sandbox_session_id':new_id('sandbox'),'execution_id':None}
    return root, workspace, policy, WorkspaceWriteBackend(workspace,owner,root/'control')


async def command(backend, script, *, seconds=20, cancel=False, environment=None, output_limit=65536):
    identity = new_id('exec')
    spec = {'mode':'argv','argv':[str(backend.runtime.node),'-e',script],
        'cwd':backend.workspace['canonical_path'],'environment':environment or {},
        'deadline_utc':(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat().replace('+00:00','Z'),
        'output_limit_bytes':output_limit,'stdin_base64':base64.b64encode(b'task-stdin').decode()}
    chunks = {'stdout':bytearray(),'stderr':bytearray()}
    async def drain():
        ended = set()
        while len(ended) != 2:
            frame = await backend.outputs.get()
            assert frame['execution_id'] == identity
            chunks[frame['stream']].extend(base64.b64decode(frame['raw_base64']))
            if frame['final']: ended.add(frame['stream'])
    reader = asyncio.create_task(drain())
    await backend.execute(identity,spec)
    if cancel:
        # Wait for the actual child-start output, not a scheduling delay.
        async with asyncio.timeout(10):
            while b'child-started' not in chunks['stdout']: await asyncio.sleep(.02)
        await backend.cancel(identity,'native acceptance',spec['deadline_utc'])
    await asyncio.wait_for(backend._executions[identity]['task'],seconds+12)
    await asyncio.wait_for(reader,2)
    return await backend.status(identity), {k:bytes(v).decode('utf-8',errors='replace') for k,v in chunks.items()}, backend._executions[identity]['cleanup']


async def probe():
    root, workspace, policy, backend = fixture()
    project=Path(workspace['canonical_path'])
    report={'schema_version':'forge.workspace-write.acceptance.v1','platform':sys.platform,'os_build':platform.platform(),
        'fixture':str(root),'mode':'workspace-write','public_model_calls':0,'eligible_for_native_pass':False,'checks':[]}
    def check(name, condition, **facts):
        report['checks'].append({'name':name,'status':'pass' if condition else 'fail',**facts})
        if not condition: raise AssertionError(name)
    try:
        started = perf_counter()
        prepared=await backend.prepare(policy)
        report['prepare_seconds'] = perf_counter() - started
        report['capabilities']=prepared['capabilities']
    except Exception as error:
        report.update(status='blocked',reason=str(error))
        report['initialization']=[{k:v for k,v in e.items() if k in ('exit_code','error','cleanup_error','process_cleanup')} |
            {'stderr':e['captured']['stderr'].decode('utf-8',errors='replace')} for e in backend._executions.values()]
        report['cleanup']=await backend.close()
        return report
    try:
        check('real write, denied outside write, private temp, stdin and cleanup probe',True)
        check('limited capabilities cannot satisfy strict policy',prepared['capabilities']['verification']['write_isolation']['status']=='partial' and not prepared['capabilities']['write_isolation'])
        client=FileWorkerClient(workspace,backend._prepared.value,native=backend)
        result=await client.request('tool',name='write_file',arguments={'path':'中文 空格.txt','content':'one\r\ntwo\r\n'})
        check('file tool writes through selected native backend',result['result']['success'],result=result['result'])
        result=await client.request('tool',name='read_file',arguments={'path':'中文 空格.txt'})
        check('file tool reads unicode paths and CRLF',result['result']['success'] and (project/'中文 空格.txt').read_bytes()==b'one\r\ntwo\r\n')
        result=await client.request('tool',name='run_command',arguments={'command':'git --version','timeout_seconds':10})
        check('shell tool uses selected native backend',result['result']['success'],result=result['result'])
        (project/'.env').write_text('SYNTHETIC=host-readable',encoding='utf-8')
        script="const fs=require('node:fs');console.log(JSON.stringify({env:process.env.FORGE_PROBE,stdin:fs.readFileSync(0,'utf8'),dynamic:fs.readFileSync('.env','utf8')}));"
        status, output, cleanup=await command(backend,script,environment={'FORGE_PROBE':'bounded'})
        check('dynamic .env read is a declared light-mode limitation; stdin and filtered env work',status['exit_code']==0 and json.loads(output['stdout'])=={'env':'bounded','stdin':'task-stdin','dynamic':'SYNTHETIC=host-readable'},cleanup=cleanup)
        try: await client.request('tool',name='read_file',arguments={'path':'.env'})
        except ContractError as error: check('file tool retains sensitive path denial',error.kind=='POLICY_DENIED')
        else: check('file tool retains sensitive path denial',False)
        outside=root/'outside.txt'; outside.write_text('untouched',encoding='utf-8')
        script="const fs=require('node:fs');let denied=false;try{fs.writeFileSync("+json.dumps(str(outside))+",'escape')}catch{denied=true};console.log(denied)"
        status,output,cleanup=await command(backend,script)
        check('ordinary sibling file write is denied',status['exit_code']==0 and output['stdout'].strip()=='true' and outside.read_text()=='untouched')
        async def echo(reader, writer):
            try:
                data=await asyncio.wait_for(reader.readexactly(5),5)
                writer.write(data);await writer.drain()
            finally:
                writer.close();await writer.wait_closed()
        server=await asyncio.start_server(echo,'127.0.0.1',0)
        try:
            port=server.sockets[0].getsockname()[1]
            script="const s=require('node:net').connect("+str(port)+",'127.0.0.1',()=>s.write('hello'));s.on('data',d=>{console.log(d.toString());s.end()});s.on('error',()=>process.exit(2));"
            status,output,cleanup=await command(backend,script)
            check('host loopback network inherited without public traffic',status['exit_code']==0 and output['stdout'].strip()=='hello' and cleanup['state']=='clean')
        finally:
            server.close();await server.wait_closed()
        if sys.platform=='win32':
            outside_dir=root/'outside-dir';outside_dir.mkdir()
            junction=project/'junction'
            made=subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/cmd.exe'),'/d','/c','mklink','/J',str(junction),str(outside_dir)],capture_output=True)
            check('native private junction fixture created',made.returncode==0,exit_code=made.returncode)
            try:
                status,output,cleanup=await command(backend,"try{require('node:fs').writeFileSync('junction/escape','bad');console.log('writable')}catch{console.log('denied')}")
                check('junction cannot write outside workspace',output['stdout'].strip()=='denied' and not (outside_dir/'escape').exists())
            finally:
                junction.rmdir()
        link=project/'linked.txt';os.link(outside,link)
        try: await client.request('tool',name='write_file',arguments={'path':'linked.txt','content':'escape'})
        except ContractError as error: check('file tool refuses hard links',error.kind in ('POLICY_DENIED','STALE_FILE'))
        else: check('file tool refuses hard links',False)
        # Observe the documented inode-alias limitation without touching user data.
        status,output,cleanup=await command(backend,"const fs=require('node:fs');try{fs.writeFileSync('linked.txt','alias-write');console.log('writable')}catch{console.log('denied')}")
        report['hardlink_shell_observation'] = {'outcome':output['stdout'].strip(), 'outside_changed':outside.read_text()!='untouched',
            'limitation':'Partial write boundary; dynamically introduced hard links are not an inode isolation guarantee'}
        link.unlink()
        outside.write_text('untouched',encoding='utf-8')
        started = perf_counter()
        workload = """const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const [git,python]=ARGS;
function run(exe,args){const out=fs.openSync('child-output.txt','w');let r;try{r=cp.spawnSync(exe,args,{stdio:['ignore',out,out],timeout:10000});}finally{fs.closeSync(out)}const text=fs.readFileSync('child-output.txt','utf8');if(r.status!==0)throw Error(JSON.stringify({exe,args,status:r.status,error:r.error?.code,output:text}));return text;}
fs.writeFileSync('test_fixture.py','import unittest\\nclass TestFixture(unittest.TestCase):\\n def test_add(self): self.assertEqual(2+3,5)\\n');
run(git,['init','--quiet']);run(git,['add','test_fixture.py']);run(git,['-c','user.name=ForgeFixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','--quiet','-m','offline fixture']);
fs.appendFileSync('test_fixture.py','# modified\\n');if(!run(git,['diff','--','test_fixture.py']).includes('modified'))throw Error('git diff missing');
run(python,['-m','unittest','test_fixture']);
run(process.execPath,['-e',"require('node:fs').mkdirSync('dist');require('node:fs').writeFileSync('dist/result.json',JSON.stringify({sum:2+3}))"]);
if(JSON.parse(fs.readFileSync('dist/result.json')).sum!==5)throw Error('build result');
for(const key of ['npm_config_cache','PIP_CACHE_DIR','UV_CACHE_DIR','XDG_CACHE_HOME']){const p=process.env[key];if(!p||!p.startsWith(process.env.TEMP+path.sep))throw Error('cache escaped');fs.mkdirSync(p,{recursive:true});fs.writeFileSync(path.join(p,'probe'),'cache');}
console.log('git-python-node-build-ok');""".replace('ARGS',json.dumps([str(backend.runtime.tools['git']) if sys.platform=='win32' else '/usr/bin/git',sys.executable]))
        status,output,cleanup=await command(backend,workload,seconds=30)
        report['workload_seconds'] = perf_counter()-started
        check('offline Git commit/diff, Python tests, Node child build with file stdio and private caches',status['exit_code']==0 and 'git-python-node-build-ok' in output['stdout'] and cleanup['state']=='clean',output=output,cleanup=cleanup)
        status,output,cleanup=await command(backend,"const r=require('node:child_process').spawnSync(process.execPath,['-e',\"console.log('pipe-child')\"],{encoding:'utf8'});console.log(JSON.stringify({status:r.status,error:r.error?.code??null,output:r.stdout??null}))")
        pipe=json.loads(output['stdout'])
        report['node_pipe_stdio']={'status':'blocked' if pipe['error'] else 'pass','observation':pipe,
            'reason':'Pinned DSH WRITE_RESTRICTED denies libuv named-pipe client writes; inherited/file stdio is supported' if pipe['error'] else None}
        check('Node piped-grandchild limitation matches declared platform capability',status['exit_code']==0 and
            (pipe['error']=='EPERM' if sys.platform=='win32' else pipe=={'status':0,'error':None,'output':'pipe-child\n'}))
        status,output,cleanup=await command(backend,"process.stdout.write('x'.repeat(10000));process.exitCode=7",output_limit=128)
        check('exit code and output budget survive native dispatcher',status['exit_code']==7 and len(output['stdout'])<=128 and status['discarded_bytes']>0 and cleanup['state']=='clean')
        if sys.platform=='linux':
            (project/'linked-path').symlink_to(outside)
            status,output,cleanup=await command(backend,"try{require('node:fs').writeFileSync('linked-path','escape');console.log('writable')}catch{console.log('denied')}")
            check('symlink does not bypass read-only outside mount',output['stdout'].strip()=='denied' and outside.read_text()=='untouched')
            (project/'linked-path').unlink()
        script="const {spawn}=require('node:child_process');let c=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'inherit'});c.on('spawn',()=>console.log('child-started'));setInterval(()=>{},1000);"
        status,output,cleanup=await command(backend,script,cancel=True)
        check('cancel reconciles owned parent and descendants',cleanup['state']=='clean',state=status['state'],cleanup=cleanup)
        status,output,cleanup=await command(backend,script,seconds=4)
        check('timeout reconciles owned parent and descendants',status['state']=='indeterminate' and cleanup['state']=='clean' and 'child-started' in output['stdout'],cleanup=cleanup)
        script="const c=require('node:child_process').spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{detached:true,stdio:'ignore'});c.unref();console.log('parent-exiting');"
        status,output,cleanup=await command(backend,script)
        check('parent exit reconciles detached child',status['exit_code']==0 and 'parent-exiting' in output['stdout'] and cleanup['state']=='clean',cleanup=cleanup)
        cleanup=await client.close()
        check('close confirms process and temporary resource absence',cleanup['state']=='clean' and not list(backend.control_root.glob('temp-exec-*')),cleanup=cleanup)
        report['status']='pass'
    except Exception as error:
        report.update(status='fail',reason=type(error).__name__+': '+str(error))
    finally:
        report['cleanup']=await backend.close()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    from scripts.evidence_gate import source_fingerprint
    before=source_fingerprint(ROOT)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    report=asyncio.run(probe())
    report.update(git_commit=commit,source_inventory_hash=before,
                  source_unchanged=source_fingerprint(ROOT)==before,python_version=platform.python_version(),
                  python_executable=sys.executable,source_root=str(ROOT))
    if not report['source_unchanged']: report.update(status='fail',reason='Source changed during native verification')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'checks':len(report['checks']),'reason':report.get('reason'),'report':str(args.output)},ensure_ascii=False))
    return {'pass':0,'blocked':2,'fail':1}[report['status']]
if __name__=='__main__':raise SystemExit(main())
