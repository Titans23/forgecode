/** Atomic no-replace publication, independent of Electron installation and dialogs. */
import { randomUUID } from 'node:crypto';
import { open,lstat,link,unlink } from 'node:fs/promises';
import { dirname,basename,isAbsolute,join } from 'node:path';

export async function publishSelectedFile(path:string,bytes:Uint8Array,current:()=>void) {
  if(!isAbsolute(path)||basename(path).length>240)throw new Error('Invalid native destination');
  const parent=dirname(path);let ancestor=parent;
  for(;;){const info=await lstat(ancestor);if(info.isSymbolicLink()||!info.isDirectory())throw new Error('Destination contains a link');const next=dirname(ancestor);if(next===ancestor)break;ancestor=next;}
  const original=await lstat(parent),temporary=join(parent,'.forge-export-'+randomUUID()+'.tmp');
  const sameParent=async()=>{const fresh=await lstat(parent);if(fresh.isSymbolicLink()||fresh.dev!==original.dev||fresh.ino!==original.ino)throw new Error('Destination directory changed');};
  const unchanged=async()=>{current();await sameParent();current();};
  let created=false;
  try {
    await unchanged();const file=await open(temporary,'wx',0o600);created=true;
    try{await file.writeFile(bytes);await file.sync();}finally{await file.close();}
    await unchanged();await link(temporary,path); // Atomic no-replace publication, including after a save dialog's overwrite prompt.
    if(process.platform!=='win32'){const directory=await open(parent,'r');try{await directory.sync();}finally{await directory.close();}}
  } finally {if(created){await sameParent();await unlink(temporary);}}
}
