import React, { useEffect, useRef, useState } from 'react';
import type { SessionListResult, SessionSnapshotResult, WorkspaceFilesResult } from '@forgecode/contracts';
import type { DesktopOperations, DesktopStatus, ConnectionMetadata } from '../../transport';
import { mergeMessages, type Message } from '../../state/messages';
import { Composer } from './Composer';
import { VirtualList } from './VirtualList';
import { Diff } from '../../components/diff/Diff';

export interface Project { workspace_id: string; path?: string; revision?: number; trust?: string; name?: string }

export function Workspace({ transport, project, status, onSession }: {
  transport: DesktopOperations; project: Project | null; status: DesktopStatus | null; onSession(id: string): void;
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
  return <><section><h2>{project?.name ?? project?.path ?? '选择一个项目'}</h2>
    {error && <div role="alert" className="notice">{error}</div>}
    {project?.trust === 'inspect_only' && <div className="notice">项目处于 inspect-only。查看文件不会执行项目代码；发送任务前需原生授权。</div>}
    <div className="workspace-controls"><select aria-label="模型连接" value={connectionId} onChange={event => setConnectionId(event.target.value)}>
      <option value="">选择模型连接</option>{connections.map(item => <option disabled={!item.credential_present || item.locked} key={item.connection_id} value={item.connection_id}>{item.provider} · {item.requested_model}</option>)}</select>
      <button disabled={busy || !project || !connectionId} onClick={() => action(create)}>新建会话</button></div>
    {sessions && <><VirtualList label="会话列表" items={sessions.items} itemKey={item => item.session_id}
      render={item => <button className="file-row" onClick={() => { setRequestedTurn(null); setSessionId(item.session_id); }}>{item.created_at_utc} · {item.state} · {item.session_id.slice(-8)}</button>}/>
      {sessions.next_cursor && <button onClick={() => action(async () => { const page = await transport.sessions({ workspace_id: project!.workspace_id, cursor: sessions.next_cursor! }); setSessions({ ...page, items: [...sessions.items, ...page.items].slice(-10000) }); })}>加载更多会话</button>}</>}
  </section>
    {sessionId && <section><h3>会话 · {sessionId.slice(-8)}</h3><p>{turn?.state ?? 'idle'} <strong data-testid="turn-outcome">{turn?.outcome}</strong></p>
      <select aria-label="历史任务" value={requestedTurn ?? ''} onChange={event => setRequestedTurn(event.target.value || null)}>
        <option value="">最新任务</option>{snapshot?.turns.map(item => <option key={item.turn_id} value={item.turn_id}>{item.turn_id.slice(-8)} · {item.outcome ?? item.state}</option>)}</select>
      {snapshot?.has_more_turns && <p>此会话还有较早任务；当前页显示最新 100 项。</p>}
      {active && <button className="secondary" disabled={busy} onClick={() => action(async () => { await transport.cancelTurn(turn!.turn_id); })}>取消任务</button>}
      <div className="messages" data-testid="messages"><VirtualList label="会话消息" items={messages} itemKey={message => String(message.sequence)}
        render={message => <button className={'file-row message ' + message.kind} data-message-sequence={message.sequence} onClick={() => setSelectedMessage(message)}>
          {message.kind} {message.tool_name ?? ''} {message.status ?? ''} · {message.text.slice(0, 140)}</button>}/></div>
      <pre className="latest-message">{(selectedMessage ?? messages.at(-1))?.text}</pre>
      {snapshot?.message_limit_reached && <p>消息显示达到采集额度；任务终态与原始 Journal 仍可核查。</p>}
      <Composer disabled={busy || !!active || project?.trust !== 'execution_allowed' || status?.readiness?.status === 'blocked'}
        submit={text => action(async () => { await transport.submit({ session_id: sessionId, client_action_id: 'act-' + crypto.randomUUID(), input: [{ type: 'text', text }] }); setRequestedTurn(null); })}/>
      {displayTurn && <Diff key={displayTurn.turn_id + ':' + displayTurn.state} transport={transport} turnId={displayTurn.turn_id}/>}</section>}
    {files && <section><h3>项目文件 · 内容版本 {files.revision}</h3>{files.history_gap && <p>文件扫描有未访问对象或超出额度。</p>}
      <VirtualList label="项目文件" items={files.items} itemKey={item => item.relative_path}
        render={item => <button className="file-row" disabled={item.kind !== 'file'} onClick={() => action(() => read(item.relative_path))}>{item.relative_path} · {item.size_bytes ?? '目录'}</button>}/>
      {files.next_cursor && <button onClick={() => action(async () => { const page = await transport.files({ workspace_id: project!.workspace_id, cursor: files.next_cursor! }); setFiles({ ...page, items: [...files.items, ...page.items].slice(-10000) }); })}>加载更多文件</button>}
      {fileView && <><h4>{fileView.path}</h4><pre>{fileView.content}</pre>{!fileView.eof && <button onClick={() => action(() => read(fileView.path, fileView.offset))}>读取后续 64 KiB</button>}</>}
    </section>}
    <section><h3>实时事件</h3>{gap && <p>事件队列有历史缺口；已重新查询持久化消息与状态。</p>}<VirtualList label="运行事件" items={events}
      itemKey={event => event.event_id} render={event => <span>{event.event_type} · {event.event_id.slice(-8)}</span>}/></section>
  </>;
}
