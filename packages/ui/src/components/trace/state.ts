import type { ObservabilitySpansResult } from '@forgecode/contracts';
export type Span = ObservabilitySpansResult['items'][number];
export type TraceRow = { span: Span; depth: number; parentMissing: boolean; cycle: boolean; left: number; width: number };
export const TRACE_LIMIT = 10000;
export const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
export const text = (value: unknown): string => typeof value === 'string' ? value : typeof value === 'number' || typeof value === 'boolean' ? String(value) : '未知';
export function nanos(value: unknown): bigint | null { return typeof value === 'string' && /^[0-9]{1,32}$/.test(value) ? BigInt(value) : null; }
export function milliseconds(value: unknown): string { const ns = nanos(value); return ns === null ? '未知' : (Number(ns / 1000n) / 1000).toFixed(2) + ' ms'; }
export function mergeSpans(previous: Span[], incoming: Span[]): { items: Span[]; dropped: boolean } {
  const items = Array.from(new Map([...previous, ...incoming].map(span => [span.trace_id + ':' + span.span_id, span])).values());
  return { items: items.slice(-TRACE_LIMIT), dropped: items.length > TRACE_LIMIT };
}
export function traceRows(items: Span[]): TraceRow[] {
  const keyed = new Map(items.map(span => [span.trace_id + ':' + span.span_id, span]));
  const bounds = new Map<string, { start: bigint; end: bigint }>();
  for (const span of items) {
    const start = nanos(span.metadata?.start_monotonic_ns), end = nanos(span.metadata?.end_monotonic_ns) ?? start;
    if (start === null || end === null) continue;
    const old = bounds.get(span.trace_id);
    bounds.set(span.trace_id, { start: old && old.start < start ? old.start : start, end: old && old.end > end ? old.end : end });
  }
  return items.map(span => {
    let parent = span.parent_span_id, depth = 0, parentMissing = false, cycle = false;
    const seen = new Set([span.span_id]);
    while (parent && depth < 64) {
      if (seen.has(parent)) { cycle = true; break; }
      seen.add(parent);
      const item = keyed.get(span.trace_id + ':' + parent);
      if (!item) { parentMissing = true; break; }
      depth++; parent = item.parent_span_id;
    }
    if (parent && depth === 64) cycle = true;
    const start = nanos(span.metadata?.start_monotonic_ns), end = nanos(span.metadata?.end_monotonic_ns), bound = bounds.get(span.trace_id);
    let left = 0, width = 0;
    if (bound && start !== null && end !== null && end >= start && bound.end > bound.start) {
      const total = bound.end - bound.start;
      left = Number((start - bound.start) * 10000n / total) / 100;
      width = Number((end - start) * 10000n / total) / 100;
    }
    return { span, depth, parentMissing, cycle, left, width };
  }).sort((a, b) => {
    if (a.span.trace_id !== b.span.trace_id) return a.span.trace_id.localeCompare(b.span.trace_id);
    const x = nanos(a.span.metadata?.start_monotonic_ns), y = nanos(b.span.metadata?.start_monotonic_ns);
    return x !== null && y !== null && x !== y ? x < y ? -1 : 1 : a.depth - b.depth;
  });
}
/** Display only catalog facts. Full attributes, tool arguments and model payloads stay private. */
export function safeFacts(value: unknown): Record<string, unknown> {
  const source = object(value), result: Record<string, unknown> = {};
  for (const key of ['tool_name','result','exit_code','role','requested_model','returned_model','usage_quality','context_version','reason',
    'workspace_revision','environment_epoch','evidence_id','repairs_remaining','cleanup_state','dimension','amount_decimal','remaining_decimal']) {
    const item = source[key]; if (item === null || ['string','number','boolean'].includes(typeof item)) result[key] = item;
  }
  return result;
}
