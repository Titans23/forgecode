/** Builtins-only grouped verification shared by installed Main and Bridge. */
import {createHash} from 'node:crypto';
import {readFile,realpath,lstat,readlink,readdir} from 'node:fs/promises';
import {isAbsolute,resolve,relative,sep} from 'node:path';
const digest=b=>createHash('sha256').update(b).digest('hex');
function toolPath(value) {
  if(typeof value!=='string'||!value||isAbsolute(value)||value.includes('\\')||value.includes(':')||value.includes('\0')||
      value.split('/').some(part=>!part||part==='.'||part==='..'||/[ .]$/.test(part)||/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(part))) {
    throw new Error('Invalid tool inventory path');
  }
  return value;
}
async function verifyToolTree(directory, entries) {
  if((await lstat(directory)).isSymbolicLink())throw new Error('Tool inventory cannot contain links');
  const actual=[];
  async function walk(path, prefix='') {
    for(const item of await readdir(path,{withFileTypes:true})) {
      if(item.isSymbolicLink())throw new Error('Tool inventory cannot contain links');
      const name=prefix+item.name;
      if(item.isDirectory())await walk(resolve(path,item.name),name+'/');
      else if(item.isFile())actual.push(name);
      else throw new Error('Tool inventory requires regular files');
    }
  }
  await walk(directory);
  if(JSON.stringify(actual.sort())!==JSON.stringify(entries.map(a=>toolPath(a.path)).sort()))throw new Error('Tool directory inventory mismatch');
}
export async function verifyToolFiles(directory, entries) {
  await verifyToolTree(directory, entries);
  for(const asset of entries) {
    const bytes=await readFile(resolve(directory,asset.path));
    if(bytes.length!==asset.size_bytes||digest(bytes)!==asset.sha256)throw new Error('Tool resource integrity mismatch');
  }
}
export async function verifyToolBundle(root,bundle) {
  root=await realpath(root);
  if(!['powershell','git','ripgrep'].includes(bundle.name)||!/^\d+(?:\.\d+){2,3}$/.test(bundle.version)||bundle.platform!=='win32-x64'||
      bundle.path!==`.local/release-runtime/${bundle.name}-${bundle.version}-win32-x64`)throw new Error('Invalid private tool identity/layout');
  const directory=resolve(root,bundle.path),owned=resolve(root,'.local/release-runtime');
  const actual=await realpath(directory),part=relative(owned,actual);
  if(part==='..'||part.startsWith('..'+sep)||isAbsolute(part)||(await lstat(directory)).isSymbolicLink())throw new Error('Tool directory escapes private assets');
  const inventoryPath=await realpath(resolve(root,toolPath(bundle.inventory))),inventoryPart=relative(root,inventoryPath);
  if(inventoryPart==='..'||inventoryPart.startsWith('..'+sep)||isAbsolute(inventoryPart))throw new Error('Tool inventory escapes installation');
  const raw=await readFile(inventoryPath);
  if(raw.length>4*1024*1024||digest(raw)!==bundle.inventory_sha256)throw new Error('Tool inventory integrity mismatch');
  const inventory=JSON.parse(raw),entries=inventory.files;
  if(inventory.schema_version!=='forge.tool.inventory.v1'||!Array.isArray(entries)||!entries.length||entries.length>10000)throw new Error('Invalid tool inventory');
  const names=entries.map(a=>toolPath(a.path).toLowerCase());
  if(new Set(names).size!==names.length||!entries.some(a=>a.path===bundle.entry))throw new Error('Tool inventory conflict or missing entry');
  await verifyToolFiles(directory,entries);
  return resolve(directory,bundle.entry);
}
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
    known.set(a.path.toLowerCase(),a);
  }
  // Bound disk work while checking every byte; cold installs must not serialize
  // thousands of independent metadata/read operations on the Main startup path.
  for(let offset=0;offset<m.files.length;offset+=8) await Promise.all(m.files.slice(offset,offset+8).map(async a=>{
    const p=resolve(root,a.path), actual=await realpath(p),part=relative(root,actual);
    if(part==='..'||part.startsWith('..'+sep)||isAbsolute(part)) throw new Error('Release link escaped trusted root');
    const st=await lstat(p), link=st.isSymbolicLink()?await readlink(p):undefined;
    if(link!==a.symlink) throw new Error('Release link inventory mismatch');
    const bytes=await readFile(actual);
    if(bytes.length!==a.size_bytes||digest(bytes)!==a.sha256) throw new Error('Release asset integrity mismatch: '+a.path);
  }));
  const same=a=>a&&JSON.stringify(known.get(a.path?.toLowerCase()))===JSON.stringify(a);
  for(const [key,prefix] of [['engine','engine/'+m.build_id+'/'],['bridge','bridge/'+m.build_id+'/'],['node','runtimes/node/']]) {
    if(!m[key]?.path.startsWith(prefix)||!same(m[key])) throw new Error('Release grouped component mismatch');
  }
  for(const key of ['engine_dependencies','bridge_dependencies','native_helpers','ui_assets']) for(const a of m[key]??[]) {
    if(!same(a))throw new Error('Release dependency inventory mismatch');
  }
  for(const [name,tool] of Object.entries(m.tools??{})) {
    const prefix=name==='powershell'?'runtimes/powershell/':`tools/${name}/`;
    if(!['powershell','git','ripgrep'].includes(name)||!toolPath(tool.root).startsWith(prefix)||
        !tool.entry?.path.startsWith(tool.root+'/')||!same(tool.entry))throw new Error('Invalid installed tool group');
    const directory=tool.root+'/';
    // Tool bytes were hashed above; still reject extra files and all tool links.
    await verifyToolTree(resolve(root,tool.root),m.files.filter(a=>a.path.startsWith(directory)).map(a=>({...a,path:a.path.slice(directory.length)})));
  }
  if(known.get('contracts/manifest.json')?.sha256!==m.contract_manifest_hash) throw new Error('Release contract mismatch');
  return m;
}
