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


async def command(backend, script, *, seconds=20, cancel=False, environment=None):
    identity = new_id('exec')
    spec = {'mode':'argv','argv':[str(backend.runtime.node),'-e',script],
        'cwd':backend.workspace['canonical_path'],'environment':environment or {},
        'deadline_utc':(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat().replace('+00:00','Z'),
        'output_limit_bytes':65536,'stdin_base64':base64.b64encode(b'task-stdin').decode()}
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
        prepared=await backend.prepare(policy)
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
        link=project/'linked.txt';os.link(outside,link)
        try: await client.request('tool',name='write_file',arguments={'path':'linked.txt','content':'escape'})
        except ContractError as error: check('file tool refuses hard links',error.kind in ('POLICY_DENIED','STALE_FILE'))
        else: check('file tool refuses hard links',False)
        link.unlink()
        script="const {spawn}=require('node:child_process');let c=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'inherit'});c.on('spawn',()=>console.log('child-started'));setInterval(()=>{},1000);"
        status,output,cleanup=await command(backend,script,cancel=True)
        check('cancel reconciles owned parent and descendants',cleanup['state']=='clean',state=status['state'],cleanup=cleanup)
        status,output,cleanup=await command(backend,script,seconds=4)
        check('timeout reconciles owned parent and descendants',status['state']=='indeterminate' and cleanup['state']=='clean' and 'child-started' in output['stdout'],cleanup=cleanup)
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
