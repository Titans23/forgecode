/** Ownership is namespace + process start time + live ChildProcess, never PID alone. */
import { readFile, readdir, readlink, stat, statfs } from 'node:fs/promises';
import type { ChildProcess } from 'node:child_process';

export function parseProcStat(text: string): { pid: number; ppid: number; start: string; state: string } {
  const close = text.lastIndexOf(')');
  const fields = text.slice(close + 2).trim().split(/\s+/);
  const pid = Number(text.slice(0, text.indexOf(' ')));
  if (close < 0 || !Number.isSafeInteger(pid) || pid < 1 || pid > 2147483647 ||
    !/^\d+$/.test(fields[1] ?? '') || !/^\d+$/.test(fields[19] ?? '')) throw new Error('Invalid proc stat');
  return { pid, ppid: Number(fields[1]), start: fields[19], state: fields[0] };
}

interface ProcessIdentity { pid: number; ppid: number; start: string; state: string; namespace: string; namespacePid: number }
async function identity(pid: number): Promise<ProcessIdentity> {
  const value = parseProcStat(await readFile(`/proc/${pid}/stat`, 'utf8'));
  const status = await readFile(`/proc/${pid}/status`, 'utf8');
  const nspid = /^NSpid:\s+([0-9\s]+)$/m.exec(status)?.[1].trim().split(/\s+/).map(Number);
  return { ...value, namespace: await readlink(`/proc/${pid}/ns/pid`), namespacePid: nspid?.at(-1) ?? -1 };
}
async function snapshot(): Promise<{ processes: ProcessIdentity[]; complete: boolean }> {
  const result: ProcessIdentity[] = [];
  let complete = true;
  for (const entry of await readdir('/proc')) {
    if (!/^\d+$/.test(entry)) continue;
    try {
      // Different host users cannot be this unprivileged SRT workload; do not inspect their namespaces.
      if ((await stat(`/proc/${entry}`)).uid !== process.getuid?.()) continue;
      result.push(await identity(Number(entry)));
    } catch (error) {
      if (!['ENOENT', 'ESRCH'].includes((error as any).code)) complete = false;
    }
  }
  return { processes: result, complete };
}
const same = (a: ProcessIdentity, b: ProcessIdentity) => a.pid === b.pid && a.start === b.start && a.namespace === b.namespace;

export class LinuxExecutionOwner {
  private root?: ProcessIdentity;
  private hostNamespace = '';
  private namespaces = new Set<string>();
  private initializers = new Map<string, ProcessIdentity>();
  private timer?: NodeJS.Timeout;
  private sampling?: Promise<void>;
  private stopped = false;
  private observationFailed = false;
  constructor(private child: ChildProcess, readonly owner: Record<string, any>) {}

  async start(): Promise<void> {
    if (process.platform !== 'linux' || !this.child.pid) { this.observationFailed = true; return; }
    try {
      if ((await statfs('/proc')).type !== 0x9fa0) throw new Error('Process inventory is not procfs');
      this.hostNamespace = await readlink('/proc/self/ns/pid');
      this.root = await identity(this.child.pid);
      await this.sample();
      this.timer = setInterval(() => { if (!this.sampling && !this.stopped) {
        this.sampling = this.sample().finally(() => { this.sampling = undefined; });
      } }, 20);
    } catch { this.observationFailed = true; }
  }

  private async sample(): Promise<void> {
    try {
      const observed = await snapshot();
      if (!observed.complete) this.observationFailed = true;
      const root = observed.processes.find(p => p.pid === this.root?.pid);
      if (!root || !this.root || !same(root, this.root)) return;
      const owned = new Set([root.pid]);
      for (let changed = true; changed;) {
        changed = false;
        for (const item of observed.processes) if (owned.has(item.ppid) && !owned.has(item.pid)) {
          owned.add(item.pid); changed = true;
        }
      }
      for (const item of observed.processes) if (owned.has(item.pid) && item.namespace !== this.hostNamespace) {
        this.namespaces.add(item.namespace);
        if (item.namespacePid === 1) this.initializers.set(item.namespace, item);
      }
    } catch { this.observationFailed = true; }
  }

  async cleanup(): Promise<any> {
    this.stopped = true;
    clearInterval(this.timer);
    await this.sampling;
    await this.sample();
    for (const original of this.initializers.values()) {
      try {
        const current = await identity(original.pid);
        if (!same(current, original) || current.namespacePid !== 1) { this.observationFailed = true; continue; }
        process.kill(current.pid, 'SIGKILL'); // Killing an owned PID namespace init closes all its descendants in the kernel.
      } catch (error) {
        if (!['ENOENT', 'ESRCH'].includes((error as any).code)) this.observationFailed = true;
      }
    }
    if (this.root && this.child.exitCode === null && this.child.signalCode === null) {
      try {
        const current = await identity(this.root.pid);
        if (same(current, this.root)) this.child.kill('SIGTERM');
        else this.observationFailed = true;
      } catch (error) { if (!['ENOENT', 'ESRCH'].includes((error as any).code)) this.observationFailed = true; }
    }
    let remaining = 0;
    let complete = false;
    for (let attempt = 0; attempt < 20; attempt++) {
      try {
        const current = await snapshot();
        complete = current.complete;
        remaining = current.processes.filter(item => this.namespaces.has(item.namespace) && item.state !== 'Z').length;
        const wrapperAlive = this.child.exitCode === null && this.child.signalCode === null;
        if (!remaining && !wrapperAlive) break;
      } catch { this.observationFailed = true; }
      await new Promise(r => setTimeout(r, 25));
    }
    const proven = !this.observationFailed && complete && this.namespaces.size > 0 && this.initializers.size > 0 &&
      remaining === 0 && (this.child.exitCode !== null || this.child.signalCode !== null);
    return { owner: this.owner, state: proven ? 'clean' : remaining ? 'residual' : 'unknown',
      remaining_processes: remaining, diagnostic_refs: [], completed_at_utc: proven ? new Date().toISOString() : null };
  }

  stop(): void { this.stopped = true; clearInterval(this.timer); }
}
