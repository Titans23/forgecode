/** Trusted task entry, always invoked *inside* SRT by the adapter.
 * Its JSON stdin is an execution payload, never Engine/Bridge control stdin.
 */
import { spawn } from 'node:child_process';
import { isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { strictLoads, validate } from '@forgecode/contracts';

export function commandArgv(command: any, shells: Record<string, string>): string[] {
  if (command.mode === 'argv') {
    if (!command.argv[0] || command.argv[0].includes('\0')) throw new Error('Empty or invalid program');
    if (process.platform === 'win32' && /\.(?:cmd|bat)$/i.test(command.argv[0])) {
      throw new Error('Batch files require an explicitly approved shell_script');
    }
    return [...command.argv];
  }
  const shell = shells[command.shell];
  if (!shell || !isAbsolute(shell)) throw new Error('Approved shell is unavailable');
  return command.shell === 'pwsh'
    ? [shell, '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(command.script, 'utf16le').toString('base64')]
    : [shell, '-c', command.script];
}

export async function dispatch(): Promise<void> {
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of process.stdin) {
    total += chunk.length;
    if (total > 1048576) throw new Error('Execution payload exceeds 1 MiB');
    chunks.push(chunk);
  }
  const payload = strictLoads(Buffer.concat(chunks)) as any;
  if (!payload || Object.keys(payload).sort().join(',') !== 'command,shells' || !payload.shells || typeof payload.shells !== 'object') {
    throw new Error('Invalid execution payload');
  }
  validate('command-spec', payload.command);
  const input = payload.command.stdin_base64 === undefined ? undefined : Buffer.from(payload.command.stdin_base64, 'base64');
  if (input && input.toString('base64') !== payload.command.stdin_base64) throw new Error('Noncanonical task stdin');
  const argv = commandArgv(payload.command, payload.shells);
  const environment = { ...process.env, ...payload.command.environment };
  // Host control variables are absent; preserve only SRT's own restricted proxy/profile overlays.
  for (const name of Object.keys(environment)) {
    if (/^(NODE_|PYTHON|LD_|DYLD_|ELECTRON_)|(?:KEY|SECRET|PASSWORD|CREDENTIAL)/i.test(name)) delete environment[name];
  }
  const child = spawn(argv[0], argv.slice(1), { cwd: payload.command.cwd, env: environment,
    shell: false, windowsHide: true, stdio: [input === undefined ? 'ignore' : 'pipe', 'inherit', 'inherit'] });
  if (child.stdin) {
    child.stdin.on('error', () => { /* Closed task stdin never triggers replay. */ });
    child.stdin.end(input);
  }
  const forward = (signal: NodeJS.Signals) => { child.kill(signal); };
  process.on('SIGTERM', forward);
  process.on('SIGINT', forward);
  const outcome = await new Promise<number>((done, reject) => {
    child.once('error', reject);
    child.once('exit', (code, signal) => done(code ?? (signal ? 128 : 1)));
  });
  process.exitCode = outcome;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  dispatch().catch(() => { process.stderr.write('ForgeCode dispatcher failed\n'); process.exitCode = 125; });
}
