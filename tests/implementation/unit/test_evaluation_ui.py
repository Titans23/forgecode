"""Actual React experimental forms and trial views, without a replacement backend."""
import json
from pathlib import Path
import subprocess


def test_four_step_wizard_immutable_budget_and_last_authorized_trial_rendering():
    script=r'''
import {rolldown} from 'rolldown';import{createRequire}from'node:module';
const require=createRequire(process.cwd()+'/package.json');
async function load(path){const b=await rolldown({input:path,platform:'node',external:['react'],transform:{jsx:'react'}});const r=await b.generate({format:'cjs'});const m={exports:{}};new Function('module','exports','require',r.output[0].code)(m,m.exports,require);await b.close();return m.exports;}
const {WizardStep}=await load('packages/ui/src/components/run-spec/Wizard.tsx'),s=await load('packages/ui/src/pages/evaluations/state.ts');
const React=require('react'),{renderToString}=require('react-dom/server');
const c={task_ids:['a'],connection_id:null,target_platform:'windows-native',temperature:null,top_p:null,reasoning_effort:null,max_output_tokens:1024,max_context_tokens:4096,max_delivery_repairs:0,compaction_enabled:true,explore_enabled:true,repeats:2,max_infrastructure_attempts:2,infrastructure_retry_categories:['runner_crash'],feedback:'none',network_mode:'deny_direct',allowed_domains:[],cache_mode:'cold',attempt_wall_seconds:30,trial_wall_seconds:60,max_model_requests_per_attempt:10,max_tool_calls_per_attempt:20,spend_ceiling:'0',currency:'USD',spend_policy:'unknown_usage_stop_next_request'};
const spec={dataset:{task_ids:['a'],revision:'actual-v1',task_revisions:{a:'v1'}},source:{commit:null,source_snapshot:{sha256:'a'.repeat(64)},dependency_lock_sha256:'b'.repeat(64)},model_mode:'scripted_mock',model:{requested_model:'fixture'}};
const html=[0,1,2,3].map(step=>renderToString(React.createElement(WizardStep,{step,choices:c,spec,connections:[],change:()=>{},check:()=>{},create:()=>{},exportPlan:()=>{},busy:false,draft:{spec_hash:'c'.repeat(64),planned_trials:2,configuration_origin:'imported_unverified'},validation:{compatible:false,issues:[{kind:'protocol_incompatible',message:'Linux Docker required'}]}})));
const attempts=[{id:'first',trial_id:'t',attempt_no:1,grade_result:'pass',grade_state:'graded'},{id:'last',trial_id:'t',attempt_no:2,grade_result:'fail',grade_state:'graded'}],trial={id:'t',selected_attempt_id:'last'};
console.log(JSON.stringify({steps:html[0].includes('任务')&&html[1].includes('Harness')&&html[2].includes('缓存')&&html[3].includes('不可变'),exportEntry:html[3].includes('保存计划并导出配置'),incompatible:html[3].includes('Linux Docker required'),selected:s.attemptViews(trial,attempts).selected.id==='last',first:s.attemptViews(trial,attempts).first.id==='first',budget:s.budgetProblem({...c,trial_wall_seconds:1})!==null,unscored:s.resultLabel(null)==='未评分',missing:s.missingLabel(2).includes('2'),noBest:!html.join('').includes('最好结果')}));
'''
    result=subprocess.run(['node','--input-type=module','-e',script],cwd=Path(__file__).resolve().parents[3],capture_output=True,text=True,timeout=30,check=True)
    assert all(json.loads(result.stdout).values())
