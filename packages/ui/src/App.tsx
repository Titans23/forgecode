import React, { useEffect, useRef, useState } from 'react';
import type { DesktopStatus, DesktopOperations } from './transport';
import { Connections } from './pages/settings/Connections';
import { Workspace, type Project } from './pages/workspace/Workspace';
import { Observability, type ObservationTarget, type ClientLatency } from './pages/observability/Observability';
import { Evaluations } from './pages/evaluations/Evaluations';
import {Failures,type FailureTarget} from './pages/failures/Failures';
import { Icon } from './components/Icon';
import { GettingStarted } from './components/GettingStarted';

export function App({transport, webEntry = false}: {transport: DesktopOperations; webEntry?: boolean}) {
  const [status, setStatus] = useState<DesktopStatus | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [page, setPage] = useState<'home' | 'workspace' | 'observability' | 'evaluations' | 'failures' | 'settings' | 'diagnostics' | 'guide'>('home');
  const [failureTarget,setFailureTarget]=useState<FailureTarget|null>(null);
  const [observeTarget,setObserveTarget] = useState<ObservationTarget|null>(null), [selectedSession,setSelectedSession] = useState<string|null>(null);
  const [latency,setLatency] = useState<Record<string,ClientLatency>>({});
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [diagnostics, setDiagnostics] = useState<unknown>(null);
  const diagnosticDetails = useRef<HTMLDetailsElement>(null);
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
  function showDiagnostics(value: unknown) {
    setDiagnostics(value);
    if (diagnosticDetails.current) diagnosticDetails.current.open = true;
  }
  const project = projects.find(item => item.workspace_id === projectId) ?? null;
  const connected = status?.engine_state === 'ready';
  const labels = {home:'新任务', workspace:'Agent 工作区', observability:'运行观测', evaluations:'评测实验',
    failures:'失败案例', settings:'连接设置', diagnostics:'诊断', guide:'使用指南'} as const;
  const chooseProject = () => action(async () => {
    const selected = await transport.selectProject() as Project;
    if (selected?.workspace_id) { setProjectId(selected.workspace_id); setPage('workspace'); }
  });
  const projectName = (item: Project) => item.name ?? item.path?.split(/[\\/]/).filter(Boolean).at(-1) ?? '未命名项目';
  const navigation = (id: keyof typeof labels, icon: React.ComponentProps<typeof Icon>['name']) =>
    <button key={id} className={'nav ' + (page === id ? 'active' : '')} data-page={id}
      aria-current={page === id ? 'page' : undefined} onClick={() => setPage(id)}><Icon name={icon}/><span>{labels[id]}</span></button>;
  const executionMode = status?.mode === 'offline-demo' || status?.mode === 'web-scripted' ? '离线演示' : status?.mode === 'web-local-trusted' ? '可信本地 · 无 OS 隔离' : '严格执行模式';
  return <div className={'app ' + (webEntry ? 'web-app' : 'desktop-app')}>
    <header className="page-header">
      <div className="brand"><Icon name="code" size={20}/><span>ForgeCode</span><span className="preview-label">预览</span></div>
      <div className="header-context"><h1>{labels[page]}</h1>
        <div className={'connection ' + (connected ? 'online' : '')} role="status"><i/>{connected ? '已连接' : status?.failure ? '连接异常' : '正在连接'}</div></div>
    </header>
    <aside className="sidebar" aria-label="主导航">
      <div className="primary-nav">{navigation('home','plus')}{navigation('workspace','chat')}</div>
      <div className="caption">工作台</div>
      <nav aria-label="工作台">{navigation('observability','activity')}{navigation('evaluations','layers')}{navigation('failures','flag')}</nav>
      <div className="caption project-caption"><span>项目</span><button className="icon-button" aria-label="选择项目" title="选择项目"
        disabled={busy || !connected} onClick={chooseProject}><Icon name="plus" size={16}/></button></div>
      <div className="project-list">{projects.length ? projects.slice().reverse().map(item =>
        <button className={'project-link ' + (item.workspace_id === projectId ? 'selected' : '')} key={item.workspace_id}
          title={item.path ?? item.name} onClick={() => { setProjectId(item.workspace_id); setPage('workspace'); }}>
          <Icon name="folder" size={16}/><span>{projectName(item)}</span>{item.trust === 'inspect_only' && <small>只读</small>}</button>)
        : <p className="sidebar-empty">打开项目后，会显示在这里</p>}</div>
      <div className="sidebar-foot">{navigation('guide','help')}{navigation('settings','settings')}{navigation('diagnostics','shield')}
        <div className="mode-label">{executionMode}<span>V4</span></div>
      </div>
    </aside>
    <main>
      <div className={'page-content page-' + page}>
        {(error || status?.failure) && <div role="alert" className="notice">{error ?? status?.failure}</div>}
        {status?.readiness?.status === 'blocked' && page !== 'diagnostics' && <div className="notice readiness-notice"><Icon name="shield"/>
          <span>执行环境尚未就绪，当前可浏览项目和历史会话。</span><button className="text-button" onClick={() => setPage('diagnostics')}>查看原因 <Icon name="arrow" size={14}/></button></div>}
        {approvals.map(item => <section className="approval-card" key={item.approval_id}><h3>等待授权：{item.tool_name ?? '工具操作'}</h3><p>风险：{item.risk ?? '未知'}</p>
          <button disabled={busy} onClick={() => action(async () => { await transport.requestApproval(item.approval_id); })}>查看原生确认框</button></section>)}
        {page === 'home' && <div className="home">
          <div className="home-hero"><span className="hero-mark"><Icon name="code" size={32}/></span>
            <h2>今天，想完成什么？</h2><p>从一个项目开始，把想法变成下一步。</p>
            <div className="home-start"><div><Icon name="folder" size={22}/><div><strong>打开你的代码项目</strong><span>{webEntry ? '选择已由桌面或 CLI 登记的项目' : '选择一个文件夹，查看代码、开始任务'}</span></div></div>
              <button disabled={busy || !connected} onClick={chooseProject}>打开项目 <Icon name="arrow"/></button></div>
            <div className="quick-links"><button className="text-button" onClick={() => setPage('settings')}><Icon name="settings" size={16}/>连接模型</button>
              <span>·</span><button className="text-button" onClick={() => setPage('guide')}><Icon name="help" size={16}/>第一次使用？</button></div>
          </div>
          {projects.length > 0 && <div className="recent-projects"><div className="section-heading"><h3>继续最近的项目</h3><span>{projects.length} 个项目</span></div>
            {projects.slice(-4).reverse().map(item => <button className="recent-project" key={item.workspace_id} onClick={() => { setProjectId(item.workspace_id); setPage('workspace'); }}>
              <span className="project-icon"><Icon name="folder"/></span><span><strong>{projectName(item)}</strong><small>{item.path ?? '已注册项目'}</small></span><Icon name="arrow" size={16}/></button>)}</div>}
          <p className="home-note">项目 · 任务 · 每一步变化，都在这里。</p>
        </div>}
        {page === 'workspace' && <>{project?.trust === 'inspect_only' && <div className="workspace-authorization"><span>这个项目目前为只读。</span><button className="secondary" disabled={busy} onClick={() => action(async () => { await transport.authorizeWorkspace(project.workspace_id); })}>授权项目执行</button></div>}
          {status?.mode === 'offline-demo' && !status.session_id && <section><p>离线演示会在测试项目中执行真实文件操作与测试。</p><button disabled={busy} onClick={() => action(async () => {
            await transport.startDemo(); setStatus(await transport.status());
          })}>运行离线 Demo →</button></section>}
          <Workspace key={projectId} transport={transport} project={project} status={status} onSession={setSelectedSession}
            onObserve={value => { setObserveTarget(value); setPage('observability'); }}
            onLatency={(id,value) => setLatency(old => Object.fromEntries(Object.entries({...old,[id]:{...old[id],...value}}).slice(-1000)))}/></>}
        {page === 'observability' && <Observability key={(observeTarget?.turnId ?? selectedSession ?? status?.session_id) + ':' + observeTarget?.executionId}
          transport={transport} sessionId={selectedSession ?? status?.session_id ?? null} target={observeTarget} latency={latency}/>}
        {page === 'evaluations' && <Evaluations transport={transport} onFailure={target => {setFailureTarget(target);setPage('failures');}}/>}
        {page === 'failures' && <Failures transport={transport} initial={failureTarget}/>}
        {page === 'settings' && <Connections transport={transport}/>}
        {page === 'guide' && <GettingStarted connected={connected && !busy} openProject={chooseProject}
          openConnections={() => setPage('settings')} openDiagnostics={() => setPage('diagnostics')}/>}
        {page === 'diagnostics' && <section className="diagnostics"><div className="diagnostics-heading"><span className="intro-icon"><Icon name="shield" size={26}/></span><h2>执行环境</h2><p>查看本机连接与任务执行状态。</p></div>
          <div className="diagnostics-summary">
            <div><span>引擎连接</span><strong><i className={connected ? 'status-dot ready' : 'status-dot'}/>{connected ? '已连接' : status?.failure ? '连接异常' : '正在连接'}</strong></div>
            <div><span>执行环境</span><strong><i className={status?.readiness?.status === 'ready' ? 'status-dot ready' : 'status-dot'}/>{status?.readiness?.status === 'blocked' ? '尚未就绪' : status?.readiness?.status === 'ready' ? '已就绪' : status?.readiness?.status === 'degraded' ? '受限模式' : '待检测'}</strong></div>
            <div><span>当前模式</span><strong>{executionMode}</strong></div>
          </div>
          {status?.readiness?.status === 'blocked' && <p className="diagnostics-hint"><Icon name="shield" size={16}/>执行环境尚未就绪。当前可以浏览项目和历史会话；运行任务前，请先检查原生沙盒。</p>}
          <div className="actions"><button disabled={busy} onClick={() => action(async () => showDiagnostics(await transport.diagnostics()))}>刷新诊断</button>
          <button className="secondary" disabled={busy} onClick={() => action(async () => showDiagnostics(await transport.diagnoseSandbox()))}>诊断原生沙盒</button></div>
          <details className="diagnostic-details" ref={diagnosticDetails}><summary>查看详细诊断数据</summary><pre>{JSON.stringify(diagnostics ?? status, null, 2)}</pre></details>
          <details className="diagnostic-setup"><summary>原生沙盒设置</summary><p>安装需要系统权限，请先确认诊断结果和目标环境。</p>
            <button className="secondary" disabled={busy} onClick={() => action(async () => showDiagnostics(await transport.installSandbox()))}>安装原生沙盒（需原生确认）</button></details></section>}
      </div>
    </main>
  </div>;
}
