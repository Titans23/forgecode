import React, { useEffect, useRef, useState } from 'react';
import type { SessionListResult, SessionSnapshotResult, WorkspaceFilesResult } from '@forgecode/contracts';
import type { DesktopOperations, DesktopStatus, ConnectionMetadata } from '../../transport';
import { mergeMessages, type Message } from '../../state/messages';
import { Composer } from './Composer';
import { VirtualList } from './VirtualList';
import { Diff } from '../../components/diff/Diff';
import { Recovery } from './Recovery';
import type { ObservationTarget, ClientLatency } from '../observability/Observability';
import { Icon } from '../../components/Icon';

const statusLabels: Record<string,string> = {idle:'等待开始',queued:'等待执行',running:'正在处理',awaiting_approval:'等待授权',
  cancel_requested:'正在取消',reconciling:'正在核对',finished:'已结束',completed:'已完成',failed:'执行失败',cancelled:'已取消',blocked:'需要处理',
  timed_out:'已超时',budget_exhausted:'已达预算上限',indeterminate:'结果待核对'};

export interface Project { workspace_id: string; path?: string; revision?: number; trust?: string; name?: string }

export function Workspace({ transport, project, status, onSession, onObserve, onLatency }: {
  transport: DesktopOperations; project: Project | null; status: DesktopStatus | null; onSession(id: string): void;
  onObserve(value:ObservationTarget):void; onLatency(id:string,value:ClientLatency):void;
}) {
  const [sessions, setSessions] = useState<SessionListResult | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(status?.session_id ?? null);
  const [snapshot, setSnapshot] = useState<SessionSnapshotResult | null>(null);
  const [requestedTurn, setRequestedTurn] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [connections, setConnections] = useState<ConnectionMetadata[]>([]);
  const [connectionId, setConnectionId] = useState('');
  const [files, setFiles] = useState<WorkspaceFilesResult | null>(null);
  const [fileView, setFileView] = useState<{ path: string; content: string; offset: number; eof: boolean; revision: number } | null>(null);
  const [selectedMessage, setSelectedMessage] = useState<Message | null>(null);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const sequence = useRef(0), currentTurn = useRef<string | null>(null), high = useRef(0n);
  const clicked = useRef<{turnId:string;at:number;receiptMs:number;first:boolean}|null>(null);
  const [events, setEvents] = useState<Array<{ event_id: string; event_type: string }>>([]);
  const [gap, setGap] = useState(false);
  async function action(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(null);
    try { await operation(); } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  useEffect(() => {
    let disposed = false;
    setSessions(null); setFiles(null); setFileView(null);
    if (!project) return;
    void Promise.all([transport.sessions({ workspace_id: project.workspace_id }), transport.files({ workspace_id: project.workspace_id }), transport.connections()])
      .then(([page, files, connections]) => { if (!disposed) {
        setSessions(page); setFiles(files); setConnections(connections.items);
        setConnectionId(connections.items.find(item => item.credential_present && !item.locked)?.connection_id ?? '');
        if (!page.items.some(item => item.session_id === sessionId)) setSessionId(page.items.at(-1)?.session_id ?? null);
      } }).catch(reason => { if (!disposed) setError(reason.message); });
    return () => { disposed = true; };
  }, [project?.workspace_id]);
  useEffect(() => {
    let disposed = false, timer: ReturnType<typeof setTimeout>;
    sequence.current = 0; currentTurn.current = null; high.current = 0n; setMessages([]); setSelectedMessage(null); setSnapshot(null); setEvents([]);
    async function refresh() {
      if (!sessionId) return;
      try {
        const next = await transport.sessionSnapshot({ session_id: sessionId, after_sequence: sequence.current,
          ...(requestedTurn ? { turn_id: requestedTurn } : {}) });
        if (disposed) return;
        if (currentTurn.current !== next.turn_id) {
          currentTurn.current = next.turn_id; sequence.current = 0; setMessages([]);
          if (next.messages[0]?.sequence !== 1) { timer = setTimeout(refresh, 0); return; }
        }
        setSnapshot(next); onSession(sessionId);
        if(clicked.current?.turnId===next.turn_id&&!clicked.current.first&&next.messages.some(message=>message.kind==='assistant'&&message.text.length>0)) {
          clicked.current.first=true;onLatency(next.turn_id,{receiptMs:clicked.current.receiptMs,firstTextMs:performance.now()-clicked.current.at});
        }
        setMessages(old => mergeMessages(old, next.messages));
        sequence.current = Math.max(sequence.current, ...next.messages.map(item => item.sequence));
        const previousHigh = high.current || BigInt(next.snapshot_cursor);
        high.current = BigInt(next.snapshot_cursor);
        const batch = await transport.events();
        if (disposed) return;
        setGap(old => old || batch.gap);
        const later = batch.events.filter(event => BigInt(String(event.store_seq ?? '0')) > previousHigh);
        setEvents(old => Array.from(new Map([...old, ...later].map(event => [event.event_id, event])).values()).slice(-10000));
        timer = setTimeout(refresh, next.has_more_messages ? 0 : 400);
      } catch (reason) { if (!disposed) { setError((reason as Error).message); timer = setTimeout(refresh, 1000); } }
    }
    void refresh(); return () => { disposed = true; clearTimeout(timer); };
  }, [sessionId, requestedTurn]);
  const turn = snapshot?.turns[0], active = turn && ['queued','running','awaiting_approval','cancel_requested','reconciling'].includes(turn.state);
  const displayTurn = snapshot?.turns.find(item => item.turn_id === snapshot.turn_id);
  async function create() {
    if (!project || !connectionId) return;
    const created = await transport.createSession({ workspace_id: project.workspace_id, expected_workspace_revision: project.revision ?? 0,
      connection_id: connectionId, client_action_id: 'act-' + crypto.randomUUID() });
    setSessionId(created.session_id);
    setRequestedTurn(null);
    setSessions(await transport.sessions({ workspace_id: project.workspace_id }));
  }
  async function read(path: string, offset=0) {
    if (!project) return;
    const refreshed = await transport.files({ workspace_id: project.workspace_id });
    setFiles(refreshed);
    const result = await transport.readProjectFile({ workspace_id: project.workspace_id, relative_path: path,
      expected_revision: offset ? fileView!.revision : refreshed.revision!, offset, length: 65536 });
    const bytes = Uint8Array.from(atob(result.data_base64), value => value.charCodeAt(0));
    const content = bytes.includes(0) ? '二进制数据；本页读取 64 KiB。' : new TextDecoder().decode(bytes);
    setFileView({ path, content, offset: offset + bytes.length, eof: result.eof, revision: result.revision });
  }
  const visibleMessage = selectedMessage ?? messages.at(-1);
  const projectName = project?.name ?? project?.path?.split(/[\\/]/).filter(Boolean).at(-1) ?? '选择一个项目';
  const conversationStatus = turn?.outcome ?? turn?.state ?? 'idle';
  return <><section className="workspace-overview">
    {error && <div role="alert" className="notice">{error}</div>}
    <div className="workspace-heading"><div><h2>{projectName}</h2>
      <p className="workspace-path" title={project?.path}>{project?.path ?? '从左侧选择项目，或回到新任务页面打开一个文件夹。'}</p></div>
      <div className="workspace-controls"><select aria-label="模型连接" value={connectionId} onChange={event => setConnectionId(event.target.value)}>
        <option value="">选择模型连接</option>{connections.map(item => <option disabled={!item.credential_present || item.locked} key={item.connection_id} value={item.connection_id}>{item.provider} · {item.requested_model}</option>)}</select>
        <button disabled={busy || !project || !connectionId} onClick={() => action(create)}><Icon name="plus" size={16}/>新建会话</button></div></div>
    {sessions && <details className="workspace-sessions"><summary>历史会话 · {sessions.items.length}</summary>
      <VirtualList label="会话列表" emptyText="创建会话后，可以在这里继续" items={sessions.items} itemKey={item => item.session_id}
        render={item => <button className="file-row" onClick={() => { setRequestedTurn(null); setSelectedMessage(null); setSessionId(item.session_id); }}>{item.created_at_utc} · {item.state} · {item.session_id.slice(-8)}</button>}/>
      {sessions.next_cursor && <button className="secondary" onClick={() => action(async () => { const page = await transport.sessions({ workspace_id: project!.workspace_id, cursor: sessions.next_cursor! }); setSessions({ ...page, items: [...sessions.items, ...page.items].slice(-10000) }); })}>加载更多会话</button>}</details>}
    {project && !sessionId && <p className="muted">选择模型后新建会话。</p>}
  </section>
    {sessionId && <section className="conversation"><div className="conversation-header"><h3 title={sessionId}>任务对话</h3>
      <span className="conversation-status" data-state={conversationStatus} data-testid="turn-outcome" data-outcome={turn?.outcome ?? ''}><i/>{statusLabels[conversationStatus] ?? conversationStatus}</span></div>
      <div className="conversation-tools"><select aria-label="历史任务" value={requestedTurn ?? ''} onChange={event => { setSelectedMessage(null); setRequestedTurn(event.target.value || null); }}>
        <option value="">最新任务</option>{snapshot?.turns.map(item => <option key={item.turn_id} value={item.turn_id}>{item.turn_id.slice(-8)} · {item.outcome ?? item.state}</option>)}</select>
      {displayTurn && <button className="secondary" title="查看本轮调用、耗时与执行记录" onClick={()=>onObserve({turnId:displayTurn.turn_id})}><Icon name="activity" size={15}/>运行详情</button>}
      {active && <button className="secondary" disabled={busy} onClick={() => action(async () => { await transport.cancelTurn(turn!.turn_id); })}>取消任务</button>}
      {displayTurn && <Recovery key={displayTurn.turn_id} transport={transport} turnId={displayTurn.turn_id}/>}</div>
      {snapshot?.has_more_turns && <p className="muted">此会话还有较早任务；当前页显示最新 100 项。</p>}
      {messages.length > 0 ? <><div className="assistant-response"><div className="message-author"><span><Icon name={visibleMessage?.kind === 'tool' ? 'code' : 'chat'} size={15}/></span>{visibleMessage?.kind === 'tool' ? '工具结果' : visibleMessage?.kind === 'user' ? '你' : 'ForgeCode'}</div>
        <pre className={'latest-message' + (visibleMessage?.kind === 'tool' ? ' tool-message' : '')}>{visibleMessage?.text}</pre></div>
        <details className="message-history"><summary>消息与工具记录 · {messages.length}</summary>
        <div className="messages" data-testid="messages"><VirtualList label="会话消息" items={messages} itemKey={message => String(message.sequence)}
          render={message => <button className={'file-row message ' + message.kind} data-message-sequence={message.sequence} onClick={() => setSelectedMessage(message)}>
            {message.kind} {message.tool_name ?? ''} {message.status ?? ''} · {message.text.slice(0, 140)}</button>}/></div></details>
        {selectedMessage && <button className="text-button" onClick={() => setSelectedMessage(null)}>回到最新消息</button>}</>
        : null}
      {selectedMessage?.kind==='tool'&&displayTurn&&<button className="secondary" data-testid="tool-trace-link" onClick={()=>onObserve({turnId:displayTurn.turn_id,
        ...(selectedMessage.execution_id?{executionId:selectedMessage.execution_id}:{})})}>查看工具详情</button>}
      {snapshot?.message_limit_reached && <p>消息显示达到采集额度；任务终态与原始 Journal 仍可核查。</p>}
      <Composer disabled={busy || !!active || project?.trust !== 'execution_allowed' || status?.readiness?.status === 'blocked'}
        disabledReason={active ? '任务正在进行' : busy ? '正在处理…' : project?.trust !== 'execution_allowed' ? '请先授权项目执行' : status?.readiness?.status === 'blocked' ? '环境未就绪，请查看诊断' : undefined}
        submit={text => action(async () => { const at=performance.now();const accepted=await transport.submit({ session_id: sessionId, client_action_id: 'act-' + crypto.randomUUID(), input: [{ type: 'text', text }] });
          const receiptMs=performance.now()-at;clicked.current={turnId:accepted.turn_id,at,receiptMs,first:false};onLatency(accepted.turn_id,{receiptMs});setSelectedMessage(null);setRequestedTurn(null); })}/>
      {displayTurn && <Diff key={displayTurn.turn_id + ':' + displayTurn.state} transport={transport} turnId={displayTurn.turn_id}/>}</section>}
    {files && <details className="workspace-panel"><summary>项目文件 · 内容版本 {files.revision}</summary>{files.history_gap && <p>文件扫描有未访问对象或超出额度。</p>}
      <VirtualList label="项目文件" emptyText="暂无可显示的项目文件" items={files.items} itemKey={item => item.relative_path}
        render={item => <button className="file-row" disabled={item.kind !== 'file'} onClick={() => action(() => read(item.relative_path))}>{item.relative_path} · {item.size_bytes ?? '目录'}</button>}/>
      {files.next_cursor && <button className="secondary" onClick={() => action(async () => { const page = await transport.files({ workspace_id: project!.workspace_id, cursor: files.next_cursor! }); setFiles({ ...page, items: [...files.items, ...page.items].slice(-10000) }); })}>加载更多文件</button>}
      {fileView && <><h4>{fileView.path}</h4><pre>{fileView.content}</pre>{!fileView.eof && <button onClick={() => action(() => read(fileView.path, fileView.offset))}>读取后续 64 KiB</button>}</>}
    </details>}
    <details className="workspace-panel"><summary>实时事件 · {events.length}</summary>{gap && <p>事件队列有历史缺口；已重新查询持久化消息与状态。</p>}<VirtualList label="运行事件" emptyText="任务运行后，执行记录会显示在这里" items={events}
      itemKey={event => event.event_id} render={event => <span>{event.event_type} · {event.event_id.slice(-8)}</span>}/></details>
  </>;
}
