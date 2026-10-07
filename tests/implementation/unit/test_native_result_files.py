"""Exercise Main's actual atomic native-selected file writer on this filesystem."""
import json
from pathlib import Path
import subprocess


def test_native_selected_file_never_overwrites_follows_links_or_publishes_after_guard_loss(tmp_path):
    script=r'''
import {rolldown}from'rolldown';import{createRequire}from'node:module';import{readFile,mkdir,readdir,symlink}from'node:fs/promises';import{join}from'node:path';
const require=createRequire(process.cwd()+'/package.json');const b=await rolldown({input:'apps/desktop/src/main/file-publication.ts',platform:'node',external:['electron'],transform:{jsx:'react'},onLog(level,log,handler){if(level==='error')handler(level,log);else console.error(log.message);}}),r=await b.generate({format:'cjs'}),m={exports:{}};new Function('module','exports','require','__filename',r.output[0].code)(m,m.exports,require,process.cwd()+'/apps/desktop/src/main/file-publication.ts');await b.close();
const {publishSelectedFile}=m.exports,root=process.argv[1],target=join(root,'result.json');await publishSelectedFile(target,Buffer.from('first'),()=>{});
let overwrite=false,linkDenied=false,guardDenied=false;try{await publishSelectedFile(target,Buffer.from('second'),()=>{});}catch{overwrite=true;}
const actual=join(root,'actual');await mkdir(actual);const linked=join(root,'linked');await symlink(actual,linked,process.platform==='win32'?'junction':'dir');
try{await publishSelectedFile(join(linked,'unsafe.json'),Buffer.from('no'),()=>{});}catch{linkDenied=true;}
let checks=0;try{await publishSelectedFile(join(root,'aborted.json'),Buffer.from('no'),()=>{if(++checks>=3)throw Error('requesting window changed');});}catch{guardDenied=true;}
const files=await readdir(root);console.log(JSON.stringify({bytes:(await readFile(target,'utf8'))==='first',overwrite,linkDenied,guardDenied,noPartial:!files.includes('aborted.json')&&!files.some(x=>x.startsWith('.forge-export-')),noUnsafe:!(await readdir(actual)).length}));
'''
    result=subprocess.run(['node','--input-type=module','-e',script,str(tmp_path)],cwd=Path(__file__).resolve().parents[3],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.lstrip().startswith('{'), repr({'stdout':result.stdout,'stderr':result.stderr})
    assert all(json.loads(result.stdout).values())
