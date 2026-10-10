import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {rolldown} from 'rolldown';
import {loadDevelopmentEngine} from '../../../apps/desktop/dist/main/assets.js';
import {EngineSupervisor} from '../../../apps/desktop/dist/main/supervisor.js';
import {validate} from '@forgecode/contracts';

let inputText='';for await(const chunk of process.stdin)inputText+=chunk;
const input=JSON.parse(inputText),root=resolve(dirname(fileURLToPath(import.meta.url)),'../../..');
async function moduleFile(source,name) {
  const bundle=await rolldown({input:resolve(root,source),platform:'node',external:['@forgecode/contracts/browser']});
  const result=await bundle.generate({format:'esm'});await bundle.close();
  let code=result.output[0].code;
  code=code.replaceAll("'@forgecode/contracts/browser'",JSON.stringify(pathToFileURL(resolve(root,'packages/contracts/dist/browser.js')).href));
  code=code.replaceAll('"@forgecode/contracts/browser"',JSON.stringify(pathToFileURL(resolve(root,'packages/contracts/dist/browser.js')).href));
  const path=resolve(input.directory,name+'.mjs');await writeFile(path,code,'utf8');return import(pathToFileURL(path).href);
}
const {DesktopTransport}=await moduleFile('packages/ui/src/transport.ts','desktop-transport');
const {HttpTransport}=await moduleFile('packages/ui/src/http_transport.ts','http-transport');
const {mergeMessages}=await moduleFile('packages/ui/src/state/messages.ts','messages');
const launch=await loadDevelopmentEngine(root,{dataDir:resolve(input.desktop,'data'),profile:'test',fixture:input.fixture});
const engine=new EngineSupervisor(launch);let http;
try {
  await engine.start();engine.attachRenderer();let desktopSubscription;
  // Named Main business calls return actual Engine DTOs. Native IPC itself is covered by the Electron suite.
  globalThis.window={forgeDesktop:{
    status:async()=>({engine_state:engine.state,readiness:engine.hello.readiness,mode:'offline-demo',session_id:input.desktopTurn.session_id,failure:null}),
    projects:()=>engine.call('workspace.list',{limit:100}),
    submit:value=>engine.call('session.submit',value),
    sessionSnapshot:async value=>{
      const result=await engine.call('session.snapshot',value);
      if(!desktopSubscription)desktopSubscription=await engine.call('events.subscribe',{scope:{kind:'all'},after_cursor:result.event_cursor});
      return result;
    },
    events:async()=>engine.events()
  }};
  const desktop=new DesktopTransport();
  const logged=await fetch(input.origin+'/api/v1/login',{method:'POST',redirect:'manual',
    headers:{Origin:input.origin,'Content-Type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams({credential:input.credential})});
  assert.equal(logged.status,303);
  const cookie=logged.headers.get('set-cookie').split(';')[0];let dropped=false;let snapshotCalls=0;let expiredAckRejected=false;
  const wire=async(url,options={})=>{
    assert.equal(new URL(url).origin,input.origin);
    const headers=new Headers(options.headers);headers.set('Cookie',cookie);headers.set('Origin',input.origin);
    const method=options.method==='POST'?JSON.parse(options.body).method:null;
    if(method==='session.snapshot')snapshotCalls++;
    const result=await fetch(url,{...options,headers});
    if(method==='events.ack'){
      const response=await result.clone().json();
      expiredAckRejected ||= response.error?.data?.kind==='INVALID_CURSOR';
    }
    if(method==='session.submit' && !dropped){
      const accepted=await result.clone().json();
      if(accepted.error)process.stderr.write(JSON.stringify({original_submit_error:accepted.error})+'\n');
      assert.ok(accepted.result,JSON.stringify(accepted.error));
      dropped=true;await result.body.cancel();throw new TypeError('Owned test dropped the committed HTTP response');
    }
    return result;
  };
  http=new HttpTransport(input.origin,wire);
  async function task(transport,turn) {
    await transport.sessionSnapshot({session_id:turn.session_id});
    const accepted=await transport.submit({session_id:turn.session_id,client_action_id:turn.client_action_id,input:turn.input});
    validate('session.submit.result',accepted);
    let snapshot;const events=[];const until=Date.now()+15000;
    do {
      snapshot=await transport.sessionSnapshot({session_id:turn.session_id});
      validate('session.snapshot.result',snapshot);
      // Consume and acknowledge as the real UI does; a slow turn must not
      // accidentally exercise expiry before the explicit expiry phase.
      // Keep the terminal batch unacknowledged for the explicit expiry phase.
      if(!events.some(value=>value.event_type==='turn.finished'))
        events.push(...(await transport.events()).events);
      if(snapshot.turns[0]?.state==='finished')break;
      await new Promise(resolve=>setTimeout(resolve,30));
    }while(Date.now()<until);
    assert.equal(snapshot.turns[0].outcome,'completed');
    const messages=mergeMessages([],snapshot.messages);
    assert.deepEqual(mergeMessages(messages,snapshot.messages),messages);
    for(let index=0;index<30&&!events.some(value=>value.event_type==='turn.finished');index++){
      events.push(...(await transport.events()).events);
      if(events.some(value=>value.event_type==='turn.finished'))break;
      await new Promise(resolve=>setTimeout(resolve,30));
    }
    assert.ok(events.some(value=>value.event_type==='turn.finished'));
    return {outcome:snapshot.turns[0].outcome,messages:messages.map(({kind,text,tool_name,status})=>({kind,text,tool_name,status}))};
  }
  const desktopResult=await task(desktop,input.desktopTurn);
  const httpResult=await task(http,input.httpTurn);
  assert.deepEqual(httpResult,desktopResult);
  assert.ok(dropped);
  await assert.rejects(http.authorizeWorkspace(input.workspaceId),error=>error.kind==='DESKTOP_OR_CLI_APPROVAL_REQUIRED');
  let expiredAckRecovered=false;
  if(input.exerciseExpiry){
    const previousSnapshots=snapshotCalls;expiredAckRejected=false;
    await new Promise(resolve=>setTimeout(resolve,4500));
    const recovered=await http.events();
    assert.ok(expiredAckRejected,'The real server must reject the deliberately expired ack');
    assert.ok(recovered.gap && snapshotCalls>previousSnapshots,'Expired ack must rebuild the actual snapshot');
    const second=await http.submit({session_id:input.httpTurn.session_id,client_action_id:'act-'+crypto.randomUUID(),input:input.httpTurn.input});
    const deadline=Date.now()+10000;let future=[];
    do{
      future.push(...(await http.events()).events);
      if(future.some(value=>value.turn_id===second.turn_id&&value.event_type==='turn.finished'))break;
      await new Promise(resolve=>setTimeout(resolve,30));
    }while(Date.now()<deadline);
    assert.ok(future.some(value=>value.turn_id===second.turn_id&&value.event_type==='turn.finished'),'Renewed subscription must deliver real future events');
    const final=await http.sessionSnapshot({session_id:input.httpTurn.session_id});
    assert.equal(final.turns.find(value=>value.turn_id===second.turn_id).outcome,'completed');
    expiredAckRecovered=true;
  }
  process.stdout.write(JSON.stringify({same_dtos:true,same_reducer:true,actual_task_outcome:httpResult.outcome,
    lost_mutation_reply_reconciled:true,native_approval_required:true,expired_ack_recovered:expiredAckRecovered,eligible_for_native_pass:false,model_origin:'scripted'}));
}finally{
  if(http)await http.close();
  await engine.shutdown('cancel');
}
