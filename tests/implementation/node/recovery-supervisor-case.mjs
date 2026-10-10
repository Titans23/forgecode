import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { loadDevelopmentEngine } from '../../../apps/desktop/dist/main/assets.js';
import { EngineSupervisor } from '../../../apps/desktop/dist/main/supervisor.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const input = JSON.parse(await readFile(process.argv[2], 'utf8'));
const launch = await loadDevelopmentEngine(root, {dataDir:resolve(input.directory,'data'),profile:'test',fixture:input.fixture});
const engine = new EngineSupervisor(launch,{responseTimeoutMs:input.mode==='unconfirmed-limit'?15000:500});
try {
  await engine.start();
  const pid = engine.pid;
  if(input.mode==='main-crash') {
    const accepted=await engine.call('session.start_turn',input.turn);
    while(!(await engine.call('session.snapshot',{session_id:input.turn.session_id})).messages.length) await new Promise(r=>setTimeout(r,10));
    process.stdout.write(JSON.stringify({mode:input.mode,turn_id:accepted.turn_id,actual_main_pid:process.pid,actual_engine_pid:pid})+'\n');
    await new Promise(()=>{});
  } else if(input.mode==='unconfirmed-limit') {
    const accepted=await engine.call('session.start_turn',input.turn);
    engine.options.responseTimeoutMs=100;
    const receive=engine.receive.bind(engine);let bytes=Buffer.alloc(0);
    engine.receive=function(chunk) {
      bytes=Buffer.concat([bytes,chunk]);
      for(let end;(end=bytes.indexOf(10))>=0;) {
        const frame=bytes.subarray(0,end+1);bytes=bytes.subarray(end+1);
        const value=JSON.parse(frame),method=engine.pending.get(value.id)?.method??engine.expired.get(value.id);
        if(!['session.cancel_turn','action.get'].includes(method)) receive(frame);
      }
    };
    for(let i=0;i<33;i++) await assert.rejects(engine.call('session.cancel_turn',{
      turn_id:accepted.turn_id,client_action_id:'act-'+randomUUID(),reason:'Bound actual unconfirmed cancellation responses'}),/remains unconfirmed/);
    assert.equal(engine.unconfirmed.size,32);
    assert.equal(engine.unconfirmedOverflow,true);
    await assert.rejects(engine.call('session.start_turn',{...input.turn,client_action_id:'act-'+randomUUID()}),/limit exceeded/);
    engine.receive=receive;
    assert.equal((await engine.call('session.get',{session_id:input.turn.session_id})).turns.length,1);
  } else if(input.mode==='unknown-response') {
    engine.receive(Buffer.from(JSON.stringify({jsonrpc:'2.0',id:'not-owned',result:{}})+'\n'));
    assert.equal(engine.state,'engine_lost');
    await assert.rejects(engine.start(),/already owned/);
  } else {
    // Inject loss only at Main's receive boundary. All responses come from the real Python Engine.
    const receive = engine.receive.bind(engine);
    let bytes=Buffer.alloc(0),held=null;
    const expected=String(engine.sequence+1);
    engine.receive=function(chunk) {
      bytes=Buffer.concat([bytes,chunk]);
      for(let end;(end=bytes.indexOf(10))>=0;) {
        const frame=bytes.subarray(0,end+1);bytes=bytes.subarray(end+1);
        const value=JSON.parse(frame);
        if(value.id===expected) held=frame;
        else if(input.mode==='double-response-loss'&&value.id===String(Number(expected)+1)) {} // Lose the first durable query response too.
        else receive(frame);
      }
    };
    let accepted;
    if(input.mode==='double-response-loss') {
      await assert.rejects(engine.call('session.start_turn',input.turn),/remains unconfirmed/);
      // Cold Harness imports may still be completing. Reconciliation remains readonly.
      engine.options.responseTimeoutMs=15000;
      accepted=await engine.call('session.start_turn',{...input.turn,client_action_id:'act-'+randomUUID()});
    } else {
      const pending=engine.call('session.start_turn',input.turn);
      // Only the deliberately lost response gets the short timer. Durable,
      // readonly reconciliation keeps the supervisor's normal bounded deadline.
      engine.options.responseTimeoutMs=15000;
      accepted=await pending;
    }
    assert.ok(held);
    const action=await engine.call('action.get',{method:'session.start_turn',client_action_id:input.turn.client_action_id});
    assert.equal(action.result.turn_id,accepted.turn_id);
    if(input.mode==='late-response') {
      receive(held);assert.equal(engine.lateResponses,1);
    }
    assert.equal(engine.state,'ready');assert.equal(engine.pid,pid);
    const retry=await engine.call('session.start_turn',input.turn);
    assert.equal(retry.turn_id,accepted.turn_id);
    const session=await engine.call('session.get',{session_id:input.turn.session_id});
    assert.equal(session.turns.length,1);
    engine.detachRenderer();engine.attachRenderer();
    assert.equal(engine.pid,pid);
    assert.equal((await engine.refreshHealth()).engine_epoch,engine.hello.engine_epoch);
  }
  process.stdout.write(JSON.stringify({mode:input.mode,actual_engine_pid:pid,late_responses:engine.lateResponses,no_mutation_replay:true}));
} finally {
  await engine.shutdown('cancel');
}
