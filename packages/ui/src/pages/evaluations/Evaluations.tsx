import React,{useEffect,useRef,useState} from 'react';
import type {EvaluationTemplatesResult,EvaluationTemplateResult,EvaluationDraftResult,EvaluationValidateResult,
  EvaluationListResult,EvaluationComparisonResult,EvaluationCreateRunRequest,JSONValue} from '@forgecode/contracts';
import type {DesktopOperations,ConnectionMetadata} from '../../transport';
import {WizardStep} from '../../components/run-spec/Wizard';
import {VirtualList} from '../workspace/VirtualList';
import {Trace} from '../../components/trace/Trace';
import {mergeSpans,safeFacts,type Span} from '../../components/trace/state';
import {attemptViews,resultLabel,missingLabel,budgetProblem,type Choices,type Snapshot,type Trial,type Attempt} from './state';

const actionId=()=> 'act-'+crypto.randomUUID();
const comparisonProtocol={comparison:'paired_task_set' as const,metric:'protocol_success_rate' as const};
function field(value:JSONValue|undefined,key:string):JSONValue|undefined {return value&&typeof value==='object'&&!Array.isArray(value)?value[key]:undefined;}
function metric(value:JSONValue|undefined):string {if(value==null)return'未知';if(typeof value!=='object')return String(value);const v=field(value,'value');if(v==null)return String(field(value,'status')??'未知');
  return field(value,'denominator')!==undefined?String(v)+' ('+field(value,'numerator')+'/'+field(value,'denominator')+')':String(v)+(field(value,'currency')?' '+field(value,'currency'):'');}

function AttemptTrace({transport,run,trial,title}:{transport:DesktopOperations;run:Snapshot|null;trial:Trial|null;title:string}) {
  const [attemptId,setAttemptId]=useState<string|null>(null),[spans,setSpans]=useState<Span[]>([]),[cursor,setCursor]=useState<string|null>(null);
  const [selected,setSelected]=useState<Span|null>(null),[error,setError]=useState<string|null>(null),[gap,setGap]=useState(false),[loading,setLoading]=useState(false);
  const generation=useRef(0),paged=useRef(false),attempts=trial&&run?attemptViews(trial,run.attempts):null;
  const actualId=attemptId&&attempts?.all.some(a=>a.id===attemptId)?attemptId:attempts?.selected?.id??null;
  useEffect(()=>{
    const ticket=++generation.current;let disposed=false,timer:ReturnType<typeof setTimeout>;paged.current=false;setSpans([]);setSelected(null);setError(null);setGap(false);setCursor(null);
    async function load(){try{
      if(!run||!actualId||run.read_only)return;
      const page=await transport.observationSpans({scope:{kind:'run',id:run.run_id},attempt_id:actualId,limit:100});
      if(disposed||ticket!==generation.current)return;
      setSpans(old=>{const merged=mergeSpans(old,page.items);if(merged.dropped)setGap(true);return merged.items;});setCursor(page.next_cursor);setGap(old=>old||page.history_gap);
    }catch(reason){if(!disposed)setError((reason as Error).message);}finally{if(!disposed&&!paged.current)timer=setTimeout(load,2000);}}
    void load();return()=>{disposed=true;clearTimeout(timer);};
  },[transport,run?.run_id,run?.read_only,actualId]);
  async function more(){if(!run||!actualId||!cursor||loading)return;const ticket=generation.current;paged.current=true;setLoading(true);
    try{const page=await transport.observationSpans({scope:{kind:'run',id:run.run_id},attempt_id:actualId,cursor,limit:100});if(ticket!==generation.current)return;
      const merged=mergeSpans(spans,page.items);setSpans(merged.items);setCursor(page.next_cursor);setGap(gap||merged.dropped||page.history_gap);
    }catch(reason){if(ticket===generation.current)setError((reason as Error).message);}finally{setLoading(false);}}
  return <section className="attempt-trace"><h3>{title}</h3>
    {!trial?<p>尚未加载相同 task / repeat 的记录。</p>:<><p>{trial.task_id} · revision {trial.task_revision} · repeat {trial.repeat_index+1}</p>
      <p>首次：{resultLabel(attempts?.first??null)} · 最后获准：{resultLabel(attempts?.selected??null)}</p>
      <label>查看实际尝试<select value={actualId??''} onChange={e=>{setAttemptId(e.target.value||null);setSelected(null);}}><option value="">尚无 attempt</option>
        {attempts?.all.map(a=><option key={a.id} value={a.id}>#{a.attempt_no} · {resultLabel(a)} · {a.execution_state} · cleanup {a.cleanup_state}{a.id===trial.selected_attempt_id?' · 选定结果':''}</option>)}</select></label>
      {attempts?.all.map(a=><p className="muted" key={a.id}>#{a.attempt_no} Agent {a.agent_outcome??'未知'} / 独立 grader {resultLabel(a)} / {a.error_origin??'无已记录错误来源'} / cleanup {a.cleanup_state} / trace {a.trace_complete?'完整':'缺口或未完成'}</p>)}
      {run?.read_only?<p className="notice">外部 imported_unverified 结果；可信本机 Trace 不可用。保留原始包用于离线复核。</p>:<>
        {error&&<p role="alert" className="notice">{error}</p>}{gap&&<p className="notice">Trace 采集或列表有缺口；不会补造事件。</p>}
        <Trace items={spans} selected={selected?.span_id??null} select={setSelected}/><button className="secondary" disabled={!cursor||loading} onClick={more}>更多 Trace（历史分页）</button>
        {selected&&<pre>{JSON.stringify(safeFacts(selected.metadata?.facts),null,2)}</pre>}
        {!spans.length&&actualId&&<p className="muted">当前没有可读取的 Trace 事实。</p>}</>}
    </>}
  </section>;
}

export function Evaluations({transport}:{transport:DesktopOperations}) {
  const [templates,setTemplates]=useState<EvaluationTemplatesResult|null>(null),[templateId,setTemplateId]=useState(''),[template,setTemplate]=useState<EvaluationTemplateResult|null>(null);
  const [connections,setConnections]=useState<ConnectionMetadata[]>([]),[choices,setChoices]=useState<Choices|null>(null),[step,setStep]=useState(0),[wizard,setWizard]=useState(true);
  const [draft,setDraft]=useState<EvaluationDraftResult|null>(null),[validation,setValidation]=useState<EvaluationValidateResult|null>(null);
  const [runs,setRuns]=useState<EvaluationListResult|null>(null),[runId,setRunId]=useState<string|null>(null),[snapshot,setSnapshot]=useState<Snapshot|null>(null),[cursor,setCursor]=useState<string|null>(null);
  const [trialId,setTrialId]=useState<string|null>(null),[partnerId,setPartnerId]=useState(''),[partner,setPartner]=useState<Snapshot|null>(null),[comparison,setComparison]=useState<EvaluationComparisonResult|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null),[notice,setNotice]=useState<string|null>(null);
  const lock=useRef(false),createRequest=useRef<EvaluationCreateRunRequest|null>(null),draftRequest=useRef<{key:string;id:string}|null>(null),epoch=useRef(0);
  const selectedTrial=snapshot?.trials.find(t=>t.id===trialId)??snapshot?.trials[0]??null;
  const partnerTrial=selectedTrial?partner?.trials.find(t=>t.task_id===selectedTrial.task_id&&t.repeat_index===selectedTrial.repeat_index)??null:null;
  async function refreshCatalog(){const [a,b,c]=await Promise.all([transport.evaluationTemplates(),transport.evaluationRuns({limit:100}),transport.connections()]);setTemplates(a);setRuns(b);setConnections(c.items);}
  useEffect(()=>{let disposed=false;void Promise.all([transport.evaluationTemplates(),transport.evaluationRuns({limit:100}),transport.connections()]).then(([a,b,c])=>{
    if(!disposed){setTemplates(a);setRuns(b);setConnections(c.items);}}).catch(reason=>{if(!disposed)setError((reason as Error).message);});return()=>{disposed=true;};},[transport]);
  async function action(operation:()=>Promise<void>){if(lock.current)return;lock.current=true;setBusy(true);setError(null);setNotice(null);
    try{await operation();}catch(reason){setError((reason as Error).message);}finally{lock.current=false;setBusy(false);}}
  function chooseRun(id:string){epoch.current++;setRunId(id);setCursor(null);setTrialId(null);setSnapshot(null);setPartner(null);setComparison(null);setWizard(false);}
  useEffect(()=>{
    if(!runId)return;const ticket=++epoch.current;let disposed=false,timer:ReturnType<typeof setTimeout>;
    async function load(){try{const value=await transport.evaluationSnapshot({run_id:runId!,limit:20,...(cursor?{cursor}:{})});
      if(!disposed&&ticket===epoch.current)setSnapshot(value);
    }catch(reason){if(!disposed&&ticket===epoch.current)setError((reason as Error).message);}finally{if(!disposed)timer=setTimeout(load,1500);}}
    void load();return()=>{disposed=true;clearTimeout(timer);};
  },[runId,cursor,transport]);
  useEffect(()=>{
    let disposed=false,timer:ReturnType<typeof setTimeout>;setPartner(null);setComparison(null);
    if(!runId||!partnerId||!selectedTrial)return;
    const leftRun=runId,rightRun=partnerId,chosenTrial=selectedTrial;
    async function loadPartner(){try{
      const result=await transport.evaluationComparison({run_ids:[leftRun,rightRun],protocol:comparisonProtocol});if(disposed)return;setComparison(result);
      let after:string|undefined;
      for(let i=0;i<5;i++){const value=await transport.evaluationSnapshot({run_id:rightRun,task_id:chosenTrial.task_id,limit:20,...(after?{cursor:after}:{})});if(disposed)return;
        if(value.trials.some(t=>t.repeat_index===chosenTrial.repeat_index)||!value.next_cursor){setPartner(value);return;}after=value.next_cursor;}
      if(!disposed)setError('对照重复记录未在分页限额内找到。');
    }catch(reason){if(!disposed)setError((reason as Error).message);}finally{if(!disposed)timer=setTimeout(loadPartner,2000);}}
    void loadPartner();return()=>{disposed=true;clearTimeout(timer);};
  },[runId,partnerId,selectedTrial?.task_id,selectedTrial?.repeat_index,snapshot?.state,transport]);
  function change(value:Partial<Choices>){setChoices(c=>c?{...c,...value}:c);setDraft(null);setValidation(null);draftRequest.current=null;createRequest.current=null;}
  async function selectTemplate(id:string){setTemplateId(id);setTemplate(null);setChoices(null);setDraft(null);setValidation(null);setStep(0);createRequest.current=null;draftRequest.current=null;
    if(id){const value=await transport.evaluationTemplate({template_id:id});setTemplate(value);setChoices(value.defaults);}}
  async function check(){if(!choices||!templateId)return;const problem=budgetProblem(choices);if(problem)throw new Error(problem);
    const key=JSON.stringify({templateId,choices});if(draftRequest.current?.key!==key)draftRequest.current={key,id:actionId()};
    const frozen=await transport.evaluationDraft({client_action_id:draftRequest.current.id,template_id:templateId,choices});setDraft(frozen);
    const checked=await transport.evaluationValidate({spec:frozen.spec});setValidation(checked);createRequest.current=null;
  }
  async function create(exportPlan=false){if(!draft||!validation||draft.spec_hash!==validation.spec_hash)throw new Error('请先检查当前配置。');
    if(!createRequest.current)createRequest.current={client_action_id:actionId(),spec:draft.spec,spec_hash:draft.spec_hash,validation_ticket:validation.validation_ticket};
    const accepted=await transport.evaluationCreate(createRequest.current);chooseRun(accepted.run_id);await refreshCatalog();
    if(exportPlan){const result=await transport.exportExperimentPlan(accepted.run_id);setNotice(result.cancelled?'已保存计划，导出已取消。':'配置已通过原生文件选择导出。');}
  }
  async function retry(attempt:Attempt){if(!selectedTrial||!snapshot)return;
    await transport.evaluationRetry({client_action_id:actionId(),trial_id:selectedTrial.id,reason:'Desktop user explicitly authorized the declared infrastructure retry'});
    setSnapshot(await transport.evaluationSnapshot({run_id:snapshot.run_id,limit:20,...(cursor?{cursor}:{})}));}
  const selectedAttempt=selectedTrial&&snapshot?attemptViews(selectedTrial,snapshot.attempts).selected:null;
  const retryAllowed=!!snapshot&&!snapshot.read_only&&!!selectedAttempt&&selectedAttempt.cleanup_state==='clean'&&['finished','blocked','error'].includes(selectedAttempt.execution_state)&&
    selectedAttempt.attempt_no<snapshot.spec.protocol.max_infrastructure_attempts&&snapshot.spec.protocol.infrastructure_retry_categories?.includes(selectedAttempt.error_origin as never);
  return <div className="evaluations"><section><h2>实验与结果</h2><p>不可变配置、计划分母与独立 grader。真实模型实验仅在环境、策略和预算许可就绪后启动。</p>
    <button className="secondary" disabled={busy} onClick={()=>action(async()=>{await refreshCatalog();})}>刷新目录</button>
    <button className="secondary" disabled={busy} onClick={()=>action(async()=>{const r=await transport.importExperimentPlan();await refreshCatalog();if(r.template_id){setWizard(true);await selectTemplate(r.template_id);}setNotice(r.cancelled?'配置导入已取消。':'已导入来源未验证的配置。');})}>导入实验配置</button>
    <button className="secondary" disabled={busy} onClick={()=>action(async()=>{const r=await transport.importResults();await refreshCatalog();if(r.run_ids?.length)chooseRun(r.run_ids[0]);setNotice(r.cancelled?'结果导入已取消。':'外部结果已导入，来源未验证。');})}>导入结果包</button>
    <button disabled={busy} onClick={()=>{setWizard(true);setStep(0);}}>新实验</button>
    {error&&<p role="alert" className="notice">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {!templates?.items.length&&<p className="notice">尚无完整实验配置。请导入含 resolved snapshots 的配置，或先通过已有 CLI 保存实验；本机环境缺失不会生成假成绩。</p>}
    <VirtualList label="实验列表" items={runs?.items??[]} itemKey={r=>r.run_id} render={r=><button className="secondary file-row" onClick={()=>chooseRun(r.run_id)}>{r.dataset} · {r.state} · planned {r.planned_trials} · {r.origin} · {r.model_mode} · {r.run_id.slice(-8)}</button>}/>
    <button className="secondary" disabled={busy||!runs?.next_cursor} onClick={()=>action(async()=>{if(!runs?.next_cursor)return;const next=await transport.evaluationRuns({limit:100,cursor:runs.next_cursor});
      const items=Array.from(new Map([...runs.items,...next.items].map(r=>[r.run_id,r])).values()).slice(-10000);setRuns({...next,items,history_gap:runs.history_gap||next.history_gap||runs.items.length+next.items.length>10000});})}>更多实验</button>
    {runs?.history_gap&&<p className="notice">实验列表存在缺口，请分页核对。</p>}
  </section>
  {wizard&&<section><h2>四步实验配置</h2><label>源版本与任务配置<select value={templateId} disabled={busy} onChange={e=>action(()=>selectTemplate(e.target.value))}><option value="">选择完整配置</option>
    {templates?.items.map(t=><option key={t.template_id} value={t.template_id}>{t.dataset} / {t.revision} · {t.task_count} 题 · {t.origin} · {t.source_commit?.slice(0,8)??'commit 未知'}</option>)}</select></label>
    {template&&choices&&<><nav className="wizard-nav">{['版本与任务','模型与 Harness','兼容目标','配置与预算'].map((s,i)=><button key={s} className={step===i?'':'secondary'} disabled={busy} onClick={()=>setStep(i)}>{i+1} · {s}</button>)}</nav>
      <WizardStep step={step} choices={choices} spec={template.spec} connections={connections} change={change} busy={busy} draft={draft} validation={validation}
        check={()=>action(check)} create={()=>action(()=>create())} exportPlan={()=>action(()=>create(true))}/>
      <button className="secondary" disabled={busy||step===0} onClick={()=>setStep(s=>s-1)}>上一步</button><button disabled={busy||step===3||!choices.task_ids.length} onClick={()=>setStep(s=>s+1)}>下一步</button></>}
  </section>}
  {!wizard&&snapshot&&<><section><h2>{snapshot.spec.dataset.name} · {snapshot.state}</h2><p><code>{snapshot.run_id}</code> · {snapshot.origin} · configuration {snapshot.configuration_origin} · {snapshot.spec.model_mode}</p>
    {snapshot.read_only&&<p className="notice">外部来源未验证，评分是导入的独立 grader 声明；只读结果无法启动或重试。</p>}
    <p className={snapshot.missing_evidence_count?'notice':'muted'} data-missing-evidence={snapshot.missing_evidence_count}>{missingLabel(snapshot.missing_evidence_count)}</p>
    {snapshot.missing_evidence_count>0&&<details><summary>查看缺失产物（前 100 项）</summary><pre>{snapshot.missing_evidence.join('\n')}</pre></details>}
    <div className="metric-grid">{[['计划题数',field(snapshot.metrics.counts,'planned')],['全部尝试',field(snapshot.metrics.counts,'attempts')],['未评分',field(snapshot.metrics.counts,'unscored')],
      ['计划成功率',snapshot.metrics.planned_success_rate],['首次成功率',snapshot.metrics.first_attempt_success_rate],['评分覆盖率',snapshot.metrics.grading_coverage],['已评分成功率',snapshot.metrics.scored_success_rate],
      ['完成误报率',snapshot.metrics.completion_false_positive],['Trace 完整率',snapshot.metrics.trace_completeness],['实际已知成本',snapshot.metrics.total_known_cost],['估算成本',snapshot.metrics.total_estimated_cost],['费用未知请求',snapshot.metrics.unknown_cost_requests],['每成功成本',snapshot.metrics.cost_per_success]].map(([label,value])=>
      <div key={String(label)}><strong>{String(label)}</strong><span>{metric(value)}</span></div>)}</div>
    {!snapshot.compatible&&snapshot.issues.slice(0,100).map((i,n)=><p className="notice" key={n}>{i.task_id??'整体'} · {i.kind} · {i.message}</p>)}
    <button disabled={busy||snapshot.read_only||!snapshot.compatible||snapshot.state!=='created'} onClick={()=>action(async()=>{
      const current=await transport.evaluationValidate({spec:snapshot.spec});if(!current.compatible)throw new Error(current.issues.map(i=>i.message).join('\n'));
      await transport.evaluationStart({client_action_id:actionId(),run_id:snapshot.run_id});setSnapshot(await transport.evaluationSnapshot({run_id:snapshot.run_id}));})}>启动兼容实验</button>
    <button className="secondary" disabled={busy||snapshot.read_only||!['queued','running','cancel_requested'].includes(snapshot.state)} onClick={()=>action(async()=>{await transport.evaluationCancel({client_action_id:actionId(),run_id:snapshot.run_id,reason:'Desktop user cancelled evaluation'});})}>请求取消</button>
    <button className="secondary" disabled={busy||snapshot.read_only} onClick={()=>action(async()=>{const r=await transport.exportExperimentPlan(snapshot.run_id);setNotice(r.cancelled?'配置导出已取消。':'配置已导出。');})}>导出配置</button>
    <button className="secondary" disabled={busy||snapshot.read_only} onClick={()=>action(async()=>{const r=await transport.exportResults(snapshot.run_id);setNotice(r.cancelled?'结果导出已取消。':'冻结结果包已导出。');})}>导出结果与隐私确认</button>
    <details><summary>不可变 RunSpec 与 hash</summary><code>{snapshot.spec_hash}</code><pre>{JSON.stringify(snapshot.spec,null,2)}</pre></details>
    <h3>逐题记录（每页最多 20 个 trial）</h3><VirtualList label="计划 trial 与选定尝试" items={snapshot.trials} itemKey={t=>t.id} render={t=>{
      const views=attemptViews(t,snapshot.attempts);return <button className="secondary file-row" onClick={()=>setTrialId(t.id)}>{t.task_id} · repeat {t.repeat_index+1} · 首次 {resultLabel(views.first)} · 选定 {resultLabel(views.selected)} · attempts {views.all.length} · cleanup {views.selected?.cleanup_state??'尚未执行'}</button>;}}/>
    <button className="secondary" disabled={!cursor} onClick={()=>{setCursor(null);setTrialId(null);}}>回到第一页</button><button className="secondary" disabled={!snapshot.next_cursor} onClick={()=>{setCursor(snapshot.next_cursor);setTrialId(null);}}>下一页 trial</button>
    <button className="secondary" disabled={busy||!retryAllowed} onClick={()=>action(()=>retry(selectedAttempt!))}>明确授权基础设施重试</button>
    <p className="muted">末次授权 attempt 是选定结果；首次结果、历史失败、所有费用与清理状态继续保留。</p>
    <label>同题 A/B 对照<select value={partnerId} onChange={e=>setPartnerId(e.target.value)}><option value="">选择另一个实验</option>{runs?.items.filter(r=>r.run_id!==snapshot.run_id).map(r=><option key={r.run_id} value={r.run_id}>{r.dataset} · {r.run_id.slice(-8)} · {r.origin}</option>)}</select></label>
    {comparison&&<><p className={comparison.comparable?'muted':'notice'}>{comparison.comparable?'控制条件可比；尚未推断提升':'不可比警告：'+comparison.differences.join(' / ')}</p><p>{comparison.uncertainty}</p></>}
    {partnerTrial&&selectedTrial&&partnerTrial.task_revision!==selectedTrial.task_revision&&<p className="notice">相同任务 ID 的 revision 不同；不能据此解释提升。</p>}
  </section><div className="dual-traces"><AttemptTrace transport={transport} run={snapshot} trial={selectedTrial} title="A · 实际 Trace"/>
    {partnerId&&<AttemptTrace transport={transport} run={partner} trial={partnerTrial} title="B · 同题实际 Trace"/>}</div></>}
  </div>;
}
