/** Trusted development probe: real Engine/React/SQLite; protocol fixture, no model score. */
import type {BrowserWindow} from 'electron';
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import type {EngineSupervisor} from './supervisor.js';
import type {EvaluationListResult} from '@forgecode/contracts';
import {probeLayout} from './layout_probe.js';

export async function probeEvaluations(window:BrowserWindow,engine:EngineSupervisor,root:string,directory:string,output:string) {
  const checks:any[]=[];const check=(id:string,passed:boolean,details:unknown={})=>checks.push({id,status:passed?'pass':'fail',details});
  const js=<T>(code:string)=>window.webContents.executeJavaScript(code) as Promise<T>;
  async function wait(code:string){const deadline=Date.now()+15000;while(Date.now()<deadline){if(await js<boolean>(code))return;await new Promise(r=>setTimeout(r,30));}throw Error('Evaluation UI wait expired: '+code);}
  async function click(label:string){await js(`(()=>{const b=Array.from(document.querySelectorAll('button')).find(x=>x.textContent===${JSON.stringify(label)});if(!b||b.disabled)throw Error('Button unavailable');b.click();})()`);}
  async function setSelect(label:string,value:string){await js(`(()=>{const l=Array.from(document.querySelectorAll('label')).find(x=>x.textContent?.startsWith(${JSON.stringify(label)}));const e=l?.querySelector('select');if(!e)throw Error('Select unavailable');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('change',{bubbles:true}));})()`);}
  try {
    const spec=JSON.parse(await readFile(join(root,'contracts/v1/examples/run-spec.valid.json'),'utf8'));
    spec.experiment_id='experiment-'+randomUUID();spec.dataset={name:'desktop-protocol-fixture',revision:'offline-protocol-v1',task_ids:['client-a','client-b'],task_revisions:{'client-a':'fixture-v1','client-b':'fixture-v1'}};
    spec.execution.target_platform='official-environment';spec.budget.attempt_wall_seconds=30;spec.budget.trial_wall_seconds=60;
    const environment={platform:'official-environment',os_build:'unverified protocol fixture',architecture:'x64',filesystem:'unverified',toolchains:[],backend_version:'unavailable'};
    const capabilities={platform:'unsupported',backend:'unavailable',backend_version:'unavailable',read_isolation:'unavailable',write_isolation:false,direct_network_isolation:false,dns_isolation:false,socket_isolation:false,process_cleanup:false,
      resource_enforcement:{memory:'unavailable',disk:'unavailable',pids:'unavailable'},readiness:'unavailable',issues:['offline client protocol fixture; no sandbox capability claimed'],measured_at_utc:new Date().toISOString()};
    const policy={schema_version:'forge.sandbox.policy.v1',policy_id:'policy-'+randomUUID(),workspace_id:'ws-'+randomUUID(),filesystem:{read_mode:'backend_default_with_protected_paths',read_roots:[directory],write_roots:[directory],protected_paths:[],deny_overrides_allow:true,reject_unsafe_links:true},
      network:{mode:'deny_direct',allowed_domains:[],dns_isolation_required:false},limits:{memory_bytes:null,disk_bytes:null,pids:null,wall_time_seconds:30,command_output_bytes:1048576,session_artifact_bytes:104857600},environment_keys:[],fallback:'deny',session_mutation:'replace_session'};
    const values:any={source:{origin:'offline-desktop-protocol-fixture',files:{}},model_parameters:{temperature:null,top_p:null,max_output_tokens:1024,reasoning_effort:null},
      harness:{max_context_tokens:4096,compaction_enabled:true,max_delivery_repairs:0,parent_budget:{max_model_calls:10,max_tool_calls:20,wall_seconds:30},explore_enabled:true,trusted_extensions_enabled:false},
      environment,policy,capabilities,network_cache:{network_mode:'deny_direct',allowed_domains:[],cache_mode:'cold',cache_snapshot:null},
      grader:{adapter_id:'fixture-assertions',revision:'v1',artifact_types:[],timeout_seconds:10,parameters:{}},grader_environment:environment,
      pricing:{revision:'unpriced-fixture-v1',currency:'USD',source:'offline protocol; no real provider prices supplied',rates:[]}};
    const canonical=(value:any):string=>Array.isArray(value)?'['+value.map(canonical).join(',')+']':value&&typeof value==='object'?'{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical(value[k])).join(',')+'}':JSON.stringify(value);
    const hash=(value:any)=>createHash('sha256').update(canonical(value)).digest('hex');
    const references:any={source:['source','source_snapshot'],model_parameters:['model','parameters'],harness:['harness','configuration'],environment:['execution','environment_snapshot'],policy:['execution','sandbox_policy'],capabilities:['execution','sandbox_capabilities'],network_cache:['execution','network_cache_configuration'],grader:['grader','configuration'],grader_environment:['grader','environment'],pricing:['observability','pricing_snapshot']};
    for(const [key,[group,name]] of Object.entries(references) as Array<[string,[string,string]]>)spec[group][name]={snapshot_id:'snap-'+randomUUID(),sha256:hash(values[key])};
    const file=join(directory,'evaluation-protocol-configuration.json');await writeFile(file,JSON.stringify({schema_version:'forge.eval.plan-export.v1',spec,spec_hash:hash(spec),resolved_snapshots:values,execution_label:spec.execution.target_platform,read_only_plan:true,required_environment:'unverified offline protocol fixture'}));
    const imported=await engine.call('evaluation.import_plan',{client_action_id:'act-'+randomUUID(),path:file});
    await click('评测实验');await wait("document.querySelector('h2')?.textContent==='实验与结果'");
    await wait(`Array.from(document.querySelectorAll('select option')).some(o=>o.value===${JSON.stringify(imported.template_id)})`);
    await setSelect('源版本与任务配置',imported.template_id);await wait("document.querySelector('[data-wizard-step=\"0\"]')!==null");
    check('evaluation-real-template-origin',await js<boolean>("document.body.textContent.includes('imported_unverified')&&document.body.textContent.includes('desktop-protocol-fixture')"));
    await click('下一步');await wait("document.querySelector('[data-wizard-step=\"1\"]')!==null");
    checks.push(...await probeLayout(window,output,'evaluation-models','.wizard-nav'));
    await click('下一步');await wait("document.querySelector('[data-wizard-step=\"2\"]')!==null");
    await setSelect('执行目标','windows-native');await click('下一步');await wait("document.querySelector('[data-wizard-step=\"3\"]')!==null");
    await click('检查兼容性与预算');await wait("document.body.textContent.includes('执行 blocked；可保存和导出计划')");
    check('windows-incompatible-keeps-export-entry',await js<boolean>("Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='保存计划并导出配置'&&!b.disabled)&&document.body.textContent.includes('unverified_configuration')"));
    checks.push(...await probeLayout(window,output,'evaluation-budget','.wizard-nav'));
    await click('保存不可变计划');await wait("document.querySelector('[data-missing-evidence]')!==null");
    const first=(await engine.call('evaluation.list',{})).items.find((r:EvaluationListResult['items'][number])=>r.origin==='local'&&r.dataset==='desktop-protocol-fixture')!;
    const a=await engine.call('evaluation.snapshot',{run_id:first.run_id});
    check('evaluation-planned-denominator-before-worker',(a.metrics.counts as any).planned===2&&(a.metrics.counts as any).unscored===2&&!a.attempts.length&&a.spec.model_mode==='scripted_mock');
    check('evaluation-blocked-start-disabled',await js<boolean>("Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='启动兼容实验')?.disabled===true"));
    await click('新实验');await click('2 · 模型与 Harness');await wait("document.querySelector('[data-wizard-step=\"1\"]')!==null");
    await js("(()=>{const e=Array.from(document.querySelectorAll('label')).find(x=>x.textContent.startsWith('最多交付修复次数'))?.querySelector('input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'2');e.dispatchEvent(new Event('input',{bubbles:true}));})()");
    await click('4 · 配置与预算');await click('检查兼容性与预算');await wait("Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='保存不可变计划'&&!b.disabled)");
    await click('保存不可变计划');await wait("document.querySelector('[data-missing-evidence]')!==null");
    const second=(await engine.call('evaluation.list',{})).items.filter((r:EvaluationListResult['items'][number])=>r.origin==='local'&&r.dataset==='desktop-protocol-fixture').at(-1)!;
    const b=await engine.call('evaluation.snapshot',{run_id:second.run_id});
    check('changed-harness-creates-new-immutable-run',a.run_id!==b.run_id&&a.spec_hash!==b.spec_hash&&a.spec.harness.max_delivery_repairs===0&&b.spec.harness.max_delivery_repairs===2&&canonical(a.spec.budget)===canonical(b.spec.budget));
    await setSelect('同题 A/B 对照',first.run_id);await wait("document.body.textContent.includes('不可比警告')&&document.body.textContent.includes('B · 同题实际 Trace')");
    check('evaluation-real-dual-trace-and-origin-warning',await js<boolean>("document.querySelectorAll('.attempt-trace').length===2&&document.body.textContent.includes('unverified_origin')&&document.body.textContent.includes('未评分')"));
    const destination=join(directory,'actual-evaluation-results.zip'),scope={kind:'run' as const,id:second.run_id},classification='metadata_only' as const;
    const grant=await engine.call('bundle.prepare_export',{path:destination,scope,classification});await engine.call('bundle.export',{client_action_id:'act-'+randomUUID(),scope,classification,destination_token:grant.destination_token});
    const source=await engine.call('bundle.prepare_import',{path:destination}),result=await engine.call('bundle.import',{client_action_id:'act-'+randomUUID(),source_token:source.source_token});
    const external=await engine.call('evaluation.snapshot',{run_id:result.run_ids[0]});
    check('actual-bundle-import-remains-readonly-unverified',external.read_only&&external.origin==='imported_unverified'&&(external.metrics.counts as any).planned===2&&!external.attempts.length);
    checks.push(...await probeLayout(window,output,'evaluations'));
    await writeFile(join(output,'evaluations.png'),(await window.webContents.capturePage()).toPNG());
    await js("document.querySelector('.dual-traces')?.scrollIntoView({block:'end'})");
    await new Promise(resolve=>setTimeout(resolve,200));
    await writeFile(join(output,'evaluation-comparison.png'),(await window.webContents.capturePage()).toPNG());
  }catch(error){check('evaluation-probe-completed',false,{message:(error as Error).message});}
  return checks;
}
