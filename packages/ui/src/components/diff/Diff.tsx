import React, { useEffect, useState } from 'react';
import type { WorkspaceDiffResult, WorkspaceDiffFileResult } from '@forgecode/contracts';
import type { DesktopOperations } from '../../transport';
import { VirtualList } from '../../pages/workspace/VirtualList';

export function Diff({ transport, turnId }: { transport: DesktopOperations; turnId: string }) {
  const [view, setView] = useState<WorkspaceDiffResult | null>(null);
  const [file, setFile] = useState<WorkspaceDiffFileResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function refresh(cursor?: string) {
    try {
      const next = await transport.diff({ turn_id: turnId, ...(cursor ? { cursor } : {}) });
      setView(old => cursor && old ? { ...next, items: [...old.items, ...next.items].slice(-10000) } : next);
      if (!cursor) setFile(null);
      setError(null);
    } catch (reason) { setError((reason as Error).message); }
  }
  useEffect(() => { void refresh(); }, [turnId]);
  async function open(relative: string) {
    if (!view) return;
    try { setFile(await transport.diffFile({ turn_id: turnId, relative_path: relative, expected_revision: view.revision })); setError(null); }
    catch (reason) { setFile(null); setError((reason as Error).message); }
  }
  return <section data-testid="task-diff"><h3>本轮 Diff <button className="secondary" onClick={() => refresh()}>刷新</button></h3>
    {error && <div role="alert" className="notice">{error}。文件可能已被外部修改，请刷新。</div>}
    {view && <><p>任务基线 {view.baseline_revision} · 当前内容版本 {view.revision}</p>
      {view.history_gap && <div className="notice">基线或扫描不完整；恢复预览已受限。</div>}
      <details><summary>任务前已有修改：{view.original_dirty.length}{view.original_dirty_has_more ? '+' : ''}</summary>
        {view.original_dirty_status === 'unavailable' ? <p>Git 原有修改状态不可用。</p> : view.original_dirty.map(item => <pre key={item.relative_path}>{item.status} {item.relative_path}</pre>)}</details>
      <VirtualList label="任务变化" items={view.items} itemKey={item => item.relative_path}
        render={item => <button className="file-row" onClick={() => open(item.relative_path)}>{item.change} · {item.previous_path ? item.previous_path + ' → ' : ''}{item.relative_path} <small>{item.classification}</small></button>}/>
      {!view.items.length && <p className="muted">尚未观察到本轮文件变化。</p>}
      {view.next_cursor && <button className="secondary" onClick={() => refresh(view.next_cursor!)}>加载后续变化</button>}</>}
    {file && <><h4>{file.relative_path}</h4><p>当前 hash：<code>{file.current_sha256 ?? '文件不存在'}</code></p>
      {file.before === null ? <p>二进制、大文件或不完整基线。请使用项目文件的按需读取查看内容。</p> :
        <><div className="diff-columns"><pre>{file.before}</pre><pre>{file.current}</pre></div><details><summary>反向 patch 预览</summary>
          <p>仅供审查与手动使用；应用前须重新核对 hash 和获得工作区写入锁。</p><pre data-testid="reverse-patch">{file.reverse_patch}</pre></details></>}
    </>}
  </section>;
}
