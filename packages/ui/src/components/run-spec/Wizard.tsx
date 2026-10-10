import React from 'react';
import type { RunSpec, EvaluationDraftResult, EvaluationValidateResult } from '@forgecode/contracts';
import type { ConnectionMetadata } from '../../transport';
import { VirtualList } from '../../pages/workspace/VirtualList';
import { budgetProblem,type Choices } from '../../pages/evaluations/state';

export function WizardStep({step,choices:c,spec,connections,change,check,create,exportPlan,busy,draft,validation}: {
  step:number;choices:Choices;spec:RunSpec;connections:ConnectionMetadata[];change(value:Partial<Choices>):void;
  check():void;create():void;exportPlan():void;busy:boolean;draft:EvaluationDraftResult|null;validation:EvaluationValidateResult|null;
}) {
  const number=(key:keyof Choices,label:string,min=1,max=9007199254740991)=><label className="field">{label}<input type="number" min={min} max={max} value={c[key] as number} disabled={busy}
    onChange={e=>change({[key]:Number(e.target.value)})}/></label>;
  return <div className="wizard-step" data-wizard-step={step}>
    {step===0&&<><h3>1 · 版本与任务</h3><p>{spec.dataset.name} · {spec.dataset.revision}</p>
      <p className="muted">源码 {spec.source.commit==null?'未知 commit':String(spec.source.commit)} · snapshot {spec.source.source_snapshot.sha256.slice(0,16)} · lock {spec.source.dependency_lock_sha256.slice(0,16)}</p>
      <VirtualList label="选择预先计划的任务" items={spec.dataset.task_ids} itemKey={id=>id} render={id=><label className="task-choice"><input type="checkbox" checked={c.task_ids.includes(id)} disabled={busy}
        onChange={e=>change({task_ids:e.target.checked?[...c.task_ids,id]:c.task_ids.filter(v=>v!==id)})}/>{id} · {String(spec.dataset.task_revisions[id])}</label>}/>
      {number('repeats','每题独立重复次数',1,100)}<p>计划分母：{c.task_ids.length*c.repeats}；尚未启动也计入分母。</p></>}
    {step===1&&<><h3>2 · 模型与 Harness</h3><div className="form-grid"><label className="field field-wide">模型连接<select value={c.connection_id??''} disabled={busy} onChange={e=>change({connection_id:e.target.value||null})}>
      <option value="">{spec.model_mode==='scripted_mock'?'模板脚本模型（离线协议测试）':'请选择实际连接'}</option>
      {connections.map(v=><option key={v.connection_id} value={v.connection_id}>{v.provider} / {v.requested_model} · rev {v.revision}{v.locked||!v.credential_present?' · 凭证不可用':''}</option>)}</select></label>
      {number('max_output_tokens','模型输出 token 上限',1,32768)}{number('max_context_tokens','Harness 上下文 token 上限',4096,2000000)}
      <label className="field">temperature（留空表示 provider 默认）<input value={c.temperature??''} disabled={busy} onChange={e=>change({temperature:e.target.value||null})}/></label>
      <label className="field">top_p（留空表示 provider 默认）<input value={c.top_p??''} disabled={busy} onChange={e=>change({top_p:e.target.value||null})}/></label>
      <label className="field">reasoning 设置<select value={c.reasoning_effort??''} disabled={busy} onChange={e=>change({reasoning_effort:(e.target.value||null) as Choices['reasoning_effort']})}>
        {['','none','minimal','low','medium','high','xhigh','max'].map(v=><option key={v} value={v}>{v||'provider 默认'}</option>)}</select></label>
      {number('max_delivery_repairs','最多交付修复次数（共享父预算）',0,10)}</div>
      <div className="check-list"><label className="check"><input type="checkbox" checked={c.compaction_enabled} disabled={busy} onChange={e=>change({compaction_enabled:e.target.checked})}/>上下文压缩</label>
      <label className="check"><input type="checkbox" checked={c.explore_enabled} disabled={busy} onChange={e=>change({explore_enabled:e.target.checked})}/>Explore</label></div>
      <p className="muted">改变参数会创建新的不可变 run。脚本模型不产生公共模型成绩。</p></>}
    {step===2&&<><h3>3 · 执行目标、网络与缓存</h3><label className="field">执行目标<select value={c.target_platform} disabled={busy} onChange={e=>change({target_platform:e.target.value as Choices['target_platform']})}>
      <option value="windows-native">Windows 原生</option><option value="linux-native">Linux 原生</option><option value="official-environment">官方 Linux Docker 环境</option></select></label>
      <p className="muted">更换目标需要重新测量能力；不兼容计划可导出到匹配环境。</p>
      <div className="form-grid"><label className="field">任务直连网络<select value={c.network_mode} disabled={busy} onChange={e=>change({network_mode:e.target.value as Choices['network_mode'],allowed_domains:[]})}>
        <option value="deny_direct">禁止直连</option><option value="allowlist">明确域名允许列表</option></select></label>
      {c.network_mode==='allowlist'&&<label className="field field-wide">域名（每行一个）<textarea value={c.allowed_domains.join('\n')} disabled={busy} onChange={e=>change({allowed_domains:[...new Set(e.target.value.split(/\s+/).filter(Boolean))]})}/></label>}
      <label className="field">缓存<select value={c.cache_mode} disabled={busy} onChange={e=>change({cache_mode:e.target.value as Choices['cache_mode']})}>
        <option value="cold">私有冷缓存</option><option value="isolated_per_trial">每 trial 隔离</option><option value="shared_read_only">共享只读快照（须已有快照）</option></select></label>
      <label className="field">独立评分反馈<select value={c.feedback} disabled={busy} onChange={e=>change({feedback:e.target.value as Choices['feedback']})}>
        <option value="none">不可见</option><option value="benchmark_defined">按基准协议明确提供反馈</option></select></label></div>
      <p>并发：1。网络、缓存、反馈均来自本配置，不继承普通会话。</p></>}
    {step===3&&<><h3>4 · 确认不可变配置与预算</h3>
      <div className="form-grid">{number('attempt_wall_seconds','每 attempt 时间（秒）')}{number('trial_wall_seconds','每 trial 总时间（秒）',1,604800)}
      {number('max_model_requests_per_attempt','每 attempt 模型请求上限')}{number('max_tool_calls_per_attempt','每 attempt 工具调用上限')}
      {number('max_infrastructure_attempts','最多基础设施尝试（含首次）',1,10)}</div>
      <fieldset className="check-list"><legend>预先允许的基础设施重试类别</legend>{(['runner_unavailable','runner_crash','environment_setup','provider_unavailable','grader_infrastructure'] as const).map(v=><label className="check" key={v}>
        <input type="checkbox" checked={c.infrastructure_retry_categories.includes(v)} disabled={busy} onChange={e=>change({infrastructure_retry_categories:e.target.checked?[...c.infrastructure_retry_categories,v]:c.infrastructure_retry_categories.filter(k=>k!==v)})}/>{v}</label>)}</fieldset>
      <div className="form-grid"><label className="field">支出控制方式<select value={c.spend_policy} disabled={busy} onChange={e=>{
        const policy=e.target.value as Choices['spend_policy'];
        change({spend_policy:policy,spend_ceiling:policy==='human_unbounded'?null:c.spend_ceiling??'0'});
      }}><option value="unknown_usage_stop_next_request">有限金额，未知费用停止下一请求</option>
        <option value="preauthorization_with_reservation">有限金额，每次请求预留预算</option>
        <option value="human_unbounded">金额无上限（须单独授权）</option></select></label>
      {c.spend_policy!=='human_unbounded'&&<label className="field">总支出上限（USD）<input value={c.spend_ceiling??''} disabled={busy} onChange={e=>change({spend_ceiling:e.target.value})}/></label>}</div>
      <p>计划 {c.task_ids.length*c.repeats} 个 trial，最多 {c.task_ids.length*c.repeats*c.max_infrastructure_attempts} 次 attempt。修复共享父预算；重试保留全部费用与首次结果。</p>
      <p>结果选择：最后一个获准 attempt。{c.spend_policy==='human_unbounded'?'金额无上限，未知费用仍记为未知；':c.spend_policy==='preauthorization_with_reservation'?'每次请求须先预留预算；':'未知费用将停止下一请求；'}真实 API 执行另需预算授权与支出控制就绪。</p>
      {budgetProblem(c)&&<p role="alert" className="notice">{budgetProblem(c)}</p>}
      <button disabled={busy||!!budgetProblem(c)} onClick={check}>检查兼容性与预算</button>
      {draft&&<p>配置 hash：<code>{draft.spec_hash}</code> · {draft.configuration_origin} · planned {draft.planned_trials}</p>}
      {validation&&<><p>{validation.compatible?'当前配置兼容':'执行 blocked；可保存和导出计划'}</p>{validation.issues.slice(0,100).map((i,n)=><p key={n} className="notice">{i.task_id??'整体'} · {i.kind} · {i.message}</p>)}
        <div className="action-row"><button disabled={busy||!draft} onClick={create}>保存不可变计划</button><button className="secondary" disabled={busy||!draft} onClick={exportPlan}>保存计划并导出配置</button></div></>}
    </>}
  </div>;
}
