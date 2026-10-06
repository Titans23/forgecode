import type { SessionSnapshotResult } from '@forgecode/contracts';
export type Message = SessionSnapshotResult['messages'][number];

/** IDs bind to a turn and sequence, never to text equality. */
export function mergeMessages(previous: Message[], incoming: Message[]): Message[] {
  return Array.from(new Map([...previous, ...incoming].map(value => [value.sequence, value])).values())
    .sort((a, b) => a.sequence - b.sequence).slice(-10000);
}
export function visibleRange(count: number, top: number, height: number, rowHeight = 44) {
  const start = Math.min(Math.max(0, count-1), Math.max(0, Math.floor(top / rowHeight) - 4));
  return { start, end: Math.min(count, start + Math.ceil(height / rowHeight) + 8) };
}
export function submitsOnEnter(event: { key: string; shiftKey: boolean; isComposing?: boolean; keyCode?: number }, composing: boolean) {
  return event.key === 'Enter' && !event.shiftKey && !composing && !event.isComposing && event.keyCode !== 229;
}
