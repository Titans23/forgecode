import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { DesktopTransport, type DesktopStatus, type SessionSnapshot } from './transport';
import './style.css';

const transport = new DesktopTransport();
const states: Record<string, string> = { ready: 'Engine 已连接', starting: '正在连接 Engine', engine_lost: 'Engine 连接已断开',
  incompatible: '协议不兼容', finished: '已结束', running: '正在执行', queued: '等待执行', completed: '完成', cancelled: '已取消' };

function App() {
  const [status, setStatus] = useState<DesktopStatus | null>(null);
  const [snapshot, setSnapshot] = useState<SessionSnapshot | null>(null);
  const [projects, setProjects] = useState<Array<{ workspace_id: string; name?: string }>>([]);
  const [events, setEvents] = useState<Array<{ event_id: string; event_type: string }>>([]);
  const [gap, setGap] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const current = await transport.status();
        if (disposed) return;
        setStatus(current);
        if (current.engine_state === 'ready') {
          const workspaces = await transport.projects();
          const next = current.session_id ? await transport.session(current.session_id) : null;
          const batch = await transport.events();
          if (disposed) return;
          setProjects(workspaces.items); setSnapshot(next);
          setGap(old => old || batch.gap);
          setEvents(old => Array.from(new Map([...old, ...batch.events].map(event => [event.event_id, event])).values()).slice(-100));
        }
      } catch (reason) { if (!disposed) setError((reason as Error).message); }
      finally { if (!disposed) timer = setTimeout(refresh, 400); }
    }
    void refresh();
    return () => { disposed = true; clearTimeout(timer); };
  }, []);
  async function action(operation: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true); setError(null);
    try { await operation(); } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  const turn = snapshot?.turns[0];
  const connected = status?.engine_state === 'ready';
  return <div className="app"><aside><div className="brand"><span className="mark">F</span>ForgeCode</div>
    <div className="nav active">工作台</div><div className="caption">项目</div>
    {projects.length ? projects.map(project => <div className="project" key={project.workspace_id}>{project.name ?? project.workspace_id}</div>) :
      <div className="muted">尚未注册项目</div>}
    <div className="sidebar-foot">V4 · Python Harness<br/>桌面开发版本</div></aside>
    <main><header><div><div className="eyebrow">WORKSPACE</div><h1>开发工作台</h1></div>
      <div className={'connection ' + (connected ? 'online' : '')}><i/>{states[status?.engine_state ?? 'starting'] ?? status?.engine_state}</div></header>
    {(error || status?.failure) && <div role="alert" className="notice">{error ?? status?.failure}</div>}
    {status?.readiness?.status === 'blocked' && <div className="notice">执行环境未就绪，任务启动已禁用。请完成沙盒诊断与配置。</div>}
    <section className="task"><div className="eyebrow">CURRENT SESSION</div><h2>{status?.mode === 'offline-demo' ? '修复整数加法' : '开始一次开发任务'}</h2>
      <p>{status?.mode === 'offline-demo' ? '离线脚本模型驱动真实 Harness、文件工具和 unittest。执行结果来自 Engine。' : '客户端通过独立 Engine 管理会话和执行环境。'}</p>
      <div className="task-bottom"><div><span className="tag">{turn ? states[turn.state] ?? turn.state : '尚未开始'}</span>
        {turn?.outcome && <span className="tag result" data-testid="turn-outcome">{states[turn.outcome] ?? turn.outcome}</span>}</div>
        {status?.mode === 'offline-demo' && !status.session_id && <button disabled={!connected || busy} onClick={() => action(() => transport.startDemo())}>运行离线 Demo →</button>}
        {turn && ['running', 'queued', 'awaiting_approval'].includes(turn.state) && <button className="secondary" disabled={busy} onClick={() => action(() => transport.cancelTurn(turn.turn_id))}>取消任务</button>}</div>
      {turn && <code className="identity">{turn.turn_id}</code>}</section>
    <div className="grid"><section><h3>会话快照</h3>{snapshot ? <><div className="muted">{snapshot.session_id}</div><pre>{JSON.stringify(snapshot, null, 2)}</pre></> : <div className="empty">执行任务后显示持久化会话状态。</div>}</section>
      <section><h3>运行事件 <span className="count">{events.length}</span></h3>{gap && <div className="notice">显示队列存在历史缺口；会话快照已重新读取。</div>}
        {events.length ? <div className="events">{events.map(event => <div key={event.event_id}><span className="dot"/><span>{event.event_type}</span><code>{event.event_id.slice(-8)}</code></div>)}</div> : <div className="empty">等待 Engine 事件。</div>}</section></div>
    <footer>来源：{status?.mode === 'offline-demo' ? 'scripted · local-trusted' : 'desktop · strict'}<span>清理与任务状态以 Engine 记录为准</span></footer>
    </main></div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
