/** The sole adapter to the audited, locked SRT 0.0.78 API. No host execution fallback. */
import { spawn } from 'node:child_process';
import { readFile, realpath, stat } from 'node:fs/promises';
import os from 'node:os';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { SandboxManager, SandboxRuntimeConfigSchema } from '@anthropic-ai/sandbox-runtime';
import { checkLinuxDependencies } from '@anthropic-ai/sandbox-runtime/dist/sandbox/linux-sandbox-utils.js';
import { ContractError, canonicalHash, validate } from '@forgecode/contracts';
import { Executions } from './executions.js';
import { LinuxExecutionOwner } from './linux-ownership.js';
import { protectedDirectoryPaths, windowsPrerequisites } from './windows-adapter.js';

const FEATURES = ['read_isolation', 'write_isolation', 'direct_network_isolation', 'dns_isolation',
  'socket_isolation', 'process_cleanup', 'memory', 'disk', 'pids'];
const unsafeEnvironment = /^(NODE_|PYTHON|LD_|DYLD_|ELECTRON_)|(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)/i;
const privateAddresses = ['0.0.0.0/8', '10.0.0.0/8', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12',
  '192.168.0.0/16', '100.64.0.0/10', '::/128', '::1/128', 'fc00::/7', 'fe80::/10'];
const inside = (root: string, path: string) => { const part = relative(root, path); return !isAbsolute(part) && part !== '..' && !part.startsWith(`..${sep}`); };

export function fixedDispatcherCommand(node: string, dispatcher: string, windows = process.platform === 'win32'): string {
  if (![node, dispatcher].every(isAbsolute)) throw new ContractError('Dispatcher resources must be absolute');
  const quote = (value: string) => windows ? "'" + value.replaceAll("'", "''") + "'" : "'" + value.replaceAll("'", "'\"'\"'") + "'";
  return `${windows ? '& ' : 'exec '}${quote(node)} ${quote(dispatcher)}`;
}

export function requireCapabilities(report: any, policy: any): void {
  if (report.readiness !== 'ready') throw new ContractError('Native SRT prerequisites are unavailable',
    report.readiness === 'setup_required' ? 'SETUP_REQUIRED' : 'SANDBOX_UNAVAILABLE', -32010);
  if (policy.filesystem.read_mode === 'strict_allowlist_required' || policy.network.dns_isolation_required && process.platform === 'win32' ||
    ['memory_bytes', 'disk_bytes', 'pids'].some(key => policy.limits[key]?.enforcement === 'hard_required')) {
    throw new ContractError('Locked SRT cannot enforce the requested stronger policy', 'CAPABILITY_UNSATISFIED', -32010);
  }
  const required = FEATURES.filter(name => !['dns_isolation', 'memory', 'disk', 'pids'].includes(name));
  if (policy.network.dns_isolation_required) required.push('dns_isolation');
  for (const name of required) if (report.verification[name].status !== 'verified' || !report.verification[name].evidence_refs.length) {
    throw new ContractError(`${name} lacks native boundary verification`, 'CAPABILITY_UNSATISFIED', -32010);
  }
}

export class SrtAdapter {
  private workspace?: { id: string; path: string; identity: string };
  private capabilities?: any;
  private preparation?: any;
  private preparing?: Promise<any>;
  private preparingHash?: string;
  private closing?: Promise<any>;
  private initialized = false;
  private nativePolicy?: any;
  private launchAttempted = false;
  private sessionOutputBytes = 0;
  readonly executions: Executions;
  readonly shells: Record<string, string>;

  constructor(readonly root: string, readonly controlRoot: string, readonly owner: any,
    readonly assets: Record<string, string>, emit: (value: Record<string, unknown>) => boolean) {
    this.executions = new Executions(owner, value => {
      const size = Buffer.byteLength(value.raw_base64 as string, 'base64');
      if (this.sessionOutputBytes + size > (this.nativePolicy?.limits.session_artifact_bytes ?? 0)) return false;
      if (!emit(value)) return false;
      this.sessionOutputBytes += size;
      return true;
    });
    this.shells = process.platform === 'win32'
      ? { pwsh: resolve(process.env.ProgramFiles || 'C:/Program Files', 'PowerShell/7/pwsh.exe') }
      : { bash: '/bin/bash', sh: '/bin/sh' };
  }

  private nativeConfig(policy?: any): any {
    const fs = policy?.filesystem;
    const rawProtectedPaths = fs?.protected_paths ?? [this.controlRoot];
    const protectedPaths = process.platform === 'win32' ? protectedDirectoryPaths(rawProtectedPaths) : rawProtectedPaths;
    const patterns = this.workspace ? ['.env', '.env.*', 'credentials', 'credentials.json', 'id_rsa', 'id_ed25519']
      .map(name => resolve(this.workspace!.path, '**', name)) : [];
    return SandboxRuntimeConfigSchema.parse({
      filesystem: { denyRead: [...protectedPaths, ...patterns], allowRead: process.platform === 'win32'
        ? [dirname(process.execPath), resolve(this.root, 'sandbox_bridge/dist'), resolve(this.root, 'packages/contracts/dist'),
          resolve(this.root, 'node_modules'), resolve(this.root, 'package.json'), resolve(this.root, 'sandbox_bridge/package.json'),
          resolve(this.root, 'packages/contracts/package.json')] : [],
        allowWrite: fs?.write_roots ?? [], denyWrite: [...protectedPaths, ...patterns, this.root], allowGitConfig: false },
      network: { allowedDomains: policy?.network.allowed_domains ?? [], deniedDomains: [], strictAllowlist: true,
        deniedResolvedAddresses: privateAddresses, allowUnixSockets: [], allowAllUnixSockets: false, allowLocalBinding: false },
      ...(process.platform === 'win32' ? { windows: { srtWin: { path: this.assets['srt-win'] } } }
        : { bwrapPath: '/usr/bin/bwrap', socatPath: '/usr/bin/socat', ripgrep: { command: '/usr/bin/rg' },
          seccomp: { applyPath: this.assets['apply-seccomp'], argv0: 'apply-seccomp' }, javaAgentJarPath: this.assets['java-proxy-agent'] }) });
  }

  async probe(params: any): Promise<any> {
    if (this.closing) throw new ContractError('Bridge session is closed', 'SANDBOX_UNAVAILABLE', -32010);
    const path = await realpath(params.workspace_path);
    const info = await stat(path);
    if (!info.isDirectory()) throw new ContractError('Workspace must be a directory');
    if (this.workspace && (this.workspace.id !== params.workspace_id || this.workspace.path !== path)) {
      throw new ContractError('Bridge is bound to another workspace', 'POLICY_DENIED', -32010);
    }
    if (this.workspace && this.workspace.identity !== `${info.dev}:${info.ino}`) {
      throw new ContractError('Workspace identity changed', 'POLICY_DENIED', -32010);
    }
    this.workspace = { id: params.workspace_id, path, identity: `${info.dev}:${info.ino}` };
    const platform = process.platform === 'win32' && Number(os.release().split('.')[2]) >= 22000 ? 'windows-native'
      : process.platform === 'linux' && process.arch === 'x64' ? 'linux-native' : 'unsupported';
    const report: any = { platform, backend: 'srt', backend_version: '0.0.78', read_isolation: 'unavailable',
      write_isolation: false, direct_network_isolation: false, dns_isolation: false, socket_isolation: false,
      process_cleanup: false, resource_enforcement: { memory: 'unavailable', disk: 'unavailable', pids: 'unavailable' },
      readiness: 'unavailable', issues: [], measured_at_utc: new Date().toISOString(),
      verification: Object.fromEntries(FEATURES.map(name => [name, { status: 'unsupported', evidence_refs: [] }])) };
    try {
      if (platform === 'unsupported') report.issues.push('Supported native hosts require Windows 11 x64 or Ubuntu 22.04/24.04 x64');
      else if (platform === 'windows-native') {
        const prerequisite = await windowsPrerequisites(this.assets['srt-win']);
        // The upstream readiness is a prerequisite, never native isolation evidence.
        report.readiness = prerequisite.ready ? 'ready' : 'setup_required';
        if (report.readiness !== 'ready') report.issues.push('SRT account/WFP prerequisites require authorized setup or verification');
      } else {
        const release = await readFile('/etc/os-release', 'utf8');
        if (!/^ID=ubuntu$/m.test(release) || !/^VERSION_ID="(?:22\.04|24\.04)"$/m.test(release)) {
          report.issues.push('Unsupported Linux distribution');
        } else {
          for (const binary of ['/usr/bin/bwrap', '/usr/bin/socat', '/usr/bin/rg', '/bin/bash', '/bin/sh']) {
            const identity = await stat(await realpath(binary));
            if (!identity.isFile() || identity.uid !== 0 || identity.mode & 0o022) throw new Error('Untrusted system dependency');
          }
          const cfg = this.nativeConfig();
          const dependencies = checkLinuxDependencies({ bwrapPath: cfg.bwrapPath, socatPath: cfg.socatPath, seccompConfig: cfg.seccomp });
          report.issues.push(...dependencies.errors.map(() => 'A required fixed Linux SRT dependency is unavailable'));
          report.readiness = dependencies.errors.length ? 'setup_required' : 'ready';
        }
      }
    } catch { report.readiness = 'unavailable'; report.issues.push('Native prerequisite probe failed'); }
    if (report.readiness === 'ready') report.issues.push('Prerequisites found; native boundary and cleanup verification must run before task execution');
    validate('capability-report', report);
    this.capabilities = report;
    return structuredClone(report);
  }

  async prepare(params: any): Promise<any> {
    if (this.closing) throw new ContractError('Bridge session is closed', 'SANDBOX_UNAVAILABLE', -32010);
    if (JSON.stringify(params.owner) !== JSON.stringify(this.owner) && canonicalHash(params.owner) !== canonicalHash(this.owner)) {
      throw new ContractError('Owner differs from trusted launcher identity', 'POLICY_DENIED', -32010);
    }
    if (canonicalHash(params.policy) !== params.policy_hash || !this.workspace || params.policy.workspace_id !== this.workspace.id) {
      throw new ContractError('Policy hash or workspace binding mismatch', 'POLICY_DENIED', -32010);
    }
    if (this.preparation) {
      if (this.preparation.policy_hash !== params.policy_hash) throw new ContractError('Session policy is immutable', 'POLICY_DENIED', -32010);
      return this.sessionResult();
    }
    if (this.preparing) {
      if (this.preparingHash !== params.policy_hash) throw new ContractError('Session policy is immutable', 'POLICY_DENIED', -32010);
      return this.preparing;
    }
    if (!this.capabilities) throw new ContractError('Probe is required before prepare', 'SANDBOX_UNAVAILABLE', -32010);
    requireCapabilities(this.capabilities, params.policy);
    this.preparingHash = params.policy_hash;
    this.preparing = (async () => {
      await this.initializeNative(params.policy);
      this.preparation = { sandbox_session_id: this.owner.sandbox_session_id, policy_hash: params.policy_hash,
        owner: this.owner, capabilities: this.capabilities, state: 'ready' };
      this.preparation.policy = params.policy;
      return this.sessionResult();
    })();
    try { return await this.preparing; } finally { this.preparing = undefined; }
  }

  private sessionResult(): any { const { policy, ...result } = this.preparation; return structuredClone(result); }

  /** Lower native adapter is available to F09/F10 controlled acceptance runners.
   * Production prepare additionally requires measured capabilities; it never accepts a supplied fake report.
   */
  async initializeNative(policy: any): Promise<void> {
    validate('sandbox-policy', policy);
    if (this.initialized) throw new ContractError('Native initialization is single-use; create a new session', 'POLICY_DENIED', -32010);
    if (!this.workspace || !this.capabilities || this.capabilities.readiness !== 'ready') {
      throw new ContractError('Native prerequisites are unavailable', 'SANDBOX_UNAVAILABLE', -32010);
    }
    if (policy.filesystem.read_mode === 'strict_allowlist_required' || policy.network.dns_isolation_required && process.platform === 'win32' ||
      ['memory_bytes', 'disk_bytes', 'pids'].some(name => policy.limits[name]?.enforcement === 'hard_required')) {
      throw new ContractError('Native SRT cannot enforce this policy', 'CAPABILITY_UNSATISFIED', -32010);
    }
    if (inside(this.root, this.workspace.path) || inside(this.workspace.path, this.root) || inside(this.workspace.path, this.controlRoot) ||
      inside(this.root, this.controlRoot) || inside(this.controlRoot, this.root)) {
      throw new ContractError('Installation/control roots must be outside workspace', 'POLICY_DENIED', -32010);
    }
    if (!policy.filesystem.protected_paths.includes(this.controlRoot)) throw new ContractError('Control root must be protected', 'POLICY_DENIED', -32010);
    if (policy.workspace_id !== this.workspace.id || policy.filesystem.write_roots.some((path: string) => !inside(this.workspace!.path, resolve(path)))) {
      throw new ContractError('Policy writes exceed the bound workspace', 'POLICY_DENIED', -32010);
    }
    this.initialized = true; // Any partial initialization requires actual reset and an unknown cleanup result.
    try {
      await SandboxManager.initialize(this.nativeConfig(policy), async () => false, false);
      this.nativePolicy = structuredClone(policy);
    }
    catch (error) { throw mapSrtError(error); }
  }

  async execute(params: any): Promise<any> {
    if (params.sandbox_session_id !== this.owner.sandbox_session_id) throw new ContractError('Session ownership mismatch', 'POLICY_DENIED', -32010);
    if (this.executions.values.has(params.execution_id)) {
      this.executions.accept(params.execution_id, params.command, params.command_hash);
      return this.executions.handle(params.execution_id, true);
    }
    if (!this.preparation || this.closing) throw new ContractError('No executable prepared session', 'SANDBOX_UNAVAILABLE', -32010);
    return this.executeNative(params);
  }

  /** Used only by the controlled native verifier after actual SRT initialize; never exposed over RPC. */
  async executeNative(params: any): Promise<any> {
    validate('bridge.execute.request', params);
    if (!this.initialized || !this.nativePolicy || this.closing) throw new ContractError('No initialized native session', 'SANDBOX_UNAVAILABLE', -32010);
    if (params.sandbox_session_id !== this.owner.sandbox_session_id) throw new ContractError('Session ownership mismatch', 'POLICY_DENIED', -32010);
    if (this.executions.values.has(params.execution_id)) {
      this.executions.accept(params.execution_id, params.command, params.command_hash);
      return this.executions.handle(params.execution_id, true);
    }
    const command = params.command;
    const policy = this.nativePolicy;
    const cwd = await realpath(command.cwd);
    const info = await stat(this.workspace!.path);
    if (`${info.dev}:${info.ino}` !== this.workspace!.identity || !inside(this.workspace!.path, cwd)) throw new ContractError('Workspace identity/cwd changed', 'POLICY_DENIED', -32010);
    if (Object.keys(command.environment).some(name => unsafeEnvironment.test(name) || !policy.environment_keys.includes(name))) {
      throw new ContractError('Command environment exceeds frozen allowlist', 'POLICY_DENIED', -32010);
    }
    const remaining = Date.parse(command.deadline_utc) - Date.now();
    if (!(remaining > 0 && remaining <= policy.limits.wall_time_seconds * 1000)) throw new ContractError('Command deadline exceeds policy');
    if (command.output_limit_bytes > policy.limits.command_output_bytes) throw new ContractError('Command output limit exceeds policy');
    const accepted = this.executions.accept(params.execution_id, command, params.command_hash);
    if (accepted.reused) return this.executions.handle(params.execution_id, true);
    const e = accepted.execution;
    // Acceptance is recorded before the first wrapper/spawn; uncertainty never causes a retry.
    try {
      const outerShell = process.platform === 'win32'
        ? { exe: this.shells.pwsh, args: ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command'] }
        : this.shells.bash;
      await stat(typeof outerShell === 'string' ? outerShell : outerShell.exe);
      const wrapped = await SandboxManager.wrapWithSandboxArgv(fixedDispatcherCommand(process.execPath, resolve(this.root, 'sandbox_bridge/dist/dispatcher.js')),
        outerShell, undefined, e.abort.signal, cwd, { commandId: params.execution_id, commandText: 'ForgeCode restricted dispatcher' });
      if (this.closing || e.abort.signal.aborted) throw new Error('Execution was cancelled before launch');
      this.launchAttempted = true;
      const child = spawn(wrapped.argv[0], wrapped.argv.slice(1), { cwd, env: wrapped.env,
        shell: false, windowsHide: true, detached: process.platform === 'linux', stdio: ['pipe', 'pipe', 'pipe'] });
      e.child = child;
      e.state = 'running';
      child.stdout.on('data', bytes => e.output.feed('stdout', bytes));
      child.stderr.on('data', bytes => e.output.feed('stderr', bytes));
      child.stdin.on('error', () => { /* Child exit/EPIPE is accounted by close, never a second launch. */ });
      child.stdin.end(JSON.stringify({ command, shells: this.shells }));
      child.once('error', () => { e.state = 'indeterminate'; });
      child.once('close', code => {
        clearTimeout(e.timer); e.output.end(); e.exitCode = code;
        if (e.state !== 'indeterminate') e.state = code === null ? 'indeterminate' : 'finished';
        e.resolve();
      });
      e.timer = setTimeout(() => { void this.cancel({ execution_id: params.execution_id, reason: 'deadline', deadline_utc: new Date(Date.now() + 3000).toISOString() }); }, remaining);
      if (process.platform === 'linux') {
        e.linuxOwner = new LinuxExecutionOwner(child, e.owner);
        await e.linuxOwner.start();
      }
      return this.executions.handle(params.execution_id);
    } catch (error) {
      e.state = 'indeterminate'; e.resolve();
      throw mapSrtError(error);
    }
  }

  status(params: any): any { return this.executions.status(params.execution_id); }

  async cancel(params: any): Promise<any> {
    const e = this.executions.get(params.execution_id);
    if (e.cancellation) return structuredClone(e.cancellation);
    e.abort.abort();
    clearTimeout(e.timer);
    if (e.child && e.state === 'running') {
      // Only a still-owned ChildProcess and its anchored process group are signalled.
      try {
        if (process.platform === 'linux' && e.child.pid && e.child.exitCode === null) process.kill(-e.child.pid, 'SIGTERM');
        else e.child.kill('SIGTERM');
      } catch { e.state = 'indeterminate'; }
      const timeout = Math.max(0, Math.min(3000, Date.parse(params.deadline_utc) - Date.now()));
      let timer!: NodeJS.Timeout;
      await Promise.race([e.done, new Promise<void>(r => { timer = setTimeout(r, timeout); })]);
      clearTimeout(timer);
      if (e.child.exitCode === null) {
        try { e.child.kill('SIGKILL'); } catch { /* Remains unknown until native descendant verification. */ }
      }
    }
    e.state = e.state === 'finished' ? 'finished' : 'indeterminate';
    const cleanup = e.linuxOwner ? await e.linuxOwner.cleanup() : this.cleanup(e.owner, false);
    e.cancellation = { execution_id: params.execution_id, confirmed: cleanup.state === 'clean', cleanup };
    return structuredClone(e.cancellation);
  }

  private cleanup(owner: any, untouched: boolean): any {
    return { owner, state: untouched ? 'clean' : 'unknown', remaining_processes: 0,
      diagnostic_refs: [], completed_at_utc: untouched ? new Date().toISOString() : null };
  }

  async close(params: any): Promise<any> {
    if (params.sandbox_session_id !== this.owner.sandbox_session_id) throw new ContractError('Session ownership mismatch', 'POLICY_DENIED', -32010);
    if (this.closing) return structuredClone(await this.closing);
    this.closing = (async () => {
      if (this.preparing) await this.preparing.catch(() => undefined);
      for (const [id, e] of this.executions.values) if (e.state === 'running' || e.state === 'accepted') {
        await this.cancel({ execution_id: id, reason: 'session-close', deadline_utc: new Date(Date.now() + 3000).toISOString() });
      }
      for (const e of this.executions.values.values()) e.linuxOwner?.stop();
      if (this.initialized) {
        try { await SandboxManager.reset(); } catch { throw new ContractError('SRT reset failed; cleanup is unknown', 'CLEANUP_FAILED', -32010); }
      }
      // Upstream reset is best effort. Only a never-initialized/never-launched session can be asserted clean here.
      return this.cleanup(this.owner, !this.initialized && !this.launchAttempted);
    })();
    return structuredClone(await this.closing);
  }
}

export function mapSrtError(error: unknown): ContractError {
  if (error instanceof ContractError) return error;
  const code = (error as any)?.code;
  const kind = ['not_provisioned', 'srt_win_not_found', 'wfp_fence_inactive'].includes(code) ? 'SETUP_REQUIRED'
    : ['acl_stamp_failed', 'acl_grant_failed', 'mapped_drive_cwd'].includes(code) ? 'POLICY_DENIED' : 'COMMAND_FAILED';
  return new ContractError(`SRT operation failed${typeof code === 'string' ? ` (${code})` : ''}`, kind, -32010);
}
