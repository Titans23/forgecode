/** DSH owns runner selection, ACL grants and child ranges; Python owns the session. */
import { Context } from '@deepseek-ai/cordis';
import { LocalSandboxProvider } from '@deepseek-ai/dsh-sandbox-local';
import { LocalSubprocessRuntime } from '@deepseek-ai/dsh-subprocess-local';
import type { SandboxPolicy, ConfinedArgv } from '@deepseek-ai/dsh-sandbox';
import { writableRoots } from '@deepseek-ai/dsh-sandbox';
import { AclWriteGrant, assertTempRootOutsideWorkspace, tempWriteSid, workspaceWriteSid } from '@deepseek-ai/dsh-sandbox-windows-acl';
import { grantArgs } from '@deepseek-ai/node-addon-system/landlock-run';
import type { SubprocessHandle } from '@deepseek-ai/dsh-subprocess';
import { createInterface } from 'node:readline';
import { once } from 'node:events';
import { basename, isAbsolute, join, toNamespacedPath } from 'node:path';
import { mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { commandArgv, commandEnvironment } from './dispatcher.js';

type Init = { workspace: string; mode: 'read-only' | 'workspace-write'; sessionId: string;
  writeRoots: string[]; shells: Record<string, string>; tools: Record<string, string> };
type Execution = { abort: AbortController; task: Promise<void>; process?: SubprocessHandle };
const ctx = new Context();
const sandboxFiber = await ctx.plugin(LocalSandboxProvider, {});
const subprocessFiber = await ctx.plugin(LocalSubprocessRuntime);
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
const active = new Map<string, Execution>();
const seen = new Set<string>();
let config: Init | undefined;
let closing = false;
let allClean = true;
let scopedTemp: { dir: string; grant: AclWriteGrant; writeSid: string; tempSid: string } | undefined;

async function send(value: object) {
  if (!process.stdout.write(JSON.stringify(value) + '\n')) await once(process.stdout, 'drain');
}

function runnerFailure(facts: ConfinedArgv, code: number | null, stderr: string): boolean {
  if (code === null || code === 0) return false;
  return facts.runnerFailureRules.some(rule =>
    (!rule.allowedExitCodes || rule.allowedExitCodes.includes(code)) && stderr.split(/\r?\n/u).some(line => {
      const value = line.trim().toLowerCase();
      return !rule.informationalLines?.some(info => info.toLowerCase() === value)
        && rule.fatalSignatures.some(signature => value.includes(signature.toLowerCase()));
    }));
}

async function confine(argv: string[], signal: AbortSignal): Promise<ConfinedArgv> {
  const value = config!;
  // A selected subtree is the DSH workspace for confinement, while tools keep
  // resolving their paths against the original caller cwd.
  const roots = value.writeRoots;
  const policy: SandboxPolicy = { mode: value.mode,
    workspaceRoot: value.mode === 'workspace-write' && roots.length === 1 ? roots[0] : value.workspace,
    sessionId: value.sessionId as SandboxPolicy['sessionId'] };
  if (value.mode !== 'workspace-write' || roots.length === 1) return ctx.sandbox.confine(argv, policy, signal);
  // Preserve explicitly configured multiple/empty write roots. Obtain DSH's
  // runner selection and failure facts without first granting the broad root.
  const facts = await ctx.sandbox.confine(argv, { ...policy, mode: 'read-only' }, signal);
  if (process.platform === 'win32') {
    if (!scopedTemp) {
      const dir = realpathSync(mkdtempSync(join(tmpdir(), 'forge-dsh-')));
      assertTempRootOutsideWorkspace(value.workspace, dir);
      const writeSid = workspaceWriteSid(JSON.stringify([value.workspace, roots]));
      const tempSid = tempWriteSid(dir);
      const standing = AclWriteGrant.create(writeSid);
      const grant = AclWriteGrant.create(tempSid);
      try {
        for (const root of roots) standing.add(toNamespacedPath(root), true);
        grant.add(toNamespacedPath(dir));
        scopedTemp = { dir, grant, writeSid, tempSid };
      } catch (error) {
        grant.dispose();
        rmSync(dir, { recursive: true });
        throw error;
      } finally { standing.dispose(); }
    }
    return { ...facts, argv: [process.execPath,
      fileURLToPath(new URL('./scoped-windows-runner.js', import.meta.url)),
      '--temp', scopedTemp.dir,
      '--write-sid', scopedTemp.writeSid, '--temp-write-sid', scopedTemp.tempSid, '--', ...argv] };
  }
  if (process.platform === 'linux') {
    const profile = basename(facts.argv[0]) === 'bwrap'
      ? ['--ro-bind', '/', '/', '--dev', '/dev', '--unshare-pid', '--proc', '/proc', '--die-with-parent',
         '--tmpfs', '/tmp', ...roots.flatMap(root => ['--bind', root, root])]
      : grantArgs({ readOnly: ['/'], readWrite: ['/dev/null', '/tmp', ...roots] });
    return { ...facts, argv: [facts.argv[0], ...profile, '--', ...argv] };
  }
  const temps = writableRoots({ mode: 'workspace-write', workspaceRoot: value.workspace })
    .filter(root => root !== realpathSync(value.workspace));
  const profile = '(version 1) (allow default) (deny file-write*) (allow file-write* (literal "/dev/null") '
    + [...roots, ...temps].map(root => `(subpath ${JSON.stringify(root)})`).join(' ') + ')';
  return { ...facts, argv: [facts.argv[0], '-p', profile, '--', ...argv] };
}

async function execute(message: any, entry: Execution) {
  const id = message.id as string;
  const command = message.command;
  let handle: SubprocessHandle | undefined;
  let facts: ConfinedArgv | undefined;
  let stderr = '';
  let exitCode: number | null = null;
  let error: string | undefined;
  let started = false;
  let clean = true;
  const remaining = Date.parse(command.deadline_utc) - Date.now();
  const timer = setTimeout(() => entry.abort.abort(), Math.max(0, remaining));
  try {
    if (!(remaining > 0)) throw new Error('Command deadline expired before confinement');
    const value = config!;
    const argv = commandArgv(command, value.shells, value.tools);
    facts = await confine(argv, entry.abort.signal);
    entry.abort.signal.throwIfAborted();
    if (Date.now() >= Date.parse(command.deadline_utc)) throw new Error('Command deadline expired before spawn');
    const temporary = process.platform === 'win32'
      ? facts.argv[facts.argv.indexOf('--temp') + 1] : '/tmp';
    handle = ctx.subprocess.spawn({ argv: facts.argv, cwd: command.cwd,
      env: commandEnvironment(command, value.tools, temporary),
      stdio: { stdin: 'pipe', stdout: 'pipe', stderr: 'pipe' }, graceMs: 1000,
      signal: entry.abort.signal });
    entry.process = handle;
    started = true;
    let bytes = 0;
    const pump = async (stream: 'stdout' | 'stderr') => {
      for await (const chunk of handle![stream]!) {
        const raw = Buffer.from(chunk);
        if (stream === 'stderr') stderr = (stderr + raw.toString('utf8')).slice(-8192);
        bytes += raw.length;
        if (bytes > command.output_limit_bytes) entry.abort.abort();
        for (let offset = 0; offset < raw.length; offset += 16384)
          await send({ id, type: 'output', stream, data: raw.subarray(offset, offset + 16384).toString('base64') });
      }
    };
    const readers = [pump('stdout'), pump('stderr')];
    void Promise.allSettled(readers);
    handle.stdin!.on('error', () => {});
    handle.stdin!.end(command.stdin_base64 ? Buffer.from(command.stdin_base64, 'base64') : undefined);
    try { exitCode = (await handle.done).exitCode; }
    finally {
      handle.terminate();
      clean = await handle.waitForExit(AbortSignal.timeout(3000));
      if (clean) await Promise.all(readers);
      else await Promise.allSettled(readers.map(async reader => {
        await Promise.race([reader, new Promise(resolve => setTimeout(resolve, 100))]);
      }));
    }
  } catch (failure) {
    error = failure instanceof Error ? failure.message : String(failure);
    if (handle) {
      handle.terminate();
      try { clean = await handle.waitForExit(AbortSignal.timeout(3000)); } catch { clean = false; }
    }
  } finally {
    clearTimeout(timer);
    allClean &&= clean;
    const failedRunner = !started || (facts !== undefined && runnerFailure(facts, exitCode, stderr));
    await send({ id, type: 'done', exit_code: exitCode, error: error ?? (failedRunner ? stderr : undefined),
      runner_failed: failedRunner && !entry.abort.signal.aborted,
      cancelled: entry.abort.signal.aborted, cleanup: { state: clean ? 'clean' : 'unknown' },
      enforcement: facts?.enforcement, runner: facts ? basename(facts.argv[0]) : undefined });
    active.delete(id);
  }
}

async function shutdown(id: string) {
  if (closing) return;
  closing = true;
  for (const entry of active.values()) entry.abort.abort();
  await Promise.allSettled([...active.values()].map(entry => entry.task));
  let clean = allClean;
  try {
    await subprocessFiber.dispose();
    // A grant is never revoked while its process range is unconfirmed.
    if (clean) await sandboxFiber.dispose();
    if (clean && scopedTemp) {
      scopedTemp.grant.dispose();
      if (realpathSync(scopedTemp.dir) !== scopedTemp.dir) throw new Error('Owned temp identity changed');
      rmSync(scopedTemp.dir, { recursive: true });
    }
  } catch { clean = false; }
  await send({ id, type: 'closed', cleanup: { state: clean ? 'clean' : 'unknown' } });
  input.close();
  process.stdin.destroy();
  process.exitCode = clean ? 0 : 125;
}

input.on('line', line => {
  void (async () => {
    if (Buffer.byteLength(line) > 1048576) throw new Error('Native request exceeds quota');
    const message = JSON.parse(line);
    if (message.op === 'init' && !config && !closing) {
      if (!['read-only', 'workspace-write'].includes(message.mode) || !isAbsolute(message.workspace)
          || !Array.isArray(message.writeRoots) || !message.writeRoots.every(isAbsolute)
          || typeof message.sessionId !== 'string') throw new Error('Invalid DSH session policy');
      config = message;
      await send({ id: message.id, type: 'ready' });
    } else if (message.op === 'execute' && config && !closing) {
      if (seen.has(message.id) || seen.size >= 4096) throw new Error('Duplicate or exhausted execution identity');
      seen.add(message.id);
      const entry: Execution = { abort: new AbortController(), task: Promise.resolve() };
      active.set(message.id, entry);
      entry.task = execute(message, entry);
    } else if (message.op === 'cancel') {
      active.get(message.id)?.abort.abort();
    } else if (message.op === 'close') {
      await shutdown(message.id);
    } else throw new Error('Invalid native session operation');
  })().catch(async error => {
    await send({ type: 'fatal', error: String(error) });
    await shutdown('fatal');
  });
});
input.on('close', () => { if (!closing) void shutdown('eof'); });
