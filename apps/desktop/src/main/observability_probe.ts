/** Development-only tests operate actual rendered UI, private Engine and owned fixture files. */
import type { BrowserWindow } from 'electron';
import { readFile, realpath, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import type { EngineSupervisor } from './supervisor.js';

export async function probeObservability(window:BrowserWindow,engine:EngineSupervisor,sessionId:string,directory:string,output:string){
  const checks:Array<{id:string;status:string}>=[],check=(id:string,passed:boolean)=>checks.push({id,status:passed?'pass':'fail'});
  const js=(code:string)=>window.webContents.executeJavaScript(code,true);
  async function until(code:string){const deadline=Date.now()+15000;while(Date.now()<deadline){if(await js(code))return;await new Promise(r=>setTimeout(r,50));}throw new Error('Observability UI condition: '+code);}
  const snapshot=await engine.call('session.snapshot',{session_id:sessionId}),turnId=snapshot.turn_id;
  const message=snapshot.messages.find((m:any)=>m.tool_name==='verify'&&m.status==='completed'&&m.execution_id);
  if(!message)throw new Error('Actual completed verify execution is missing');
  const usage=await engine.call('observability.usage',{scope:{kind:'turn',id:turnId}});
  async function open(){
    await js(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Agent 工作区').click()`);
    await until(`!!Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes(${JSON.stringify(sessionId.slice(-8))}))`);
    await js(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes(${JSON.stringify(sessionId.slice(-8))})).click()`);
    await until(`!!document.querySelector('[data-message-sequence="${message.sequence}"]')`);
    await js(`document.querySelector('[data-message-sequence="${message.sequence}"]').click()`);
    await until(`!!document.querySelector('[data-testid=tool-trace-link]')`);
    await js(`document.querySelector('[data-testid=tool-trace-link]').click()`);
    await until(`document.querySelector('[data-testid=selected-span]')?.textContent.includes(${JSON.stringify(message.execution_id)})||document.querySelector('[data-testid=selected-span]')&&document.querySelector('[data-span-id]')`);
  }
  await open();
  const exact=await engine.call('observability.spans',{scope:{kind:'turn',id:turnId},execution_id:message.execution_id});
  check('gui-tool-card-exact-execution-navigation',await js(`document.querySelector('[data-testid=selected-span]').textContent.includes(${JSON.stringify(exact.items[0].span_id)})`));
  await until(`!!document.querySelector('[data-testid=observation-usage]')`);
  check('gui-authoritative-unknown-cost-quality',await js(`document.querySelector('[data-testid=observation-usage]').textContent.includes('未知费用请求 ${usage.unknown_requests}')`));
  await js(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='按需读取工具输出').click()`);
  await until(`!!document.querySelector('.observed-output')`);
  check('gui-lazy-actual-redacted-tool-output',await js(`Array.from(document.querySelectorAll('.observed-output')).some(p=>p.textContent.includes('OK'))&&!document.querySelector('[data-testid=selected-span]').textContent.includes('scripted-test-only')`));
  check('gui-no-model-debug-or-raw-script',await js(`!document.querySelector('[data-testid=selected-span]').textContent.includes('command":')&&typeof window.forgeDesktop.readDebugFile==='undefined'&&typeof window.forgeDesktop.invoke==='undefined'`));
  const file=resolve(directory,'project/calculator.py');if(await realpath(file)!==file)throw new Error('Owned smoke fixture identity changed');
  const original=await readFile(file);
  try{
    await writeFile(file,Buffer.concat([original,Buffer.from('\n# external observation edit\n')]));
    await js(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='重新核对全部视图').click()`);
    await until(`!!Array.from(document.querySelectorAll('[data-evidence-id]')).find(b=>b.textContent.includes('workspace_content_changed_since_verification'))`);
    await js(`Array.from(document.querySelectorAll('[data-evidence-id]')).find(b=>b.textContent.includes('workspace_content_changed_since_verification')).click()`);
    const evidence=await engine.call('observability.evidence',{scope:{kind:'turn',id:turnId}}),comparison=evidence.items.find((e:any)=>e.reason==='workspace_content_changed_since_verification').metadata.workspace_comparison;
    check('gui-stale-evidence-actual-content-revision-difference',comparison.current.content_revision>comparison.observed.content_revision&&await js(`document.querySelector('[data-testid=evidence-comparison]').textContent.includes('当前内容版本 ${comparison.current.content_revision}')`));
    check('gui-separated-client-engine-harness-timings',await js(`document.querySelector('[data-testid=observation-timings]').textContent.includes('含排队与清理')&&document.querySelector('[data-testid=observation-timings]').textContent.includes('Harness 执行')`));
    await writeFile(resolve(output,'observability.png'),(await window.webContents.capturePage()).toPNG());
  }finally{if(await realpath(file)!==file)throw new Error('Fixture path changed');await writeFile(file,original);}
  const loaded=new Promise<void>(r=>window.webContents.once('did-finish-load',()=>r()));window.webContents.reload();await loaded;
  await until(`!!Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Agent 工作区')`);await open();
  const after=await engine.call('observability.usage',{scope:{kind:'turn',id:turnId}});
  check('gui-reload-ledger-does-not-double-cost',JSON.stringify(after)===JSON.stringify(usage));
  check('gui-trace-list-bounded-dom',await js(`Array.from(document.querySelectorAll('.virtual-list')).every(list=>list.querySelectorAll('[data-virtual-row]').length<=20)`));
  return checks;
}
