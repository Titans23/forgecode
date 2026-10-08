import React,{useEffect,useRef,useState} from 'react';
import type {FailureGetResult,FailureListResult,FailureAnnotateRequest,FailureCandidateResult,EvaluationListResult} from '@forgecode/contracts';
import type {DesktopOperations} from '../../transport';
import {VirtualList} from '../workspace/VirtualList';

export type FailureTarget={run_id:string;attempt_id:string};
const categories=['model_reasoning','tool_usage','context','verification','sandbox','environment','provider','grader','unknown'] as const;
export function FailureDetails({value}:{value:FailureGetResult}) {
  return <><h3>{value.task_id} · {value.task_revision}</h3><p>{value.attempt.execution_state} / 独立 grader {value.attempt.grade_result??'未评分'} / cleanup {value.attempt.cleanup_state} / {value.origin}</p>
    {value.origin==='imported_unverified'&&<p className="notice">外部结果来源未验证；人工修正另存为本机注释，原始评分和来源保持只读。</p>}
    <p>规则只提出相关性建议，人工标签不证明根因。规则依据最近 200 个尝试事件。</p>
    {value.suggestions.map(s=><p className="notice" key={s.rule}>建议 {s.category}：{s.rule} · {s.authority}<br/>{s.basis_ids.join(' / ')}</p>)}
    <h3>标注历史</h3>{value.history_gap&&<p className="notice">显示末 20 个版本，旧版本继续保存在数据库。</p>}
    {value.annotations.map(a=><article key={a.id}><strong>{a.category} · {a.author}</strong><p>{a.created_at??'历史时间未知'} · {a.origin} · supersedes {a.supersedes??'无'}</p><p>{a.note}</p><p>证据 {a.evidence_refs.join(' / ')||'未选择'}</p></article>)}
    <p>证据范围：{value.evidence_scope} · {value.evidence_count} 项；显示前 100 项，缺失产物阻止完全复现判断。</p></>;
}

export function Failures({transport,initial}:{transport:DesktopOperations;initial:FailureTarget|null}) {
  const [runs,setRuns]=useState<EvaluationListResult|null>(null),[runId,setRunId]=useState(initial?.run_id??'');
  const [page,setPage]=useState<FailureListResult|null>(null),[cursor,setCursor]=useState<string|null>(null),[filter,setFilter]=useState('');
  const [attemptId,setAttemptId]=useState(initial?.attempt_id??''),[detail,setDetail]=useState<FailureGetResult|null>(null);
  const [author,setAuthor]=useState('local-user'),[category,setCategory]=useState<FailureAnnotateRequest['category']>('unknown'),[note,setNote]=useState(''),[refs,setRefs]=useState<string[]>([]);
  const [error,setError]=useState<string|null>(null),[busy,setBusy]=useState(false),[notice,setNotice]=useState<string|null>(null),[fixture,setFixture]=useState<string|null>(null);
  const [reproductionRun,setReproductionRun]=useState(''),[reproductionAttempt,setReproductionAttempt]=useState(''),[choices,setChoices]=useState<FailureListResult|null>(null);
  const pending=useRef(new Map<string,string>()),lock=useRef(false),generation=useRef(0);
  const id=(method:string,params:unknown)=>{const key=method+JSON.stringify(params);let value=pending.current.get(key);if(!value){value='act-'+crypto.randomUUID();pending.current.set(key,value);if(pending.current.size>100)pending.current.delete(pending.current.keys().next().value!);}return value;};
  useEffect(()=>{let disposed=false;void transport.evaluationRuns({limit:100}).then(r=>{if(!disposed)setRuns(r);}).catch(e=>{if(!disposed)setError(e.message);});return()=>{disposed=true;};},[transport]);
  useEffect(()=>{if(initial){setRunId(initial.run_id);setAttemptId(initial.attempt_id);setCursor(null);}},[initial]);
  useEffect(()=>{let disposed=false;setPage(null);if(runId)void transport.failureList({run_id:runId,limit:20,...(filter?{category:filter as FailureAnnotateRequest['category']}:{ }),...(cursor?{cursor}:{})})
    .then(r=>{if(!disposed)setPage(r);}).catch(e=>{if(!disposed)setError(e.message);});return()=>{disposed=true;};},[transport,runId,filter,cursor]);
  useEffect(()=>{const ticket=++generation.current;setDetail(null);setRefs([]);setFixture(null);setNote('');setError(null);
    if(runId&&attemptId)void transport.failureGet({run_id:runId,attempt_id:attemptId}).then(r=>{if(ticket===generation.current){setDetail(r);setCategory(r.annotations.at(-1)?.category??'unknown');}})
      .catch(e=>{if(ticket===generation.current)setError(e.message);});return()=>{generation.current++;};},[transport,runId,attemptId]);
  useEffect(()=>{let disposed=false;setChoices(null);setReproductionAttempt('');if(reproductionRun)void transport.failureList({run_id:reproductionRun,limit:20})
    .then(r=>{if(!disposed)setChoices(r);}).catch(e=>{if(!disposed)setError(e.message);});return()=>{disposed=true;};},[transport,reproductionRun]);
  async function action(operation:()=>Promise<void>){if(lock.current)return;lock.current=true;setBusy(true);setError(null);setNotice(null);const ticket=generation.current;
    try{await operation();if(ticket===generation.current&&runId&&attemptId)setDetail(await transport.failureGet({run_id:runId,attempt_id:attemptId}));}
    catch(e){if(ticket===generation.current)setError((e as Error).message);}finally{lock.current=false;setBusy(false);}}
  async function readFixture(candidate:FailureCandidateResult){const current=await transport.failureCandidate({candidate_id:candidate.candidate_id});if(!current.fixture.available||current.fixture.size_bytes>1048576)throw Error('候选产物缺失或超过限额');
    let offset=0;const bytes:number[]=[];
    while(offset<current.fixture.size_bytes){const part=await transport.artifactChunk({artifact_id:current.fixture.artifact_id,offset,length:Math.min(262144,current.fixture.size_bytes-offset)});
      const chunk=Uint8Array.from(atob(part.data_base64),c=>c.charCodeAt(0));if(!chunk.length||part.offset!==offset||part.sha256!==current.fixture.sha256||chunk.length>current.fixture.size_bytes-offset)throw Error('候选产物内容变化');
      for(const byte of chunk)bytes.push(byte);offset+=chunk.length;if(part.eof!==(offset===current.fixture.size_bytes))throw Error('候选产物不完整');}
    setFixture(JSON.stringify(JSON.parse(new TextDecoder().decode(new Uint8Array(bytes))),null,2));}
  return <div className="failures"><section><h2>失败案例与回归候选</h2><p>保留实际失败、未评分和清理异常。已保存与已复现分别记录；复现仅确认相同冻结条件下的独立 grader 失败。</p>
    <div className="toolbar"><label className="field">实验<select value={runId} onChange={e=>{setRunId(e.target.value);setAttemptId('');setCursor(null);}}><option value="">选择实验</option>{runs?.items.map(r=><option value={r.run_id} key={r.run_id}>{r.dataset} · {r.run_id.slice(-8)} · {r.origin}</option>)}</select></label>
    <button className="secondary" disabled={!runs?.next_cursor||busy} onClick={()=>action(async()=>{const more=await transport.evaluationRuns({limit:100,cursor:runs!.next_cursor!});setRuns({...more,items:[...runs!.items,...more.items].slice(-10000)});})}>更多实验</button>
    <label className="field">人工分类筛选<select value={filter} onChange={e=>{setFilter(e.target.value);setCursor(null);}}><option value="">全部分类</option>{categories.map(c=><option key={c}>{c}</option>)}</select></label></div>
    {error&&<p role="alert" className="notice">{error}</p>}{notice&&<p role="status">{notice}</p>}
    <VirtualList label="真实失败尝试" items={page?.items??[]} itemKey={r=>r.attempt_id} render={r=><button className="secondary file-row" onClick={()=>setAttemptId(r.attempt_id)}>{r.task_id} · {r.category} · {r.execution_state} / {r.grade_result??'未评分'} · {r.attempt_id.slice(-8)}</button>}/>
    <div className="pagination"><button className="secondary" disabled={!cursor} onClick={()=>setCursor(null)}>第一页</button><button className="secondary" disabled={!page?.next_cursor} onClick={()=>setCursor(page!.next_cursor)}>下一页失败</button></div>
  </section>
  {detail&&<><section><FailureDetails value={detail}/><div className="subsection"><h3>人工标注与修正</h3>
    <div className="form-grid"><label className="field">作者<input maxLength={128} value={author} onChange={e=>setAuthor(e.target.value)}/></label><label className="field">分类<select value={category} onChange={e=>setCategory(e.target.value as typeof category)}>{categories.map(c=><option key={c}>{c}</option>)}</select></label>
    <label className="field field-wide">判断依据与修正说明<textarea maxLength={4000} value={note} onChange={e=>setNote(e.target.value)}/></label></div>
    <div className="check-list">{detail.evidence.map(r=><label className="check" key={r.artifact_id}><input type="checkbox" checked={refs.includes(r.artifact_id)} onChange={e=>setRefs(old=>e.target.checked?[...old,r.artifact_id]:old.filter(x=>x!==r.artifact_id))}/><span>{r.artifact_id} · {r.available?'实际产物可读':'产物缺失'} · SHA {r.sha256.slice(0,12)}</span></label>)}</div>
    <div className="action-row"><button disabled={busy||!author.trim()} onClick={()=>action(async()=>{const params={run_id:runId,attempt_id:attemptId,expected_head:detail.annotation_head,author,category,note,evidence_refs:refs};
      await transport.failureAnnotate({...params,client_action_id:id('annotate',params)});setNotice('已追加新版本，旧标注继续保留。');})}>保存人工标注版本</button>
    <button className="secondary" disabled={busy||!detail.is_failure} onClick={()=>action(async()=>{const params={run_id:runId,attempt_id:attemptId,expected_head:detail.annotation_head};
      await transport.failureSaveCandidate({...params,client_action_id:id('candidate',params)});setNotice('脱敏候选已保存；尚未据此证明复现。');})}>保存脱敏回归候选</button></div></div>
  </section><section><h3>实际复现检查</h3><p>先通过评测实验或 CLI 运行相同 task revision 与冻结配置，再选择本机实际尝试核验。此按钮读取记录，不发起模型调用。缺产物、未验证来源和条件差异会阻止“已复现”。</p>
    <div className="form-grid"><label className="field">复现实验<select value={reproductionRun} onChange={e=>setReproductionRun(e.target.value)}><option value="">选择实际执行实验</option>{runs?.items.map(r=><option key={r.run_id} value={r.run_id}>{r.dataset} · {r.run_id.slice(-8)} · {r.origin}</option>)}</select></label>
    <label className="field">实际失败尝试<select value={reproductionAttempt} onChange={e=>setReproductionAttempt(e.target.value)}><option value="">选择尝试</option>{choices?.items.map(r=><option key={r.attempt_id} value={r.attempt_id}>{r.task_id} · {r.attempt_id}</option>)}</select></label></div>
    <div className="pagination">
    <button className="secondary" disabled={!choices?.next_cursor||busy} onClick={()=>action(async()=>{if(!choices?.next_cursor)return;const next=await transport.failureList({run_id:reproductionRun,cursor:choices.next_cursor,limit:20});setChoices({...next,items:[...choices.items,...next.items].slice(-10000)});})}>更多复现尝试</button>
    </div>{detail.candidates.map(c=><article key={c.candidate_id}><strong>{c.status==='reproduced'?'已复现':c.status==='blocked'?'复现 blocked':'已保存，待实际复现'}</strong><p>{c.candidate_id} · {c.created_at}</p><p className="notice">{c.blockers.join(' / ')||c.authority}</p>
      <div className="action-row"><button className="secondary" disabled={busy||!c.fixture.available} onClick={()=>action(()=>readFixture(c))}>查看实际脱敏 fixture</button>
      <button className="secondary" disabled={busy||!c.fixture.available} onClick={()=>action(async()=>{const r=await transport.exportRegressionCandidate(c.candidate_id);setNotice(r.cancelled?'导出已取消。':'候选已通过原生文件选择导出。');})}>导出回归候选</button>
      <button disabled={busy||!reproductionRun||!reproductionAttempt} onClick={()=>action(async()=>{const params={candidate_id:c.candidate_id,run_id:reproductionRun,attempt_id:reproductionAttempt};const result=await transport.failureCheckReproduction({...params,client_action_id:id('reproduction',params)});setNotice(result.observed_status==='reproduced'?'独立 grader 失败已由新执行重现；人工根因标签仍未被证明。':result.blockers.join(' / '));})}>核验实际复现</button></div>
    </article>)}{fixture&&<details open><summary>冻结候选元数据与证据引用</summary><pre>{fixture}</pre></details>}
  </section></>}
  </div>;
}
