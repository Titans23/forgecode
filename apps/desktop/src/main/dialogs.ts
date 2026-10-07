/** Native file selection and privacy choices belong to Main, never to Renderer. */
import { dialog,type BrowserWindow } from 'electron';
import { randomUUID,createHash } from 'node:crypto';
import { open,lstat,link,unlink } from 'node:fs/promises';
import { dirname,basename,resolve,isAbsolute,join } from 'node:path';
import type { EngineSupervisor } from './supervisor.js';
import type {Artifact} from '@forgecode/contracts';
import { nativeOperation } from './ipc.js';

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

export class NativeFileDialogs {
  constructor(private engine:()=>EngineSupervisor){}
  private guard(engine:EngineSupervisor,current:()=>BrowserWindow) {
    const epoch=engine.hello?.engine_epoch;
    return ()=>{current();if(this.engine()!==engine||engine.hello?.engine_epoch!==epoch||engine.state!=='ready')throw new Error('Engine changed during file selection');};
  }
  private async select(current:()=>BrowserWindow,kind:'configuration'|'results') {
    const result=await dialog.showOpenDialog(current(),{title:kind==='results'?'导入 ForgeCode 结果包':'导入完整实验配置',properties:['openFile'],
      filters:[{name:kind==='results'?'ForgeCode result bundle':'RunSpec with resolved snapshots',extensions:[kind==='results'?'zip':'json']}]});
    current();if(result.canceled)return null;
    if(result.filePaths.length!==1||!isAbsolute(result.filePaths[0]))throw new Error('Invalid native selection');
    return resolve(result.filePaths[0]);
  }
  importConfiguration(current:()=>BrowserWindow) {
    return nativeOperation(async()=>{
      const engine=this.engine(),guard=this.guard(engine,current),path=await this.select(current,'configuration');guard();if(!path)return{cancelled:true};
      const choice=await dialog.showMessageBox(current(),{type:'warning',title:'导入实验配置',message:'将完整配置作为来源未验证的模板保存？',
        detail:'导入不授权模型支出或本机执行。需要配置及全部 resolved snapshots；不会执行其中脚本、修改凭证或安装程序。',buttons:['取消','导入只读配置'],defaultId:0,cancelId:0,noLink:true});
      guard();if(choice.response!==1)return{cancelled:true};
      const result=await engine.call('evaluation.import_plan',{client_action_id:'act-'+randomUUID(),path});guard();
      return{cancelled:false,template_id:result.template_id,origin:result.origin};
    });
  }
  importResults(current:()=>BrowserWindow) {
    return nativeOperation(async()=>{
      const engine=this.engine(),guard=this.guard(engine,current),path=await this.select(current,'results');guard();if(!path)return{cancelled:true};
      const choice=await dialog.showMessageBox(current(),{type:'warning',title:'导入外部结果包',message:'校验并保留原始结果包？',
        detail:'包按限额验证，不执行任务代码。来源显示 imported_unverified，原始包保留在私有目录；导入不会增加本机费用或覆盖本机成绩。',buttons:['取消','验证并导入'],defaultId:0,cancelId:0,noLink:true});
      guard();if(choice.response!==1)return{cancelled:true};
      const grant=await engine.call('bundle.prepare_import',{path});guard();
      const result=await engine.call('bundle.import',{client_action_id:'act-'+randomUUID(),source_token:grant.source_token});guard();
      return{cancelled:false,run_ids:result.run_ids,origin:result.origin};
    });
  }
  exportResults(runId:string,current:()=>BrowserWindow) {
    return nativeOperation(async()=>{
      const engine=this.engine(),guard=this.guard(engine,current),snapshot=await engine.call('evaluation.snapshot',{run_id:runId,limit:1});guard();
      if(snapshot.read_only)throw new Error('外部结果为只读，请保留原始包。');
      const privacy=await dialog.showMessageBox(current(),{type:'warning',title:'选择导出隐私范围',message:'导出当前实验的冻结结果与校验值',
        detail:runId+'\n仅元数据包含配置标识、尝试、评分、费用和证据索引。脱敏副本还可能包含代码、路径和工具输出；原始 grader 产物不会被覆盖。',
        buttons:['取消','仅元数据','含脱敏产物副本'],defaultId:0,cancelId:0,noLink:true});guard();if(![1,2].includes(privacy.response))return{cancelled:true};
      const classification=privacy.response===1?'metadata_only':'redacted_artifacts';
      const selected=await dialog.showSaveDialog(current(),{title:'保存 ForgeCode 结果包（不会覆盖已有文件）',defaultPath:'forge-'+runId+'.zip',filters:[{name:'Result bundle',extensions:['zip']}]});
      guard();if(selected.canceled||!selected.filePath)return{cancelled:true};if(!isAbsolute(selected.filePath))throw new Error('Invalid native destination');
      const scope={kind:'run' as const,id:runId};
      const grant=await engine.call('bundle.prepare_export',{path:selected.filePath,scope,classification});guard();
      await engine.call('bundle.export',{client_action_id:'act-'+randomUUID(),destination_token:grant.destination_token,scope,classification});guard();
      return{cancelled:false,saved:true};
    });
  }
  private async artifactBytes(engine:EngineSupervisor,guard:()=>void,reference:Artifact) {
    if(reference.size_bytes>1048576||!reference.available)throw new Error('Metadata artifact unavailable or exceeds quota');
    const parts:Buffer[]=[];let offset=0;
    while(offset<reference.size_bytes){const part=await engine.call('artifact.read_chunk',{artifact_id:reference.artifact_id,offset,length:Math.min(262144,reference.size_bytes-offset)});guard();
      const bytes=Buffer.from(part.data_base64,'base64');if(!bytes.length||part.offset!==offset||part.sha256!==reference.sha256||bytes.length>reference.size_bytes-offset)throw new Error('Metadata artifact changed');
      parts.push(bytes);offset+=bytes.length;if(part.eof!==(offset===reference.size_bytes))throw new Error('Incomplete metadata artifact');}
    const bytes=Buffer.concat(parts);if(createHash('sha256').update(bytes).digest('hex')!==reference.sha256)throw new Error('Metadata hash differs');
    return bytes;
  }
  exportCandidate(candidateId:string,current:()=>BrowserWindow) {
    return nativeOperation(async()=>{
      const engine=this.engine(),guard=this.guard(engine,current),candidate=await engine.call('failure.candidate',{candidate_id:candidateId});guard();
      const choice=await dialog.showMessageBox(current(),{type:'warning',title:'导出脱敏回归候选',message:'保存候选元数据和证据引用？',
        detail:'已保存不代表已复现。文件含冻结配置引用、任务标识和人工说明，不含原始模型输出或凭证；导出不授权执行和模型支出。',
        buttons:['取消','导出候选'],defaultId:0,cancelId:0,noLink:true});guard();if(choice.response!==1)return{cancelled:true};
      const selected=await dialog.showSaveDialog(current(),{title:'保存回归候选（不会覆盖已有文件）',defaultPath:'forge-'+candidateId+'.json',filters:[{name:'Regression candidate',extensions:['json']}]});
      guard();if(selected.canceled||!selected.filePath)return{cancelled:true};
      const bytes=await this.artifactBytes(engine,guard,candidate.fixture);
      await publishSelectedFile(selected.filePath,bytes,guard);guard();return{cancelled:false,saved:true};
    });
  }
  exportConfiguration(runId:string,current:()=>BrowserWindow) {
    return nativeOperation(async()=>{
      const engine=this.engine(),guard=this.guard(engine,current);
      await engine.call('evaluation.snapshot',{run_id:runId,limit:1});guard();
      const choice=await dialog.showMessageBox(current(),{type:'warning',title:'导出可复现实验配置',message:'保存 RunSpec 与完整配置快照？',
        detail:'配置可能包含源版本、任务标识、本机路径、策略及环境信息。不会导出凭证；导出不授权执行或模型支出。',buttons:['取消','导出配置'],defaultId:0,cancelId:0,noLink:true});
      guard();if(choice.response!==1)return{cancelled:true};
      const selected=await dialog.showSaveDialog(current(),{title:'保存实验配置（不会覆盖已有文件）',defaultPath:'forge-'+runId+'.json',filters:[{name:'Experimental configuration',extensions:['json']}]});
      guard();if(selected.canceled||!selected.filePath)return{cancelled:true};
      const exported=await engine.call('evaluation.plan_export',{run_id:runId});guard();const reference=exported.plan_artifact;
      const bytes=await this.artifactBytes(engine,guard,reference);
      await publishSelectedFile(selected.filePath,bytes,guard);guard();return{cancelled:false,saved:true};
    });
  }
}
