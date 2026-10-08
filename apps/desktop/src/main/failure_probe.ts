/** Real development client/Engine storage; blocked native executor is kept visible. */
import type {BrowserWindow} from 'electron';
import type {EngineSupervisor} from './supervisor.js';
import {writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import type {EvaluationListResult,EvaluationSnapshotResult,FailureGetResult} from '@forgecode/contracts';
import {probeLayout} from './layout_probe.js';

export async function probeFailures(window:BrowserWindow,engine:EngineSupervisor,output:string) {
  const checks:any[]=[],check=(id:string,passed:boolean,details:unknown={})=>checks.push({id,status:passed?'pass':'fail',details});
  const js=<T>(code:string)=>window.webContents.executeJavaScript(code) as Promise<T>;
  async function wait(code:string){const until=Date.now()+15000;while(Date.now()<until){if(await js<boolean>(code))return;await new Promise(r=>setTimeout(r,30));}throw Error('Failure UI wait expired: '+code);}
  async function click(label:string){await js(`(()=>{const b=Array.from(document.querySelectorAll('button')).find(x=>x.textContent===${JSON.stringify(label)});if(!b||b.disabled)throw Error('Button unavailable');b.click();})()`);}
  try {
    const run=(await engine.call('evaluation.list',{})).items.find((r:EvaluationListResult['items'][number])=>r.origin==='local'&&r.dataset==='desktop-protocol-fixture')!;
    await engine.call('evaluation.start',{run_id:run.run_id,client_action_id:'act-'+randomUUID()});
    const until=Date.now()+10000;let attempt:string|undefined;
    while(Date.now()<until){const value=await engine.call('evaluation.snapshot',{run_id:run.run_id});attempt=value.attempts.find((a:EvaluationSnapshotResult['attempts'][number])=>['blocked','error','finished'].includes(a.execution_state))?.id;if(attempt)break;await new Promise(r=>setTimeout(r,50));}
    if(!attempt)throw Error('Actual blocked evaluation attempt unavailable');
    const original=await engine.call('failure.get',{run_id:run.run_id,attempt_id:attempt});
    check('failure-rules-never-write-cause',original.annotations.length===0&&original.suggestions.every((s:FailureGetResult['suggestions'][number])=>s.authority==='suggestion_only'));
    await click('失败案例');await wait(`Array.from(document.querySelectorAll('select option')).some(o=>o.value===${JSON.stringify(run.run_id)})`);
    await js(`(()=>{const e=Array.from(document.querySelectorAll('label')).find(x=>x.textContent?.startsWith('实验'))?.querySelector('select');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,${JSON.stringify(run.run_id)});e.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await wait("document.querySelector('[aria-label=\"真实失败尝试\"] button')!==null");
    await js("document.querySelector('[aria-label=\"真实失败尝试\"] button').click()");
    await wait("Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='保存人工标注版本'&&!b.disabled)");
    check('failure-actual-blocked-client-detail',await js<boolean>("document.body.textContent.includes('未评分')&&document.body.textContent.includes('规则只提出相关性建议')"));
    await click('保存人工标注版本');await wait("document.querySelectorAll('.failures article').length===1");
    await js("(()=>{const e=document.querySelector('.failures textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,'Correction with preserved original');e.dispatchEvent(new Event('input',{bubbles:true}));})()");
    await wait("Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='保存人工标注版本'&&!b.disabled)");await click('保存人工标注版本');await wait("document.querySelectorAll('.failures article').length===2");
    const labels=await engine.call('failure.get',{run_id:run.run_id,attempt_id:attempt});
    check('failure-human-version-history',labels.annotations.length===2&&labels.annotations[1].supersedes===labels.annotations[0].id&&!!labels.annotations[0].created_at&&labels.annotations[1].note==='Correction with preserved original');
    await wait("Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='保存脱敏回归候选'&&!b.disabled)");await click('保存脱敏回归候选');await wait("document.body.textContent.includes('复现 blocked')");
    const detail=await engine.call('failure.get',{run_id:run.run_id,attempt_id:attempt});
    check('failure-saved-candidate-not-fake-reproduction',detail.candidates.length===1&&detail.candidates[0].fixture.available&&detail.candidates[0].status==='blocked'&&detail.candidates[0].blockers.includes('no_original_independent_failure'));
    check('failure-native-candidate-export-entry',await js<boolean>("Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='导出回归候选'&&!b.disabled)"));
    checks.push(...await probeLayout(window,output,'failures'));
    await js("new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>{document.querySelector('.failures section:last-child')?.scrollIntoView({block:'end'});r();})))");
    await new Promise(resolve=>setTimeout(resolve,200));
    await writeFile(join(output,'failures.png'),(await window.webContents.capturePage()).toPNG());
  }catch(error){check('failure-probe-completed',false,{message:(error as Error).message});}
  return checks;
}
