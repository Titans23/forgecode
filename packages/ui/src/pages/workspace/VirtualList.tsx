import React, { useState } from 'react';
import { visibleRange } from '../../state/messages';

export function VirtualList<T>({ items, itemKey, render, label }: {
  items: T[]; itemKey(value: T): string; render(value: T): React.ReactNode; label: string;
}) {
  const [top, setTop] = useState(0);
  const range = visibleRange(items.length, top, 264);
  return <div className="virtual-list" aria-label={label} onScroll={event => setTop(event.currentTarget.scrollTop)}>
    <div style={{ height: range.start * 44 }}/>
    {items.slice(range.start, range.end).map(value => <div className="virtual-row" data-virtual-row key={itemKey(value)}>{render(value)}</div>)}
    <div style={{ height: (items.length - range.end) * 44 }}/>
  </div>;
}
