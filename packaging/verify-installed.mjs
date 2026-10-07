/** Builtins-only grouped verification shared by installed Main and Bridge. */
import {createHash} from 'node:crypto';
import {readFile,realpath,lstat,readlink} from 'node:fs/promises';
import {isAbsolute,resolve,relative,sep} from 'node:path';
const digest=b=>createHash('sha256').update(b).digest('hex');
export async function verifyInstalled(root,{target=process.platform+'-'+process.arch,contractHash,production=false}={}) {
  root=await realpath(root);
  const raw=await readFile(resolve(root,'release-manifest.json'));
  if(raw.length>16*1024*1024) throw new Error('Release manifest quota exceeded');
  const m=JSON.parse(raw);
  if(!m.platform||!m.build_id||!m.protocol||!Array.isArray(m.files))throw new Error('Release manifest is incomplete');
  if(m.schema_version!=='forge.release.manifest.v1'||m.platform!==target||!['win32-x64','linux-x64'].includes(target)||m.protocol!=='forge.engine.v1'||
      !/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}$/.test(m.build_id)||!Number.isSafeInteger(m.database_schema)||m.database_schema<1||
      !/^[0-9a-f]{64}$/.test(m.contract_manifest_hash)||contractHash&&contractHash!==m.contract_manifest_hash) throw new Error('Release component/protocol mismatch');
  if(production) throw new Error('Production signing/security acceptance requires external evidence; preview metadata is insufficient');
  if(!Array.isArray(m.files)||!m.files.length||m.files.length>30000) throw new Error('Release inventory incomplete');
  const known=new Map();
  for(const a of m.files) {
    if(typeof a.path!=='string'||!a.path||isAbsolute(a.path)||a.path.includes('\\')||a.path.includes(':')||a.path.split('/').includes('..')||
       known.has(a.path.toLowerCase())) throw new Error('Release asset path/conflict');
    const p=resolve(root,a.path), actual=await realpath(p),part=relative(root,actual);
    if(part==='..'||part.startsWith('..'+sep)||isAbsolute(part)) throw new Error('Release link escaped trusted root');
    const st=await lstat(p), link=st.isSymbolicLink()?await readlink(p):undefined;
    if(link!==a.symlink) throw new Error('Release link inventory mismatch');
    const bytes=await readFile(actual);
    if(bytes.length!==a.size_bytes||digest(bytes)!==a.sha256) throw new Error('Release asset integrity mismatch: '+a.path);
    known.set(a.path.toLowerCase(),a);
  }
  const same=a=>a&&JSON.stringify(known.get(a.path?.toLowerCase()))===JSON.stringify(a);
  for(const [key,prefix] of [['engine','engine/'+m.build_id+'/'],['bridge','bridge/'+m.build_id+'/'],['node','runtimes/node/']]) {
    if(!m[key]?.path.startsWith(prefix)||!same(m[key])) throw new Error('Release grouped component mismatch');
  }
  for(const key of ['engine_dependencies','bridge_dependencies','native_helpers','ui_assets']) for(const a of m[key]??[]) {
    if(!same(a))throw new Error('Release dependency inventory mismatch');
  }
  if(known.get('contracts/manifest.json')?.sha256!==m.contract_manifest_hash) throw new Error('Release contract mismatch');
  return m;
}
