import { isAbsolute } from 'node:path';
import type { LaunchPayload } from './launch-payload.js';
export function fixedDispatcherCommand(node: string, dispatcher: string, windows = process.platform === 'win32', payload?: LaunchPayload): string {
  if (![node, dispatcher].every(isAbsolute)) throw new ContractError('Dispatcher resources must be absolute');
  const quote = (value: string) => windows ? "'" + value.replaceAll("'", "''") + "'" : "'" + value.replaceAll("'", "'\"'\"'") + "'";
  if (payload && (!windows || !isAbsolute(payload.path) || !/^[0-9a-f]{64}$/.test(payload.sha256))) throw new ContractError('Invalid launch reference');
  const input = payload ? ` --payload-file ${quote(payload.path)} --payload-sha256 ${quote(payload.sha256)}` : '';
  return `${windows ? '& ' : 'exec '}${quote(node)} ${quote(dispatcher)}${input}${windows ? '; exit $LASTEXITCODE' : ''}`;
}

/** Legacy strict RPC compatibility only; no execution backend is shipped. */
import { realpath, stat } from 'node:fs/promises';
import { ContractError, canonicalHash, validate } from '@forgecode/contracts';
import { windowsSupported } from './windows-adapter.js';

export class SrtAdapter {
  private workspace?: { id: string; path: string; identity: string };
  private closed = false;
  private cleanup?: any;
  constructor(readonly root: string, readonly controlRoot: string, readonly owner: any,
    readonly assets: Record<string, string>, readonly emit: (value: Record<string, unknown>) => boolean) {}
  async probe(params: any): Promise<any> {
    if (this.closed) this.unavailable();
    const path = await realpath(params.workspace_path), info = await stat(path);
    if (!info.isDirectory()) throw new ContractError('Workspace must be a directory');
    const identity = String(info.dev) + ':' + String(info.ino);
    if (this.workspace && (this.workspace.id !== params.workspace_id || this.workspace.path !== path || this.workspace.identity !== identity))
      throw new ContractError('Workspace binding changed', 'POLICY_DENIED', -32010);
    this.workspace = { id: params.workspace_id, path, identity };
    const features = ['read_isolation', 'write_isolation', 'direct_network_isolation', 'dns_isolation', 'socket_isolation', 'process_cleanup', 'memory', 'disk', 'pids'];
    const report = { platform: windowsSupported() ? 'windows-native' : process.platform === 'linux' ? 'linux-native' : 'unsupported',
      backend: 'strict-unavailable', backend_version: 'unavailable', read_isolation: 'unavailable',
      write_isolation: false, direct_network_isolation: false, dns_isolation: false, socket_isolation: false,
      process_cleanup: false, resource_enforcement: { memory: 'unavailable', disk: 'unavailable', pids: 'unavailable' },
      readiness: 'unavailable', issues: ['Strict execution is unavailable; explicitly select workspace-write for new sessions.'],
      measured_at_utc: new Date().toISOString(), verification: Object.fromEntries(features.map(name => [name, { status: 'unsupported', evidence_refs: [] }])) };
    validate('capability-report', report);
    return report;
  }
  private unavailable(): never { throw new ContractError('Strict execution is unavailable; no task was started', 'SANDBOX_UNAVAILABLE', -32010); }
  async prepare(params: any): Promise<never> {
    if (canonicalHash(params.owner) !== canonicalHash(this.owner) || canonicalHash(params.policy) !== params.policy_hash || params.policy.workspace_id !== this.workspace?.id)
      throw new ContractError('Owner, policy or workspace binding mismatch', 'POLICY_DENIED', -32010);
    this.unavailable();
  }
  async execute(_params: any): Promise<never> { this.unavailable(); }
  async status(_params: any): Promise<never> { this.unavailable(); }
  async cancel(_params: any): Promise<never> { this.unavailable(); }
  async close(params: any): Promise<any> {
    if (params.sandbox_session_id !== this.owner.sandbox_session_id) throw new ContractError('Session is not owned', 'POLICY_DENIED', -32010);
    this.closed = true;
    // No execution resources can be allocated here; old installations are not being inspected.
    this.cleanup ??= { owner: this.owner, state: 'clean', remaining_processes: 0,
      diagnostic_refs: [], completed_at_utc: new Date().toISOString() };
    validate('cleanup-report', this.cleanup);
    return structuredClone(this.cleanup);
  }
}
