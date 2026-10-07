/** Same business DTOs as DesktopTransport; only the authenticated wire changes. */
import { canonicalString,ContractError,METHODS,MAX_FRAME_BYTES,strictLoads,validate,validateEvent } from '@forgecode/contracts/browser';
import type { DesktopOperations,DesktopStatus,ConnectionPage } from './transport';

type Input<K extends keyof DesktopOperations> = Parameters<DesktopOperations[K]>[0];
type Output<K extends keyof DesktopOperations> = Awaited<ReturnType<DesktopOperations[K]>>;
type Pending = { method: keyof typeof METHODS; client_action_id: string; payload_hash: string };
type Metadata = { csrf:string; health:Output<'diagnostics'>; mode:string; profile:string; credential_storage:ConnectionPage['protection'] };
type Subscription = { subscription_id:string; cursor:string; history_gap:boolean };
type Batch = { jsonrpc:'2.0'; method:'events.batch'; params:{subscription_id:string;cursor:string;events:Array<{event_id:string;event_type:string;[key:string]:unknown}>} };

export class HttpTransport implements DesktopOperations {
  private csrf: string | null=null;
  private currentSession: string | null=null;
  private sequence=0;
  private pending: Pending[]=[];
  private storage: Storage | null;
  private subscription: Subscription | null=null;
  private stream: AbortController | null=null;
  private queue: Batch['params']['events']=[];
  private gap=false;
  private ack: {subscription_id:string;cursor:string} | null=null;
  constructor(private origin=window.location.origin,private fetcher:typeof fetch=globalThis.fetch.bind(globalThis)) {
    const url=new URL(origin);
    if(url.protocol!=='http:' || url.hostname!=='127.0.0.1' || !url.port || url.origin!==origin)
      throw new ContractError('HttpTransport requires the trusted loopback origin','POLICY_DENIED');
    this.storage=typeof window==='undefined'?null:window.sessionStorage??null;
    const saved=this.storage?.getItem('forge.pending-actions');
    if(saved) {
      const parsed=strictLoads(saved);
      if(!Array.isArray(parsed)||parsed.length>32||parsed.some(item=>!item||typeof item!=='object'||
        Object.keys(item).sort().join(',')!=='client_action_id,method,payload_hash'||
        !Object.hasOwn(METHODS,item.method)||!/^(act)-[0-9a-f-]{36}$/.test(item.client_action_id)||!/^[0-9a-f]{64}$/.test(item.payload_hash)))
        throw new ContractError('Pending action history is corrupt; reconcile through trusted CLI','INDETERMINATE');
      this.pending=parsed;
    }
  }
  private persist() { this.storage?.setItem('forge.pending-actions',JSON.stringify(this.pending)); }
  private async json(response:Response):Promise<any> {
    if(!response.ok)throw new Error('HTTP transport refused request: '+response.status);
    if(!response.body)throw new Error('HTTP response has no body');
    const reader=response.body.getReader();let length=0;const chunks:Uint8Array[]=[];
    try {
      while(true){const part=await reader.read();if(part.done)break;length+=part.value.byteLength;
        if(length>MAX_FRAME_BYTES)throw new ContractError('HTTP response exceeds frame limit','ARTIFACT_LIMIT');
        chunks.push(part.value);}
    } finally {await reader.cancel();}
    const bytes=new Uint8Array(length);let position=0;
    for(const chunk of chunks){bytes.set(chunk,position);position+=chunk.byteLength;}
    return strictLoads(bytes);
  }
  private async metadata():Promise<Metadata> {
    const value=await this.json(await this.fetcher(this.origin+'/api/v1/session',
      {credentials:'same-origin',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(15000)}));
    validate('system.health.result',value.health);
    if(typeof value.csrf!=='string'||value.csrf.length<40||!['strict','local-trusted'].includes(value.mode)||!['cli','test'].includes(value.profile))
      throw new ContractError('Web session metadata is invalid');
    this.csrf=value.csrf;return value;
  }
  private async wire<T>(method:keyof typeof METHODS,params:unknown):Promise<T> {
    const catalog=METHODS[method];validate(catalog.request_schema,params);
    if(!this.csrf)await this.metadata();
    const id='web-'+(++this.sequence);
    const body=JSON.stringify({jsonrpc:'2.0',id,method,params});
    if(new TextEncoder().encode(body).byteLength>MAX_FRAME_BYTES)throw new ContractError('RPC exceeds frame limit','ARTIFACT_LIMIT');
    const response=await this.json(await this.fetcher(this.origin+'/api/v1/rpc',{method:'POST',credentials:'same-origin',
      cache:'no-store',redirect:'error',signal:AbortSignal.timeout(15000),body,
      headers:{'Content-Type':'application/json','X-Forge-CSRF':this.csrf!,'X-Forge-Nonce':crypto.randomUUID(),'X-Forge-Time':String(Math.floor(Date.now()/1000))}}));
    validate('rpc-response',response);
    if(response.id!==id)throw new ContractError('HTTP RPC response identity mismatch','INDETERMINATE');
    if(response.error)throw new ContractError(response.error.message,response.error.data?.kind??'INVALID_PARAMS',response.error.code);
    validate(catalog.result_schema,response.result);return response.result;
  }
  private async resolve(item:Pending):Promise<any> {
    const action=await this.wire<any>('action.get',{method:item.method,client_action_id:item.client_action_id});
    if(action.payload_hash!==item.payload_hash || action.result_schema!==METHODS[item.method].result_schema)
      throw new ContractError('Durable action payload differs','ACTION_CONFLICT');
    validate(action.result_schema,action.result);
    this.pending=this.pending.filter(value=>value!==item);this.persist();return action.result;
  }
  private async call<T>(method:keyof typeof METHODS,params:unknown):Promise<T> {
    validate(METHODS[method].request_schema,params);
    const action=(params as {client_action_id?:string}).client_action_id;
    if(!METHODS[method].mutation||!action)return this.wire<T>(method,params);
    if(this.pending.length && !['session.cancel_turn','evaluation.cancel'].includes(method))
      throw new ContractError('A previous action needs durable reconciliation before new work','INDETERMINATE');
    if(this.pending.length>=32)throw new ContractError('Pending action quota reached; reconcile in trusted CLI','INDETERMINATE');
    const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonicalString(params)));
    const item={method,client_action_id:action,payload_hash:Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('')};
    if(this.pending.length && !['session.cancel_turn','evaluation.cancel'].includes(method))
      throw new ContractError('Concurrent mutation requires durable reconciliation','INDETERMINATE');
    if(this.pending.length>=32)throw new ContractError('Pending action quota reached','INDETERMINATE');
    this.pending.push(item);this.persist();
    try {
      const result=await this.wire<T>(method,params);
      this.pending=this.pending.filter(value=>value!==item);this.persist();return result;
    } catch(reason) {
      if(reason instanceof ContractError && reason.kind!=='INDETERMINATE') {
        this.pending=this.pending.filter(value=>value!==item);this.persist();throw reason;
      }
      try{return await this.resolve(item);}catch{/* A missing observation cannot prove an in-flight request did not commit. */}
      throw new ContractError('HTTP action outcome is unknown; query durable action and snapshot','INDETERMINATE');
    }
  }
  private nativeRequired():Promise<never> {
    return Promise.reject(new ContractError('请通过桌面或可信 CLI 完成此操作。','DESKTOP_OR_CLI_APPROVAL_REQUIRED',-32010));
  }
  async status():Promise<DesktopStatus> {
    const value=await this.metadata();
    for(const item of [...this.pending]){try{await this.resolve(item);}catch{/* Remain visibly unresolved. */}}
    return {engine_state:'ready',readiness:value.health.readiness,mode:value.profile==='test'?'web-scripted':'web-'+value.mode,
      session_id:this.currentSession,failure:this.pending.length?'Previous action outcome requires reconciliation':null};
  }
  projects() {return this.call<Output<'projects'>>('workspace.list',{limit:100});}
  approvals() {return this.call<Output<'approvals'>>('approval.list',{scope:{kind:'all'},limit:100});}
  async connections():Promise<ConnectionPage> {
    const metadata=await this.metadata();
    const page=await this.call<{items:ConnectionPage['items']}>('connection.list',{limit:100});
    return {items:page.items,protection:metadata.credential_storage};
  }
  session(id:string) {return this.call<Output<'session'>>('session.get',{session_id:id});}
  async sessionSnapshot(value:Input<'sessionSnapshot'>) {
    const snapshot=await this.call<Output<'sessionSnapshot'>>('session.snapshot',value);
    this.currentSession=snapshot.session.session_id;
    await this.subscribe(snapshot.event_cursor);return snapshot;
  }
  async createSession(value:Input<'createSession'>) {
    const result=await this.call<Output<'createSession'>>('session.create_default',value);
    this.currentSession=result.session_id;return result;
  }
  submit(value:Input<'submit'>) {return this.call<Output<'submit'>>('session.submit',value);}
  sessions(value: Input<'sessions'>) { return this.call<Output<'sessions'>>('session.list',value); }
  recoveryInspect(value: Input<'recoveryInspect'>) { return this.call<Output<'recoveryInspect'>>('recovery.inspect',value); }
  files(value: Input<'files'>) { return this.call<Output<'files'>>('workspace.files',value); }
  readProjectFile(value: Input<'readProjectFile'>) { return this.call<Output<'readProjectFile'>>('workspace.read_file',value); }
  changes(value: Input<'changes'>) { return this.call<Output<'changes'>>('workspace.changes',value); }
  diff(value: Input<'diff'>) { return this.call<Output<'diff'>>('workspace.diff',value); }
  diffFile(value: Input<'diffFile'>) { return this.call<Output<'diffFile'>>('workspace.diff_file',value); }
  observationSpans(value: Input<'observationSpans'>) { return this.call<Output<'observationSpans'>>('observability.spans',value); }
  observationContext(value: Input<'observationContext'>) { return this.call<Output<'observationContext'>>('observability.context',value); }
  observationEvidence(value: Input<'observationEvidence'>) { return this.call<Output<'observationEvidence'>>('observability.evidence',value); }
  observationUsage(value: Input<'observationUsage'>) { return this.call<Output<'observationUsage'>>('observability.usage',value); }
  observationEvents(value: Input<'observationEvents'>) { return this.call<Output<'observationEvents'>>('observability.events',value); }
  observationOutput(value: Input<'observationOutput'>) { return this.call<Output<'observationOutput'>>('observability.output',value); }
  observationTimings(value: Input<'observationTimings'>) { return this.call<Output<'observationTimings'>>('observability.timings',value); }
  evaluationTemplate(value: Input<'evaluationTemplate'>) { return this.call<Output<'evaluationTemplate'>>('evaluation.template',value); }
  evaluationDraft(value: Input<'evaluationDraft'>) { return this.call<Output<'evaluationDraft'>>('evaluation.draft',value); }
  evaluationValidate(value: Input<'evaluationValidate'>) { return this.call<Output<'evaluationValidate'>>('evaluation.validate',value); }
  evaluationCreate(value: Input<'evaluationCreate'>) { return this.call<Output<'evaluationCreate'>>('evaluation.create_run',value); }
  evaluationStart(value: Input<'evaluationStart'>) { return this.call<Output<'evaluationStart'>>('evaluation.start',value); }
  evaluationCancel(value: Input<'evaluationCancel'>) { return this.call<Output<'evaluationCancel'>>('evaluation.cancel',value); }
  evaluationRetry(value: Input<'evaluationRetry'>) { return this.call<Output<'evaluationRetry'>>('evaluation.retry',value); }
  evaluationRuns(value: Input<'evaluationRuns'>) { return this.call<Output<'evaluationRuns'>>('evaluation.list',value); }
  evaluationSnapshot(value: Input<'evaluationSnapshot'>) { return this.call<Output<'evaluationSnapshot'>>('evaluation.snapshot',value); }
  evaluationComparison(value: Input<'evaluationComparison'>) { return this.call<Output<'evaluationComparison'>>('evaluation.comparison',value); }
  failureList(value: Input<'failureList'>) { return this.call<Output<'failureList'>>('failure.list',value); }
  failureGet(value: Input<'failureGet'>) { return this.call<Output<'failureGet'>>('failure.get',value); }
  failureAnnotate(value: Input<'failureAnnotate'>) { return this.call<Output<'failureAnnotate'>>('failure.annotate',value); }
  failureSaveCandidate(value: Input<'failureSaveCandidate'>) { return this.call<Output<'failureSaveCandidate'>>('failure.save_candidate',value); }
  failureCheckReproduction(value: Input<'failureCheckReproduction'>) { return this.call<Output<'failureCheckReproduction'>>('failure.check_reproduction',value); }
  failureCandidate(value: Input<'failureCandidate'>) { return this.call<Output<'failureCandidate'>>('failure.candidate',value); }
  evaluationTemplates() {return this.call<Output<'evaluationTemplates'>>('evaluation.templates',{});}
  diagnostics() {return this.call<Output<'diagnostics'>>('system.health',{});}
  cancelTurn(id:string) {return this.call<unknown>('session.cancel_turn',{turn_id:id,client_action_id:'act-'+crypto.randomUUID(),reason:'Web user cancelled'});}
  async artifactChunk(value:Input<'artifactChunk'>):Promise<Output<'artifactChunk'>> {
    validate('artifact.read_chunk.request',value);
    const result=await this.json(await this.fetcher(this.origin+'/api/v1/artifacts/'+encodeURIComponent(value.artifact_id)+
      '?offset='+value.offset+'&length='+value.length,{credentials:'same-origin',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(15000)}));
    validate('artifact.read_chunk.result',result);return result;
  }
  selectProject(..._args: Parameters<DesktopOperations['selectProject']>): Promise<never> { return this.nativeRequired(); }
  authorizeWorkspace(..._args: Parameters<DesktopOperations['authorizeWorkspace']>): Promise<never> { return this.nativeRequired(); }
  requestApproval(..._args: Parameters<DesktopOperations['requestApproval']>): Promise<never> { return this.nativeRequired(); }
  saveConnection(..._args: Parameters<DesktopOperations['saveConnection']>): Promise<never> { return this.nativeRequired(); }
  deleteConnection(..._args: Parameters<DesktopOperations['deleteConnection']>): Promise<never> { return this.nativeRequired(); }
  lockConnection(..._args: Parameters<DesktopOperations['lockConnection']>): Promise<never> { return this.nativeRequired(); }
  unlockConnection(..._args: Parameters<DesktopOperations['unlockConnection']>): Promise<never> { return this.nativeRequired(); }
  testConnection(..._args: Parameters<DesktopOperations['testConnection']>): Promise<never> { return this.nativeRequired(); }
  exportRegressionCandidate(..._args: Parameters<DesktopOperations['exportRegressionCandidate']>): Promise<never> { return this.nativeRequired(); }
  importExperimentPlan(..._args: Parameters<DesktopOperations['importExperimentPlan']>): Promise<never> { return this.nativeRequired(); }
  exportExperimentPlan(..._args: Parameters<DesktopOperations['exportExperimentPlan']>): Promise<never> { return this.nativeRequired(); }
  importResults(..._args: Parameters<DesktopOperations['importResults']>): Promise<never> { return this.nativeRequired(); }
  exportResults(..._args: Parameters<DesktopOperations['exportResults']>): Promise<never> { return this.nativeRequired(); }
  diagnoseSandbox(..._args: Parameters<DesktopOperations['diagnoseSandbox']>): Promise<never> { return this.nativeRequired(); }
  installSandbox(..._args: Parameters<DesktopOperations['installSandbox']>): Promise<never> { return this.nativeRequired(); }
  startDemo(..._args: Parameters<DesktopOperations['startDemo']>): Promise<never> { return this.nativeRequired(); }
  private async subscribe(cursor?:string) {
    if(!this.subscription){
      try{this.subscription=await this.call<Subscription>('events.subscribe',{scope:{kind:'all'},...(cursor?{after_cursor:cursor}:{})});}
      catch(reason){
        if(!(reason instanceof ContractError)||reason.kind!=='INVALID_CURSOR')throw reason;
        this.gap=true;this.subscription=await this.call<Subscription>('events.subscribe',{scope:{kind:'all'}});
      }
      this.gap ||= this.subscription.history_gap;
    }
    if(!this.stream){
      const controller=new AbortController();this.stream=controller;
      void this.pump(controller).catch(()=>{if(!controller.signal.aborted)this.gap=true;}).finally(()=>{if(this.stream===controller)this.stream=null;});
    }
  }
  private async pump(controller:AbortController) {
    const subscription=this.subscription!;
    const response=await this.fetcher(this.origin+'/api/v1/events?subscription_id='+encodeURIComponent(subscription.subscription_id),
      {credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal});
    if(!response.ok || response.headers.get('Content-Type')?.split(';')[0]!=='text/event-stream'||!response.body)
      throw new Error('Authenticated SSE unavailable');
    const reader=response.body.getReader(),decoder=new TextDecoder('utf-8',{fatal:true});let buffer='';
    try {
      while(true) {
        const chunk=await reader.read();if(chunk.done)throw new Error('SSE disconnected');
        buffer+=decoder.decode(chunk.value,{stream:true});
        let boundary:number;
        while((boundary=buffer.indexOf('\n\n'))>=0) {
          const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);
          if(new TextEncoder().encode(frame).byteLength>65542)throw new ContractError('SSE frame exceeds limit');
          if(frame.startsWith(':'))continue;
          if(!frame.startsWith('data: ')||frame.includes('\n'))throw new ContractError('Invalid SSE frame');
          const batch=strictLoads(frame.slice(6)) as Batch;validate('event-notification',batch);
          if(batch.params.subscription_id!==subscription.subscription_id)throw new ContractError('SSE scope changed');
          for(const event of batch.params.events) {
            validateEvent(event);
            if(!this.queue.some(item=>item.event_id===event.event_id))this.queue.push(event);
            if(this.queue.length>128){this.queue.shift();this.gap=true;}
          }
          this.ack={subscription_id:subscription.subscription_id,cursor:batch.params.cursor};
        }
        if(new TextEncoder().encode(buffer).byteLength>65542)throw new ContractError('SSE buffer exceeds limit');
      }
    }finally{await reader.cancel();}
  }
  async events():Promise<Output<'events'>> {
    // The UI consumed the previous return before this acknowledgement advances replay.
    if(this.ack && !this.queue.length){
      const ack=this.ack;
      try{await this.call('events.ack',ack);if(this.ack===ack)this.ack=null;}
      catch(reason){
        if(!(reason instanceof ContractError)||!['INVALID_CURSOR','NOT_FOUND'].includes(reason.kind))throw reason;
        await this.close();this.gap=true;
        if(this.currentSession)await this.sessionSnapshot({session_id:this.currentSession});
      }
    }
    await this.subscribe();
    const events=this.queue.splice(0),gap=this.gap;this.gap=false;
    return {events,gap};
  }
  async close() {
    const stream=this.stream;this.stream=null;stream?.abort();
    if(this.subscription)await this.call('events.unsubscribe',{subscription_id:this.subscription.subscription_id});
    this.subscription=null;this.queue=[];this.ack=null;
  }
}
