import React from 'react';
import { VirtualList } from '../../pages/workspace/VirtualList';
import { milliseconds, text, traceRows, type Span } from './state';

export function Trace({ items, selected, select }: { items: Span[]; selected: string | null; select(span: Span): void }) {
  return <VirtualList label="Trace 树与瀑布" items={traceRows(items)} itemKey={row => row.span.trace_id + ':' + row.span.span_id}
    render={row => <button className={'trace-row file-row ' + (selected === row.span.span_id ? 'selected' : '')} data-span-id={row.span.span_id}
      onClick={() => select(row.span)}><span style={{ paddingLeft: Math.min(row.depth, 10) * 12 }}>{row.depth ? '↳ ' : ''}{row.span.name} · {row.span.state}
      {row.parentMissing ? ' · 父节点未加载' : row.cycle ? ' · 父链不完整' : ''}</span>
      <span className="waterfall"><i style={{ marginLeft: row.left + '%', width: Math.max(row.width, 0.5) + '%' }}/></span>
      <span>{milliseconds(row.span.metadata?.duration_nanoseconds)} · {text(row.span.metadata?.execution_id).replace('未知','')}</span></button>}/>;
}
