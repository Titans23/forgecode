import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Ajv2020 } from 'ajv/dist/2020.js';
import formatsModule from 'ajv-formats';
import type { FormatsPlugin } from 'ajv-formats';
import bundle from './generated/schemas.json' with { type: 'json' };
export * from './generated/types.js';
import { ERROR_KINDS } from './generated/types.js';

export const MAX_FRAME_BYTES = 1048576;
export const MAX_DEPTH = 32;
export const METHODS = bundle.methods;
export const BRIDGE_METHODS = bundle.bridge_methods;
export const EVENTS = bundle.events;
export class ContractError extends Error {
  constructor(message: string, public kind = 'INVALID_PARAMS', public code = -32602) { super(message); }
}

function checkString(value: string): void {
  for (let i = 0; i < value.length; i++) {
    const code = value.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(++i);
      if (!(next >= 0xdc00 && next <= 0xdfff)) throw new ContractError('Unpaired Unicode surrogate');
    } else if (code >= 0xdc00 && code <= 0xdfff) throw new ContractError('Unpaired Unicode surrogate');
  }
}

function checkTree(value: unknown, depth = 0): void {
  if (value === null || typeof value === 'boolean') return;
  if (typeof value === 'string') { checkString(value); return; }
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) throw new ContractError('Non-interoperable JSON number');
    return;
  }
  if (typeof value !== 'object') throw new ContractError('Unsupported JSON value');
  if (depth >= MAX_DEPTH) throw new ContractError('JSON nesting exceeds 32 containers');
  if (Array.isArray(value)) { for (const child of value) checkTree(child, depth + 1); }
  else {
    if (![Object.prototype, null].includes(Object.getPrototypeOf(value))) throw new ContractError('Expected a plain JSON object');
    for (const [key, child] of Object.entries(value)) { checkString(key); checkTree(child, depth + 1); }
  }
}

export function strictLoads(raw: string | Uint8Array): unknown {
  if (typeof raw === 'string') checkString(raw);
  const bytes = typeof raw === 'string' ? Buffer.from(raw, 'utf8') : raw;
  if (bytes.byteLength > MAX_FRAME_BYTES) throw new ContractError('JSON frame exceeds 1 MiB');
  let text: string;
  try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes); }
  catch { throw new ContractError('Invalid UTF-8 JSON', 'INVALID_PARAMS', -32700); }
  let position = 0;
  const fail = (): never => { throw new ContractError('Invalid JSON syntax', 'INVALID_PARAMS', -32700); };
  const whitespace = () => { while (/[ \t\r\n]/.test(text[position] ?? '\0')) position++; };
  function quoted(): string {
    const start = position++;
    while (position < text.length) {
      const char = text[position++];
      if (char === '\\') { position++; continue; }
      if (char === '"') {
        let value: string;
        try { value = JSON.parse(text.slice(start, position)); } catch { return fail(); }
        checkString(value); return value;
      }
    }
    return fail();
  }
  function parse(depth: number): unknown {
    whitespace();
    const char = text[position];
    if (char === '"') return quoted();
    if (char === '{' || char === '[') {
      if (depth >= MAX_DEPTH) throw new ContractError('JSON nesting exceeds 32 containers');
      position++; whitespace();
      const close = char === '{' ? '}' : ']';
      const output: unknown[] | Record<string, unknown> = char === '{' ? Object.create(null) : [];
      const keys = new Set<string>();
      if (text[position] === close) { position++; return output; }
      while (true) {
        if (char === '{') {
          if (text[position] !== '"') return fail();
          const key = quoted();
          if (keys.has(key)) throw new ContractError('Duplicate JSON key', 'INVALID_PARAMS', -32700);
          keys.add(key); whitespace();
          if (text[position++] !== ':') return fail();
          (output as Record<string, unknown>)[key] = parse(depth + 1);
        } else (output as unknown[]).push(parse(depth + 1));
        whitespace();
        const separator = text[position++];
        if (separator === close) return output;
        if (separator !== ',') return fail();
        whitespace();
      }
    }
    for (const [literal, value] of [['true', true], ['false', false], ['null', null]] as const) {
      if (text.startsWith(literal, position)) { position += literal.length; return value; }
    }
    const match = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(text.slice(position));
    if (!match) return fail();
    position += match[0].length;
    const value = Number(match[0]); checkTree(value); return value;
  }
  const value = parse(0); whitespace();
  if (position !== text.length) return fail();
  return value;
}

// Sort by Unicode scalar value, matching Python rather than JavaScript's UTF-16 ordering.
function compareKeys(left: string, right: string): number {
  const a = Array.from(left, char => char.codePointAt(0)!);
  const b = Array.from(right, char => char.codePointAt(0)!);
  for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
}
export function canonicalHash(value: unknown): string {
  checkTree(value);
  function encode(item: unknown): string {
    if (typeof item === 'number' && !Number.isInteger(item)) throw new ContractError('Canonical configuration decimals must use normalized strings');
    if (Array.isArray(item)) return '[' + item.map(encode).join(',') + ']';
    if (item !== null && typeof item === 'object') {
      const object = item as Record<string, unknown>;
      return '{' + Object.keys(object).sort(compareKeys).map(key => JSON.stringify(key) + ':' + encode(object[key])).join(',') + '}';
    }
    return JSON.stringify(item);
  }
  return createHash('sha256').update(encode(value), 'utf8').digest('hex');
}

const ajv = new Ajv2020({ strict: false, allErrors: false, validateFormats: true, ownProperties: true });
(formatsModule as unknown as FormatsPlugin)(ajv);
const schemas: Record<string, { $id: string }> = bundle.schemas;
for (const schema of Object.values(schemas)) ajv.addSchema(schema);

export function validate(name: string, value: unknown): unknown {
  checkTree(value);
  const key = name.replace(/^schemas\//, '').replace(/\.schema\.json$/, '');
  if (!Object.hasOwn(schemas, key)) throw new ContractError('Unknown contract schema');
  const validator = ajv.getSchema(schemas[key].$id)!;
  if (!validator(value)) {
    const error = validator.errors?.[0];
    throw new ContractError(`Contract ${key}: ${error?.keyword} failed at ${error?.instancePath}`);
  }
  const object = value as Record<string, any>;
  if (key === 'run-spec') {
    if (Object.keys(object.dataset.task_revisions).sort(compareKeys).join('\0') !== [...object.dataset.task_ids].sort(compareKeys).join('\0')) throw new ContractError('Task revisions must match selected tasks');
    if (object.budget.trial_wall_seconds < object.budget.attempt_wall_seconds) throw new ContractError('Trial budget smaller than attempt budget');
  }
  if (key === 'bundle-manifest') {
    const paths = object.contents.map((entry: any) => entry.path.toLowerCase());
    if (new Set(paths).size !== paths.length) throw new ContractError('Bundle content paths conflict');
    if (object.contents.reduce((sum: number, entry: any) => sum + entry.size_bytes, 0) !== object.total_size_bytes) throw new ContractError('Bundle total size differs from inventory');
  }
  if (key === 'evaluation.validate.request' || key === 'evaluation.create_run.request') validate('run-spec', object.spec);
  if (key === 'bundle.export.result') validate('bundle-manifest', object.manifest);
  if (key === 'event-notification' && Buffer.byteLength(JSON.stringify(value), 'utf8') > 65536) throw new ContractError('Event batch exceeds 64 KiB');
  return value;
}

export function validateRequest(request: unknown, principal: 'main' | 'renderer'): unknown {
  validate('rpc-request', request);
  const object = request as { method: keyof typeof METHODS; params: unknown; id?: string };
  const method = Object.hasOwn(METHODS, object.method) ? METHODS[object.method] : undefined;
  if (!method) throw new ContractError('Unknown RPC method', 'NOT_FOUND', -32601);
  if (!['main', 'renderer'].includes(principal) || (method.audience === 'main' && principal !== 'main')) throw new ContractError('Method requires trusted Main channel', 'UNAUTHORIZED', -32010);
  if (method.mutation && !object.id) throw new ContractError('Mutation requires request ID');
  validate(method.request_schema, object.params);
  return request;
}

export function validateEvent(value: unknown): unknown {
  validate('event-envelope', value);
  const object = value as { event_type: keyof typeof EVENTS; attributes: unknown };
  const event = Object.hasOwn(EVENTS, object.event_type) ? EVENTS[object.event_type] : undefined;
  if (!event) throw new ContractError('Unknown event type');
  validate(event.payload_schema, object.attributes);
  return value;
}

// Test-only CLI invokes the same exported decoder and validators used by clients.
if (typeof import.meta.url === 'string' && process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1] && process.argv[2] === '--verify-fixtures') {
  const input = JSON.parse(readFileSync(0, 'utf8'));
  const valid = input.cases.map((item: { raw: string; schema: string | null }) => {
    try { const value = strictLoads(item.raw); if (item.schema) validate(item.schema, value); return true; }
    catch (error) { if (error instanceof ContractError) return false; throw error; }
  });
  const requests = (input.requests ?? []).map((item: { request: unknown; principal: 'main' | 'renderer' }) => {
    try { validateRequest(item.request, item.principal); return 'valid'; }
    catch (error) { if (error instanceof ContractError) return error.kind; throw error; }
  });
  process.stdout.write(JSON.stringify({ valid, requests, hash: canonicalHash(input.hash_value), error_kinds: ERROR_KINDS }));
}
