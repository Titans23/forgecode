import React, { useEffect, useRef, useState } from 'react';
import type { ObservabilityContextResult, ObservabilityEvidenceResult, ObservabilityEventsResult, ObservabilityUsageResult,
  ObservabilityTimingsResult, SessionSnapshotResult } from '@forgecode/contracts';
import type { DesktopOperations } from '../../transport';
import { Trace } from '../../components/trace/Trace';
import { mergeSpans, milliseconds, nanos, object, safeFacts, text, type Span } from '../../components/trace/state';
import { VirtualList } from '../workspace/VirtualList';

export type ObservationTarget = { turnId: string; executionId?: string };
export type ClientLatency = { receiptMs: number; firstTextMs?: number };
const EVENT_TYPES = ['completion.accepted','completion.rejected','completion.repair_started','sandbox.prepared','sandbox.denied',
  'sandbox.failed','sandbox.cleanup_finished','verification.invalidated'] as const;
type Context = ObservabilityContextResult['items'][number];
type Evidence = ObservabilityEvidenceResult['items'][number];
type Event = ObservabilityEventsResult['items'][number];
type Collection = 'spans' | 'context' | 'evidence' | 'events';
type Data = { spans: Span[]; context: Context[]; evidence: Evidence[]; events: Event[]; cursors: Record<Collection,string|null> };
const empty = (): Data => ({spans:[],context:[],evidence:[],events:[],cursors:{spans:null,context:null,evidence:null,events:null}});

export function Observability({ transport, sessionId, target, latency }: {
  transport: DesktopOperations; sessionId: string|null; target: ObservationTarget|null; latency: Record<string,ClientLatency>;
}) {
  const [turnId,setTurnId] = useState(target?.turnId ?? null), [snapshot,setSnapshot] = useState<SessionSnapshotResult|null>(null);
  const [data,setData] = useState<Data>(empty), [usage,setUsage] = useState<ObservabilityUsageResult|null>(null);
  const [timing,setTiming] = useState<ObservabilityTimingsResult|null>(null), [selected,setSelected] = useState<Span|null>(null);
  const [evidenceId,setEvidenceId] = useState<string|null>(null), [context,setContext] = useState<Context|null>(null);
  const [output,setOutput] = useState<{reason:string;streams?:Record<string,string>}>({reason:'按需读取；默认只采集元数据。'});
  const [error,setError] = useState<string|null>(null), [gap,setGap] = useState(false), [busy,setBusy] = useState(false);
  const paged = useRef(false), generation = useRef(0), targetApplied=useRef(false);
  useEffect(() => {
    let disposed=false;
    setTurnId(target?.turnId ?? null);
    if (sessionId) void transport.sessionSnapshot({session_id:sessionId}).then(value=>{
      if (!disposed) { setSnapshot(value); if (!target?.turnId) setTurnId(value.turn_id); }
    }).catch(reason=>{if(!disposed)setError(reason.message);});
    return ()=>{disposed=true;};
  },[sessionId,target?.turnId]);
  async function refresh(reset=false) {
    if (!turnId) return;
    const current=generation.current,scope={kind:'turn' as const,id:turnId};
    const [u,t]=await Promise.all([transport.observationUsage({scope}),transport.observationTimings({turn_id:turnId})]);
    if(current!==generation.current)return;
    setUsage(u);setTiming(t);
    if(paged.current&&!reset)return;
    const [spans,contexts,evidence,events]=await Promise.all([transport.observationSpans({scope}),transport.observationContext({turn_id:turnId}),
      transport.observationEvidence({scope}),transport.observationEvents({scope,event_types:[...EVENT_TYPES]})]);
    if(current!==generation.current)return;
    if(paged.current&&!reset)return;
    setData({spans:spans.items,context:contexts.items,evidence:evidence.items,events:events.items,
      cursors:{spans:spans.next_cursor,context:contexts.next_cursor,evidence:evidence.next_cursor,events:events.next_cursor}});
    setGap(old=>old||spans.history_gap||contexts.history_gap||evidence.history_gap||events.history_gap);
    setSelected(old=>old ? spans.items.find(x=>x.span_id===old.span_id&&x.trace_id===old.trace_id)??old : null);
    setContext(old=>old ? contexts.items.find(item=>item.version===old.version)??old : null);
    if(target?.executionId&&!targetApplied.current) {
      const exact=await transport.observationSpans({scope,execution_id:target.executionId});
      if(current!==generation.current)return;
      if(exact.items[0]) {targetApplied.current=true;setSelected(exact.items[0]);setData(old=>({...old,spans:mergeSpans(old.spans,exact.items).items}));}
    }
    if(reset)paged.current=false;
  }
  useEffect(()=>{
    generation.current++;paged.current=false;targetApplied.current=false;setData(empty());setUsage(null);setTiming(null);setSelected(null);setContext(null);setEvidenceId(null);setGap(false);setError(null);
    setOutput({reason:'按需读取；默认只采集元数据。'});
    let disposed=false,timer:ReturnType<typeof setTimeout>;
    async function poll(){try{await refresh();if(!disposed)setError(null);}catch(reason){if(!disposed)setError((reason as Error).message);}finally{if(!disposed)timer=setTimeout(poll,1000);}}
    void poll();return()=>{disposed=true;generation.current++;clearTimeout(timer);};
  },[turnId]);
  async function action(fn:()=>Promise<void>){if(busy)return;setBusy(true);setError(null);try{await fn();}catch(reason){setError((reason as Error).message);}finally{setBusy(false);}}
  async function more(kind:Collection){
    const cursor=data.cursors[kind];if(!cursor||!turnId)return;
    const current=generation.current,scope={kind:'turn' as const,id:turnId};paged.current=true;
    const page=kind==='spans'?await transport.observationSpans({scope,cursor}):kind==='context'?await transport.observationContext({turn_id:turnId,cursor}):
      kind==='evidence'?await transport.observationEvidence({scope,cursor}):await transport.observationEvents({scope,cursor,event_types:[...EVENT_TYPES]});
    if(current!==generation.current)return;
    setGap(old=>old||page.history_gap);
    if(data[kind].length+page.items.length>10000)setGap(true);
    setData(old=>{
      const rows=[...old[kind],...page.items];
      const key=(v:unknown)=>{const x=object(v);return text(x.span_id??object(x.snapshot).snapshot_id??x.evidence_id??x.event_id);};
      const items=Array.from(new Map(rows.map(x=>[key(x),x])).values()).slice(-10000);
      return {...old,[kind]:items,cursors:{...old.cursors,[kind]:page.next_cursor}};
    });
  }
  async function loadOutput(){
    const execution=selected?.metadata?.execution_id;if(!turnId||typeof execution!=='string')return;
    const current=generation.current;
    const result=await transport.observationOutput({turn_id:turnId,execution_id:execution});
    if(current!==generation.current)return;
    if(result.state!=='available'||!result.artifact_id){setOutput({reason:result.state+' · '+result.reason});return;}
    const chunk=await transport.artifactChunk({artifact_id:result.artifact_id,offset:0,length:262144});
    if(current!==generation.current)return;
    if(!chunk.eof||chunk.sha256!==result.sha256)throw new Error('输出产物不完整；请重新核对。');
    const raw=new TextDecoder().decode(Uint8Array.from(atob(chunk.data_base64),c=>c.charCodeAt(0))),value=object(JSON.parse(raw));
    const streams:Record<string,string>={};for(const key of ['stdout','stderr'])if(typeof value[key]==='string')streams[key]=value[key];
    setOutput({reason:result.reason,streams});
  }
  const selectedEvidence=data.evidence.find(item=>item.evidence_id===evidenceId),client=turnId?latency[turnId]:undefined;
  const compare=object(selectedEvidence?.metadata?.workspace_comparison),observed=object(compare.observed),current=object(compare.current);
  const facts=object(selected?.metadata?.facts),contextMeta=object(context?.metadata?.context_metadata),after=object(contextMeta.after);
  const contextFacts=Object.keys(after).length?after:contextMeta;
  const moreButton=(kind:Collection)=>data.cursors[kind]&&<button disabled={busy} onClick={()=>action(()=>more(kind))}>加载更多{kind}（每页至多 100）</button>;
  return <div data-testid="observability"><section><h2>运行观测</h2>
    <select aria-label="观测任务" value={turnId??''} onChange={event=>setTurnId(event.target.value||null)}><option value="">选择任务</option>
      {snapshot?.turns.map(turn=><option key={turn.turn_id} value={turn.turn_id}>{turn.turn_id.slice(-8)} · {turn.outcome??turn.state}</option>)}</select>
    <button disabled={busy||!turnId} onClick={()=>action(()=>refresh(true))}>重新核对全部视图</button>
    {!turnId&&<p>打开一个会话，或从工具卡查看对应执行。</p>}
    {error&&<p role="alert">查询失败：{error}。游标失效时重新核对视图。</p>}
    {gap&&<p role="alert">历史或采集存在缺口，不能把当前页当作完整 Trace。</p>}
    {paged.current&&<p>已进入历史分页；费用和时延继续刷新。重新核对会从第一页读取。</p>}
  </section>{turnId&&<>
    <section data-testid="observation-usage"><h3>实际请求账本</h3>{usage?<>
      <p>请求 {usage.request_count??'未知'} · 已知 {usage.known_cost_decimal??'未知'} {usage.currency} · 估算 {usage.estimated_cost_decimal??'未知'} {usage.currency} · 未知费用请求 {usage.unknown_requests}</p>
      <p>完整总额 {usage.cost_decimal??'未知'} · 输入 {usage.input_tokens??'未知'} / 输出 {usage.output_tokens??'未知'} token。包含失败、摘要、Explore 和重试；父 Span 汇总不重复计费。</p>
      <p>Usage 质量：actual {text(object(usage.usage_quality_counts).actual)} / estimated {text(object(usage.usage_quality_counts).estimated)} / unknown {text(object(usage.usage_quality_counts).unknown)}。价格快照 {usage.pricing_snapshot?.sha256??'缺失'}。</p>
      <p>USD 硬上限：{usage.hard_usd_bound_available?'可用':'无保守请求价格保证'}。Exporter {text(object(usage.exporter).pending_items)} 项待发；失败记录 {Array.isArray(object(usage.exporter).failures)?(object(usage.exporter).failures as unknown[]).length:'未知'}。</p>
    </>:<p>账本尚未读取。</p>}</section>
    <section data-testid="observation-timings"><h3>时间来源</h3><p>客户端点击→收到受理回执：{client?client.receiptMs.toFixed(2)+' ms':'本次界面未采集'}；点击→客户端读取首个模型文本：{client?.firstTextMs!==undefined?client.firstTextMs.toFixed(2)+' ms':'本次界面未采集'}（含 400 ms 轮询观察延迟）。</p>
      <p>Engine 受理→终态（含排队与清理）：{milliseconds(timing?.engine_duration_nanoseconds)}；Harness 执行：{timing?.harness_wall_seconds??'未知'} s。</p>
      <p>瀑布使用本地单调时钟；并行 Span 耗时不相加。模型首 chunk 是客户端 SDK 观察时间。</p></section>
    <section id="trace-panel"><h3>Trace 树／瀑布 · 已加载 {data.spans.length}</h3><Trace items={data.spans} selected={selected?.span_id??null} select={span=>{setSelected(span);setOutput({reason:'选择后按需读取 stdout／stderr。'});}}/>{moreButton('spans')}
      {selected&&<div data-testid="selected-span"><h4>{selected.name} · {selected.state}</h4><p>trace {selected.trace_id} / span {selected.span_id}</p>
        <pre>{JSON.stringify(safeFacts(facts),null,2)}</pre><p>模型请求→SDK 首个文本 chunk：{(()=>{const start=nanos(selected.metadata?.start_monotonic_ns),first=nanos(selected.metadata?.first_client_text_chunk_monotonic_ns);return start!==null&&first!==null&&first>=start?milliseconds((first-start).toString()):'未知';})()}</p>
        <button disabled={busy||typeof selected.metadata?.execution_id!=='string'} onClick={()=>action(loadOutput)}>按需读取工具输出</button><p data-testid="output-state">{output.reason}</p>
        {Object.entries(output.streams??{}).map(([stream,value])=><div key={stream}><h4>{stream}</h4><pre className="observed-output">{value}</pre></div>)}</div>}
    </section>
    <section><h3>上下文版本 · 元数据</h3><VirtualList label="上下文版本" items={data.context} itemKey={item=>item.snapshot.snapshot_id}
      render={item=><button className="file-row" onClick={()=>setContext(item)}>v{item.version??'未知'} · {item.reason} · {item.tokens??'未知'} 估算 token · {item.capture_mode}</button>}/>{moreButton('context')}
      {context&&<div><p>快照 {context.snapshot.sha256} · 估算器 {text(context.metadata?.estimator_version)} · 消息 {text(contextFacts.message_count)} · 工具配对完整 {text(contextFacts.pairing_complete)} · 元数据截断 {text(contextFacts.truncated)}</p>
        <p>约束检查 {text(contextMeta.check_kind)}；仅核对原始消息存在，不据摘要文本断言语义全部保留。</p>
        <pre>{JSON.stringify({message_ids:contextFacts.message_ids,constraint_message_ids:contextFacts.constraint_message_ids,tool_pairs:contextFacts.tool_pairs,retained_constraint_message_ids:contextMeta.retained_constraint_message_ids},null,2)}</pre></div>}
    </section>
    <section id="evidence-panel"><h3>内部验证证据</h3><VirtualList label="验证证据" items={data.evidence} itemKey={item=>item.evidence_id}
      render={item=><button className="file-row" data-evidence-id={item.evidence_id} onClick={()=>setEvidenceId(item.evidence_id)}>{item.validity} · {item.reason} · {item.evidence_id.slice(-8)}</button>}/>{moreButton('evidence')}
      {selectedEvidence&&<div data-testid="evidence-comparison"><p>{selectedEvidence.validity} · {selectedEvidence.reason}</p>
        <p>验证时内容版本 {text(observed.content_revision)} → 当前内容版本 {text(current.content_revision)}</p><p>原 hash {text(observed.sha256)} → 当前 hash {text(current.sha256)}</p>
        <p>Harness workspace revision {text(selectedEvidence.metadata?.workspace_revision)}；原环境 {text(selectedEvidence.metadata?.environment_epoch)}；当前环境 {text(compare.environment_state)}。</p>
        <pre>{JSON.stringify(safeFacts(object(selectedEvidence.metadata?.evidence_metadata)),null,2)}</pre><p>内部验证不等于独立 grader 成绩。</p></div>}
      {evidenceId&&!selectedEvidence&&<p>引用证据未在已加载分页中；继续加载证据或重新核对。</p>}
    </section>
    <section><h3>完成决定与沙盒</h3><VirtualList label="完成与沙盒事件" items={data.events} itemKey={item=>item.event_id}
      render={item=><span>{item.event_type} · {text(item.attributes.reason??item.attributes.cleanup_state)}</span>}/>{moreButton('events')}
      {data.events.filter(item=>item.event_type.startsWith('completion.')).map(item=><details key={item.event_id} open={item.event_type==='completion.rejected'}><summary>{item.event_type} · {text(item.attributes.reason)}</summary>
        <button onClick={()=>{const span=data.spans.find(s=>s.trace_id===item.trace_id&&s.span_id===item.span_id);if(span){setSelected(span);setOutput({reason:'决定关联的真实 Span；工具输出单独按需读取。'});document.getElementById('trace-panel')?.scrollIntoView();}else setError('决定引用的 Span 未在当前分页加载。');}}>查看决定对应 Trace</button>
        <p>剩余修复 {text(item.attributes.repairs_remaining)} · workspace revision {text(item.attributes.workspace_revision)} / evidence revision {text(item.attributes.evidence_revision)}</p>
        {Array.isArray(item.attributes.evidence_ids)&&(item.attributes.evidence_ids as string[]).map(id=><button key={id} onClick={()=>{setEvidenceId(id);document.getElementById('evidence-panel')?.scrollIntoView();}}>查看证据 {id.slice(-8)}</button>)}
        <pre>{JSON.stringify((()=>{const report=object(item.attributes.completion_report);return {run_status:report.run_status,agent_assessment:report.agent_assessment,acceptance_status:report.acceptance_status,verification_status:report.verification_status,passed_checks:report.passed_checks,failed_checks:report.failed_checks,historical_checks:report.historical_checks,unmet_requirements:report.unmet_requirements,usage_complete:report.usage_complete,metadata_truncated:report.metadata_truncated,collection_counts:report.collection_counts};})(),null,2)}</pre></details>)}
      {!data.events.length&&<p>当前分页没有完成或沙盒事实；运行中或采集缺失时不能据此断言成功。</p>}
    </section>
  </>}</div>;
}
