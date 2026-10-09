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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
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
  const labels = {home:'新任务', workspace:'工作区', observability:'运行观测', evaluations:'评测实验',
    failures:'失败案例', settings:'模型连接', diagnostics:'环境诊断', guide:'使用指南'} as const;
  const chooseProject = () => action(async () => {
    const selected = await transport.selectProject() as Project;
    if (selected?.workspace_id) { setProjectId(selected.workspace_id); setPage('workspace'); }
  });
  const projectName = (item: Project) => item.name ?? item.path?.split(/[\\/]/).filter(Boolean).at(-1) ?? '未命名项目';
  const navigation = (id: keyof typeof labels, icon: React.ComponentProps<typeof Icon>['name']) =>
    <button key={id} className={'nav ' + (page === id ? 'active' : '')} data-page={id}
      aria-current={page === id ? 'page' : undefined} onClick={() => setPage(id)}><Icon name={icon}/><span>{labels[id]}</span></button>;
  const localExecution = status?.mode === 'desktop-local-trusted' || status?.mode === 'web-local-trusted';
  const executionMode = status?.mode === 'offline-demo' || status?.mode === 'web-scripted' ? '离线演示' : localExecution ? '本机执行 · 无 OS 隔离' : '严格沙盒模式';
  const readinessReason = status?.readiness?.reasons?.[0];
  const blockedReason = readinessReason === 'strict_sandbox_backend_not_ready'
    ? '当前版本的严格沙盒尚未就绪，重复诊断或安装不会解除任务限制。'
    : readinessReason === 'previous_execution_requires_reconciliation' || readinessReason === 'previous_cleanup_unconfirmed'
      ? '上次任务的执行或清理尚未确认，请在工作区核对执行状态。'
      : readinessReason === 'database_read_only' ? '本地数据处于只读状态，请检查诊断详情。' : '执行环境未就绪，请查看诊断详情。';
  return <div className={'app ' + (webEntry ? 'web-app' : 'desktop-app') + (sidebarCollapsed ? ' sidebar-collapsed' : '')}>
    <header className="page-header">
      <div className="header-tools">
        <button className="icon-button" data-testid="sidebar-toggle" aria-label={sidebarCollapsed ? '展开侧栏' : '收起侧栏'}
          title={sidebarCollapsed ? '展开侧栏' : '收起侧栏'} aria-expanded={!sidebarCollapsed} aria-controls="app-sidebar"
          onClick={() => setSidebarCollapsed(value => !value)}><Icon name="sidebar"/></button>
        <span className="wordmark">ForgeCode</span>
        <button className="icon-button header-new-task" aria-label="新任务" title="新任务" onClick={() => setPage('home')}><Icon name="compose"/></button>
      </div>
      <div className="header-context">
        <div className="header-heading">{page === 'workspace' && project && <><span className="header-project" title={project.path ?? projectName(project)}>{projectName(project)}</span><span className="header-separator" aria-hidden="true">/</span></>}
          <h1>{labels[page]}</h1></div>
        <button className="header-open-project" aria-label="打开项目" title="打开项目" disabled={busy || !connected} onClick={chooseProject}>
          <Icon name="folder" size={15}/><span>打开项目</span></button>
      </div>
    </header>
    <aside id="app-sidebar" className="sidebar" aria-label="主导航" hidden={sidebarCollapsed}>
      <div className="primary-nav">{navigation('home','compose')}{navigation('workspace','chat')}</div>
      <div className="caption">工作台</div>
      <nav aria-label="工作台">{navigation('observability','activity')}{navigation('evaluations','layers')}{navigation('failures','issue')}</nav>
      <div className="caption project-caption"><span>项目</span><button className="icon-button" aria-label="选择项目" title="选择项目"
        disabled={busy || !connected} onClick={chooseProject}><Icon name="plus" size={16}/></button></div>
      <div className="project-list">{projects.length ? projects.slice().reverse().map(item =>
        <button className={'project-link ' + (item.workspace_id === projectId ? 'selected' : '')} key={item.workspace_id}
          title={item.path ?? item.name} onClick={() => { setProjectId(item.workspace_id); setPage('workspace'); }}>
          <Icon name="folder" size={16}/><span>{projectName(item)}</span>{item.trust === 'inspect_only' && <small>只读</small>}</button>)
        : <div className="sidebar-empty"><Icon name="folder" size={20}/><span>暂无项目</span></div>}</div>
      <div className="sidebar-foot">{navigation('guide','book')}{navigation('settings','link')}{navigation('diagnostics','shield')}
        <div className="sidebar-status"><div className={'connection ' + (connected ? 'online' : '')} role="status" title="客户端与本地服务的连接状态"><i/>{connected ? '本地已连接' : status?.failure ? '连接异常' : '正在连接'}</div><span>V4 预览</span></div>
        <div className="mode-label">{executionMode}</div>
      </div>
    </aside>
    <main>
      <div className={'page-content page-' + page}>
        {(error || status?.failure) && <div role="alert" className="notice">{error ?? status?.failure}</div>}
        {status?.readiness?.status === 'blocked' && page !== 'diagnostics' && <div className="notice readiness-notice"><Icon name="shield"/>
          <span>{blockedReason}</span><button className="text-button" onClick={() => setPage('diagnostics')}>查看原因 <Icon name="arrow" size={14}/></button></div>}
        {localExecution && page === 'workspace' && <div className="notice"><Icon name="shield"/>
          <span>本机执行没有 OS 沙盒隔离，命令以当前用户权限运行。</span></div>}
        {approvals.map(item => <section className="approval-card" key={item.approval_id}><h3>等待授权：{item.tool_name ?? '工具操作'}</h3><p>风险：{item.risk ?? '未知'}</p>
          <button disabled={busy} onClick={() => action(async () => { await transport.requestApproval(item.approval_id); })}>查看授权</button></section>)}
        {page === 'home' && <div className="home">
          <div className="home-hero"><div className="hero-kicker" aria-hidden="true"><span className="hero-mark"><Icon name="code" size={18}/></span></div>
            <h2>今天，想构建什么？</h2>
          </div>
          <div className="home-start"><div><span className="start-icon"><Icon name="folder" size={22}/></span><div><strong>{webEntry ? '已注册项目' : '本地项目'}</strong>{webEntry && <span>新项目需在桌面端打开</span>}</div></div>
            <button disabled={busy || !connected} onClick={chooseProject}>打开项目 <Icon name="arrow" size={16}/></button></div>
          <div className="quick-links"><button className="text-button" onClick={() => setPage('settings')}><Icon name="link" size={16}/>连接模型</button>
            <button className="text-button" onClick={() => setPage('guide')}><Icon name="book" size={16}/>使用指南</button></div>
          {projects.length > 0 && <div className="recent-projects"><div className="section-heading"><h3>最近项目</h3></div>
            <div className="recent-grid">{projects.slice(-4).reverse().map(item => <button className="recent-project" key={item.workspace_id} title={item.path ?? projectName(item)} onClick={() => { setProjectId(item.workspace_id); setPage('workspace'); }}>
              <span className="project-icon"><Icon name="folder"/></span><span><strong>{projectName(item)}</strong><small>{item.path ?? '已注册项目'}</small></span><Icon name="arrow" size={15}/></button>)}</div></div>}
        </div>}
        {page === 'workspace' && <>{project?.trust === 'inspect_only' && <div className="workspace-authorization"><span>项目只读</span><button className="secondary" disabled={busy} onClick={() => action(async () => { await transport.authorizeWorkspace(project.workspace_id); })}>授权项目执行</button></div>}
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
        {page === 'diagnostics' && <section className="diagnostics"><div className="diagnostics-heading"><span className="intro-icon"><Icon name="shield" size={26}/></span><h2>执行环境</h2></div>
          <div className="diagnostics-summary">
            <div><span>引擎连接</span><strong><i className={connected ? 'status-dot ready' : 'status-dot'}/>{connected ? '已连接' : status?.failure ? '连接异常' : '正在连接'}</strong></div>
            <div><span>执行环境</span><strong><i className={status?.readiness?.status === 'ready' ? 'status-dot ready' : 'status-dot'}/>{status?.readiness?.status === 'blocked' ? '尚未就绪' : status?.readiness?.status === 'ready' ? '已就绪' : status?.readiness?.status === 'degraded' ? localExecution ? '可执行（无隔离）' : '演示环境' : '待检测'}</strong></div>
            <div><span>当前模式</span><strong>{executionMode}</strong></div>
          </div>
          {status?.readiness?.status === 'blocked' && <p className="diagnostics-hint"><Icon name="shield" size={16}/>{blockedReason}</p>}
          {localExecution && <p className="diagnostics-hint">命令可访问当前用户可访问的文件和网络。项目授权与操作审批仍保留，本模式不代表沙盒验收通过。</p>}
          <div className="actions"><button disabled={busy} onClick={() => action(async () => showDiagnostics(await transport.diagnostics()))}><Icon name="refresh" size={16}/>刷新诊断</button>
          <button className="secondary" disabled={busy} onClick={() => action(async () => showDiagnostics(await transport.diagnoseSandbox()))}><Icon name="shield" size={16}/>诊断原生沙盒</button>
          {!webEntry && status?.mode !== 'offline-demo' && <button className="secondary" disabled={busy || !connected} onClick={() => action(async () => { await transport.chooseExecutionMode(); })}>切换执行模式…</button>}</div>
          {!webEntry && status?.mode !== 'offline-demo' && <p className="muted">切换需确认并重启客户端；历史会话保留原策略，请新建会话使用新模式。</p>}
          <details className="diagnostic-details" ref={diagnosticDetails}><summary>诊断详情</summary><pre>{JSON.stringify(diagnostics ?? status, null, 2)}</pre></details>
          <details className="diagnostic-setup"><summary>原生沙盒设置</summary><p>安装需要系统权限，请先确认诊断结果和目标环境。</p>
            <button className="secondary" disabled={busy} onClick={() => action(async () => showDiagnostics(await transport.installSandbox()))}>安装原生沙盒（需原生确认）</button></details></section>}
      </div>
    </main>
  </div>;
}
