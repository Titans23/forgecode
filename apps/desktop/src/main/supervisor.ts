/** One Engine, independent of renderer lifetime. Protocol corruption never triggers automatic replay. */
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { METHODS, MAX_FRAME_BYTES, canonicalHash, strictLoads, validate, validateEvent } from '@forgecode/contracts';
import type { EngineLaunch } from './assets.js';

type Pending = { method: keyof typeof METHODS; resolve: (value: any) => void; reject: (error: Error) => void; timer: NodeJS.Timeout };
class UnconfirmedResponse extends Error {}

async function within<T>(operation: Promise<T>, milliseconds: number): Promise<T> {
  let timer: NodeJS.Timeout;
  try { return await Promise.race([operation, new Promise<T>((_, reject) => { timer = setTimeout(() => reject(new Error('Lifecycle deadline exceeded')), milliseconds); })]); }
  finally { clearTimeout(timer!); }
}

export class EngineSupervisor {
  state: 'idle' | 'starting' | 'ready' | 'incompatible' | 'engine_lost' | 'closing' | 'closed' = 'idle';
  readonly transport = 'private-stdio';
  hello: any = null;
  child: ChildProcessWithoutNullStreams | null = null;
  private pending = new Map<string, Pending>();
  private expired = new Map<string, keyof typeof METHODS>();
  private unconfirmed = new Map<string, { method: keyof typeof METHODS; params: any }>();
  private unconfirmedOverflow = false;
  private input = Buffer.alloc(0);
  private sequence = 0;
  private eventQueue: any[] = [];
  private gap = false;
  private rendererCount = 0;
  private closing: Promise<{ state: 'confirmed' | 'unknown'; cleanup_state: 'complete' | 'unknown'; reason?: string }> | null = null;
  diagnosticBytes = 0;
  lateResponses = 0;

  constructor(private launch: EngineLaunch, private options: { responseTimeoutMs?: number } = {}) {
    if (!launch.executable || !launch.cwd || !/^[0-9a-f]{64}$/.test(launch.manifestHash)) throw new Error('Supervisor requires verified Main assets');
    if (options.responseTimeoutMs !== undefined && (!Number.isInteger(options.responseTimeoutMs) || options.responseTimeoutMs < 10 || options.responseTimeoutMs > 15000)) throw new Error('Response deadline must be bounded');
  }
  get pid() { return this.child?.pid ?? null; }
  attachRenderer() { this.rendererCount++; }
  detachRenderer() { this.rendererCount = Math.max(0, this.rendererCount - 1); }
  events() { const result = { events: this.eventQueue.splice(0), gap: this.gap }; this.gap = false; return result; }
  markEventGap() { this.gap = true; }
  async refreshHealth() {
    const health = await this.call('system.health', {});
    if (health.engine_epoch !== this.hello.engine_epoch) throw new Error('Engine epoch changed without an owned restart');
    this.hello.readiness = health.readiness;
    return health;
  }

  async start() {
    if (this.state !== 'idle') throw new Error('Engine is already owned; renderer reload cannot restart it');
    this.state = 'starting';
    this.child = spawn(this.launch.executable, [...this.launch.arguments,'--main-owner-pid',String(process.pid)], { cwd: this.launch.cwd,
      env: { ...this.launch.environment }, shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    this.child.stdout.on('data', (bytes: Buffer) => this.receive(bytes));
    this.child.stderr.on('data', (bytes: Buffer) => { this.diagnosticBytes += bytes.length; });
    this.child.once('error', () => this.lost('Engine could not start'));
    this.child.once('close', () => {
      if (this.input.length) this.lost('Engine exited with an incomplete protocol frame');
      else if (this.state !== 'closing' && this.state !== 'closed') this.lost('Engine exited; reconcile previous work');
    });
    this.child.stdin.on('error', () => this.lost('Engine control pipe closed'));
    try {
      const hello = await this.request('system.initialize', { protocol: { major: 1, minor: 0 }, client_build: 'forgecode-desktop-v4',
        expected_manifest_hash: this.launch.manifestHash, profile: this.launch.profile });
      if (hello.protocol.major !== 1 || hello.manifest_hash !== this.launch.manifestHash) throw new Error('Protocol/manifest mismatch');
      this.hello = hello;
      this.state = 'ready';
      return hello;
    } catch (error) {
      this.state = 'incompatible';
      if (this.child.exitCode === null) this.child.stdin.end();
      throw error;
    }
  }

  private lost(reason: string) {
    if (this.state !== 'closing' && this.state !== 'closed' && this.state !== 'incompatible') this.state = 'engine_lost';
    for (const request of this.pending.values()) { clearTimeout(request.timer); request.reject(new Error(reason)); }
    this.pending.clear();
    this.expired.clear();
  }

  private receive(bytes: Buffer) {
    try {
      this.input = Buffer.concat([this.input, bytes]);
      while (true) {
        const boundary = this.input.indexOf(10);
        if (boundary < 0) { if (this.input.length > MAX_FRAME_BYTES) throw new Error('Oversized Engine frame'); return; }
        if (boundary > MAX_FRAME_BYTES) throw new Error('Oversized Engine frame');
        const raw = this.input.subarray(0, boundary); this.input = this.input.subarray(boundary + 1);
        const value = strictLoads(raw) as any;
        if (value.method === 'events.batch') {
          validate('event-notification', value);
          for (const event of value.params.events) {
            validateEvent(event);
            if (this.eventQueue.length >= 128) { this.eventQueue.shift(); this.gap = true; }
            this.eventQueue.push(event);
          }
          // Main continuously consumes and retains bounded metadata even without a renderer.
          void this.request('events.ack', { subscription_id: value.params.subscription_id, cursor: value.params.cursor }).catch(() => {});
          continue;
        }
        validate('rpc-response', value);
        const request = this.pending.get(value.id);
        if (!request) {
          const method = this.expired.get(value.id);
          if (!method) throw new Error('Response has no owned request');
          if (!value.error) validate(METHODS[method].result_schema, value.result);
          this.expired.delete(value.id); this.lateResponses++;
          continue; // A validated late response never cancels or repeats accepted work.
        }
        if (!value.error) validate(METHODS[request.method].result_schema, value.result);
        this.pending.delete(value.id); clearTimeout(request.timer);
        if (value.error) request.reject(new Error(`Engine RPC failed (${value.error.code}, ${value.error.data?.kind ?? 'protocol'})`));
        else request.resolve(value.result);
      }
    } catch {
      this.lost('Invalid Engine protocol; pending actions require reconciliation');
      this.child?.stdin.end();
    }
  }

  private request(method: keyof typeof METHODS, params: any): Promise<any> {
    if (!this.child || this.child.exitCode !== null || this.child.stdin.destroyed || this.pending.size >= 32) return Promise.reject(new Error('Engine channel is unavailable or saturated'));
    validate(METHODS[method].request_schema, params);
    const id = String(++this.sequence);
    const raw = Buffer.from(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
    if (raw.length > MAX_FRAME_BYTES || this.child.stdin.writableLength + raw.length > MAX_FRAME_BYTES * 2) return Promise.reject(new Error('Control frame/queue limit exceeded'));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id); this.expired.set(id, method);
        if (this.expired.size > 256) this.expired.delete(this.expired.keys().next().value!);
        reject(new UnconfirmedResponse('Engine action response unconfirmed; query its durable action ID before retry'));
      }, method === 'system.initialize' ? 15000 : this.options.responseTimeoutMs ?? 15000);
      this.pending.set(id, { method, resolve, reject, timer });
      this.child!.stdin.write(raw, error => { if (error) this.lost('Engine control write failed'); });
    });
  }

  private async durableResult(method: keyof typeof METHODS, params: any) {
    const action = await this.request('action.get', { method, client_action_id: params.client_action_id });
    if (action.payload_hash !== canonicalHash(params) || action.result_schema !== METHODS[method].result_schema) throw new Error('Durable action binding changed');
    validate(METHODS[method].result_schema, action.result);
    return action.result;
  }

  async call(method: keyof typeof METHODS, params: any): Promise<any> {
    if (this.state !== 'ready') return Promise.reject(new Error('Engine handshake is unavailable'));
    if (method === 'system.initialize' || method === 'system.shutdown') return Promise.reject(new Error('Lifecycle methods belong to Main supervisor'));
    if (METHODS[method].mutation && ['session.start_turn', 'session.submit', 'run.start'].includes(method) && this.hello.readiness.status === 'blocked') return Promise.reject(new Error('Execution readiness is blocked'));
    if (!this.hello.capabilities.supported_methods.includes(method)) return Promise.reject(new Error('Engine method is not implemented'));
    validate(METHODS[method].request_schema, params);
    if (METHODS[method].mutation && !['session.cancel_turn','evaluation.cancel','credentials.clear','connection.delete'].includes(method)) {
      if (this.unconfirmedOverflow) throw new UnconfirmedResponse('Unconfirmed action limit exceeded; reconcile before restarting Main');
      for (const [key, previous] of this.unconfirmed) {
        let result;
        try { result = await this.durableResult(previous.method, previous.params); }
        catch { throw new UnconfirmedResponse(`Action ${previous.params.client_action_id} remains unconfirmed; no new mutation was sent`); }
        this.unconfirmed.delete(key);
        const content = (value: any) => canonicalHash(Object.fromEntries(Object.entries(value).filter(([name]) => name !== 'client_action_id')));
        if (previous.method === method && content(previous.params) === content(params)) return result;
      }
    }
    return this.request(method, params).catch(async error => {
      if (!(error instanceof UnconfirmedResponse) || !METHODS[method].mutation || METHODS[method].timeout_query !== 'action.get' || typeof params.client_action_id !== 'string') throw error;
      const key = method + ':' + params.client_action_id;
      if (this.unconfirmed.has(key) || this.unconfirmed.size < 32) this.unconfirmed.set(key, { method, params: strictLoads(JSON.stringify(params)) });
      else this.unconfirmedOverflow = true;
      try {
        const result = await this.durableResult(method, params);
        this.unconfirmed.delete(key);
        return result;
      } catch {
        throw new UnconfirmedResponse(`Action ${params.client_action_id} remains unconfirmed; retain this ID and reconcile before submitting again`);
      }
    });
  }

  shutdown(mode: 'cancel' | 'drain'): Promise<{ state: 'confirmed' | 'unknown'; cleanup_state: 'complete' | 'unknown'; reason?: string }> {
    if (this.closing) return this.closing;
    this.closing = (async () => {
      const child = this.child;
      if (!child) { this.state = 'closed'; return { state: 'confirmed' as const, cleanup_state: 'complete' as const }; }
      const prior = this.state;
      this.state = 'closing';
      let terminalObserved = false;
      let cleanupState: 'complete' | 'unknown' = 'unknown';
      try {
        if (prior === 'ready') {
          if (this.hello.capabilities.supported_methods.includes('sandbox.cleanup_status')) {
            const expires = Date.now() + 8000;
            const observe = async () => {
              const sessions: string[] = [];
              const workspaces: string[] = [];
              let workspaceCursor: string | undefined;
              do {
                if (Date.now() >= expires || workspaces.length >= 3200) throw new Error('Cleanup workspace limit');
                const page = await this.request('workspace.list', { limit: 100, ...(workspaceCursor ? { cursor: workspaceCursor } : {}) });
                workspaces.push(...page.items.map((item: any) => item.workspace_id));
                workspaceCursor = page.next_cursor ?? undefined;
              } while (workspaceCursor);
              for (const workspace_id of workspaces) {
                let cursor: string | undefined;
                do {
                  if (Date.now() >= expires || sessions.length >= 3200) throw new Error('Cleanup observation limit');
                  const page = await this.request('session.list', { workspace_id, limit: 100, ...(cursor ? { cursor } : {}) });
                  for (const session of page.items) {
                    if (session.active_turn_id && mode === 'cancel') await this.request('session.cancel_turn', {
                      turn_id: session.active_turn_id, client_action_id: 'act-' + randomUUID(), reason: 'Desktop shutdown' });
                    sessions.push(session.session_id);
                  }
                  cursor = page.next_cursor ?? undefined;
                } while (cursor);
              }
              for (const session_id of sessions) {
                let report;
                do {
                  if (Date.now() >= expires) throw new Error('Cleanup observation expired');
                  report = await this.request('sandbox.cleanup_status', { session_id });
                  if (report.state === 'pending') await new Promise(r => setTimeout(r, 20));
                } while (report.state === 'pending');
                if (report.state !== 'complete') return 'unknown' as const;
              }
              return 'complete' as const;
            };
            try { cleanupState = await within(observe(), 8000); } catch { cleanupState = 'unknown'; }
          }
          await within(this.request('system.shutdown', { client_action_id: 'act-' + randomUUID(), mode }), 5000);
        }
        child.stdin.end();
        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('Engine cleanup exit unconfirmed')), 10000);
          const finish = () => { clearTimeout(timer); terminalObserved = child.exitCode === 0; resolve(); };
          if (child.exitCode !== null || child.signalCode !== null) finish(); else child.once('close', finish);
        });
        this.state = 'closed';
        this.lost('Engine closed');
        return { state: terminalObserved ? 'confirmed' as const : 'unknown' as const, cleanup_state: cleanupState,
          ...(!terminalObserved ? { reason: 'Engine exited without a confirmed shutdown' } : {}) };
      } catch {
        child.stdin.end();
        if (child.exitCode === null) child.kill(); // Exact owned Engine; OS sandbox descendants still require reconciliation.
        this.state = 'closed'; this.lost('Engine shutdown unconfirmed');
        return { state: 'unknown' as const, cleanup_state: 'unknown' as const, reason: 'Shutdown deadline exceeded; cleanup requires reconciliation' };
      }
    })();
    return this.closing;
  }
}
