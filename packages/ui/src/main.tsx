import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { DesktopTransport, type DesktopStatus } from './transport';
import { Connections } from './pages/settings/Connections';
import { Workspace, type Project } from './pages/workspace/Workspace';
import { Observability, type ObservationTarget, type ClientLatency } from './pages/observability/Observability';
import './style.css';

const transport = new DesktopTransport();
function App() {
  const [status, setStatus] = useState<DesktopStatus | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [page, setPage] = useState<'home' | 'workspace' | 'observability' | 'settings' | 'diagnostics'>('home');
  const [observeTarget,setObserveTarget] = useState<ObservationTarget|null>(null), [selectedSession,setSelectedSession] = useState<string|null>(null);
  const [latency,setLatency] = useState<Record<string,ClientLatency>>({});
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [diagnostics, setDiagnostics] = useState<unknown>(null);
  const [approvals, setApprovals] = useState<Array<{ approval_id: string; state: string; tool_name?: string; risk?: string }>>([]);
  useEffect(() => {
    let disposed = false, timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const current = await transport.status();
        if (disposed) return;
        setStatus(current);
        if (current.engine_state === 'ready') {
          const workspaces = await transport.projects();
          const pending = await transport.approvals();
          if (disposed) return;
          const items = workspaces.items as Project[];
          setProjects(items); setApprovals(pending.items.filter(item => item.state === 'pending'));
          if (current.session_id && !projectId) {
            const snapshot = await transport.sessionSnapshot({ session_id: current.session_id });
            if (!disposed) { setProjectId(snapshot.session.workspace_id); setPage('workspace'); }
          }
        }
      } catch (reason) { if (!disposed) setError((reason as Error).message); }
      finally { if (!disposed) timer = setTimeout(refresh, 1000); }
    }
    void refresh(); return () => { disposed = true; clearTimeout(timer); };
  }, [projectId]);
  async function action(operation: () => Promise<void>) {
    if (busy) return; setBusy(true); setError(null);
    try { await operation(); } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  const project = projects.find(item => item.workspace_id === projectId) ?? null;
  const connected = status?.engine_state === 'ready';
  return <div className="app"><aside><div className="brand"><span className="mark">F</span>ForgeCode</div>
    {([['home','首页／项目'],['workspace','Agent 工作区'],['observability','运行观测'],['settings','连接设置'],['diagnostics','诊断']] as const).map(([id, label]) =>
      <button className={'nav secondary ' + (page === id ? 'active' : '')} key={id} onClick={() => setPage(id)}>{label}</button>)}
    <div className="caption">最近项目</div><button className="secondary" disabled={busy || !connected} onClick={() => action(async () => {
      const selected = await transport.selectProject() as Project;
      if (selected?.workspace_id) { setProjectId(selected.workspace_id); setPage('workspace'); }
    })}>选择项目</button>
    {projects.slice().reverse().map(item => <button className="secondary project" key={item.workspace_id} onClick={() => { setProjectId(item.workspace_id); setPage('workspace'); }}>
      {item.path?.split(/[\\/]/).at(-1) ?? item.name ?? item.workspace_id.slice(-8)} · {item.trust === 'inspect_only' ? '仅查看' : '可执行'}</button>)}
    <div className="sidebar-foot">V4 · Python Harness<br/>{status?.mode === 'offline-demo' ? '脚本模型开发测试' : '严格执行模式'}</div></aside>
    <main><header><div><div className="eyebrow">FORGECODE</div><h1>{page === 'workspace' ? '开发工作区' : page === 'observability' ? '运行观测' : page === 'settings' ? '连接设置' : page === 'diagnostics' ? '诊断' : '项目'}</h1></div>
      <div className={'connection ' + (connected ? 'online' : '')}>{connected ? 'Engine 已连接' : status?.engine_state ?? '正在连接'}</div></header>
    {(error || status?.failure) && <div role="alert" className="notice">{error ?? status?.failure}</div>}
    {status?.readiness?.status === 'blocked' && <div className="notice">执行环境未就绪，任务启动已禁用。项目文件和历史会话仍可查看。</div>}
    {approvals.map(item => <section key={item.approval_id}><h3>等待授权：{item.tool_name ?? '工具操作'}</h3><p>风险：{item.risk ?? '未知'}</p>
      <button disabled={busy} onClick={() => action(async () => { await transport.requestApproval(item.approval_id); })}>查看原生确认框</button></section>)}
    {page === 'home' && <section><h2>最近注册的项目</h2><p>通过原生目录选择器打开项目；默认仅查看，执行前需单独授权。</p>
      {!projects.length && <p className="muted">尚未注册项目。</p>}
      {projects.slice().reverse().map(item => <p key={item.workspace_id}><button className="secondary" onClick={() => { setProjectId(item.workspace_id); setPage('workspace'); }}>{item.path ?? item.workspace_id}</button></p>)}</section>}
    {page === 'workspace' && <>{project?.trust === 'inspect_only' && <button disabled={busy} onClick={() => action(async () => { await transport.authorizeWorkspace(project.workspace_id); })}>授权项目执行</button>}
      {status?.mode === 'offline-demo' && !status.session_id && <section><p>离线脚本模型将调用真实 Harness、文件工具和 unittest。</p><button disabled={busy} onClick={() => action(async () => {
        await transport.startDemo(); setStatus(await transport.status());
      })}>运行离线 Demo →</button></section>}
      <Workspace key={projectId} transport={transport} project={project} status={status} onSession={setSelectedSession}
        onObserve={value=>{setObserveTarget(value);setPage('observability');}}
        onLatency={(id,value)=>setLatency(old=>Object.fromEntries(Object.entries({...old,[id]:{...old[id],...value}}).slice(-1000)))}/></>}
    {page === 'observability' && <Observability key={(observeTarget?.turnId??selectedSession??status?.session_id)+':'+observeTarget?.executionId}
      transport={transport} sessionId={selectedSession??status?.session_id??null} target={observeTarget} latency={latency}/>}
    {page === 'settings' && <Connections transport={transport}/>}
    {page === 'diagnostics' && <section><h2>Engine 状态</h2><button onClick={() => action(async () => setDiagnostics(await transport.diagnostics()))}>刷新诊断</button>
      <button className="secondary" disabled={busy} onClick={() => action(async () => setDiagnostics(await transport.diagnoseSandbox()))}>诊断原生沙盒</button>
      <button className="secondary" disabled={busy} onClick={() => action(async () => setDiagnostics(await transport.installSandbox()))}>安装原生沙盒（需原生确认）</button>
      <pre>{JSON.stringify(diagnostics ?? status, null, 2)}</pre><p>原生沙盒安装和签名验收结果另行记录；环境缺失时不会报告通过。</p></section>}
    <footer>来源：{status?.mode === 'offline-demo' ? 'scripted · local-trusted' : 'desktop · strict'}<span>任务与清理状态由 Engine 提供</span></footer>
    </main></div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
