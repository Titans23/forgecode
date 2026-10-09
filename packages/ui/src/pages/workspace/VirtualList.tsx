import React, { useState } from 'react';
import { visibleRange } from '../../state/messages';
import { Icon } from '../../components/Icon';

export function VirtualList<T>({ items, itemKey, render, label, emptyText = '还没有记录' }: {
  items: T[]; itemKey(value: T): string; render(value: T): React.ReactNode; label: string; emptyText?: string;
}) {
  const [top, setTop] = useState(0);
  const height = Math.min(264, Math.max(44, items.length * 44));
  const range = visibleRange(items.length, Math.min(top, Math.max(0, items.length * 44 - height)), height);
  return <div className="virtual-list" aria-label={label} style={{ height: items.length ? height + 2 : 88 }} onScroll={event => setTop(event.currentTarget.scrollTop)}>
    {!items.length && <div className="list-empty"><Icon name="inbox" size={20}/><span>{emptyText}</span></div>}
    <div style={{ height: range.start * 44 }}/>
    {items.slice(range.start, range.end).map(value => <div className="virtual-row" data-virtual-row key={itemKey(value)}>{render(value)}</div>)}
    <div style={{ height: (items.length - range.end) * 44 }}/>
  </div>;
}
