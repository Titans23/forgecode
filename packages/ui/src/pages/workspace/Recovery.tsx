import React,{useState} from 'react';
import type {RecoveryInspectResult} from '@forgecode/contracts';
import type {DesktopOperations} from '../../transport';

export function RecoveryDetails({report}:{report:RecoveryInspectResult}) {
  return <div data-testid="recovery-report"><h4>恢复对账 · {report.state}</h4>
    <p>{report.unknown_side_effects?'执行结果有未确认部分，禁止自动重放。':'当前没有未确认的执行结果。'}</p>
    <p>取消 {report.cancel_state} · 清理 {report.cleanup_state} · 期限 {report.deadline_status}</p>
    <p>Journal {report.journal.state} · 已投影 {report.journal.projected_records} / {report.journal.records} 条</p>
    {report.journal.basis_scope==='journal'&&<p>旧工具记录按整个 Journal 核对，未声称归属于本轮。</p>}
    <p>进程归属 {report.process_ownership} · 历史 PID 不用于终止进程。</p>
    {!!report.journal.unmatched_intents.length&&<p>结果未确认的 intent：{report.journal.unmatched_intents.join(' / ')}</p>}
    {report.blockers.length>0&&<div className="notice" role="status">{report.blockers.join(' / ')}</div>}
    <p>产物核对范围 {report.artifacts.scope} · {report.artifacts.items.length} 项{report.artifacts.history_gap?' · 有未扫描项':''}</p>
    {report.artifacts.items.filter(item=>item.state!=='valid').map(item=><p key={item.artifact_id}>{item.artifact_id} · {item.state}</p>)}
    <p>记录时间 {report.observed_at_utc} · {report.startup_observation_id??'本次只读核对'}</p>
  </div>;
}

export function Recovery({transport,turnId}:{transport:DesktopOperations;turnId:string}) {
  const [report,setReport]=useState<RecoveryInspectResult|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  async function inspect() {
    if(busy)return;
    setBusy(true);setError('');
    try{setReport(await transport.recoveryInspect({turn_id:turnId}));}
    catch(reason){setError((reason as Error).message);}
    finally{setBusy(false);}
  }
  return <div><button className="secondary" data-testid="inspect-recovery" disabled={busy} onClick={inspect}>查看恢复对账</button>
    {error&&<p role="alert">{error}</p>}{report&&<RecoveryDetails report={report}/>}</div>;
}
