/** Trusted task entry, always invoked *inside* SRT by the adapter.
 * Input is an execution payload, never Engine/Bridge control stdin.
 */
import { spawn } from 'node:child_process';
import { delimiter, dirname, isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { strictLoads, validate } from '@forgecode/contracts';
import { MAX_LAUNCH_BYTES, readLaunchPayload } from './launch-payload.js';

export function commandArgv(command: any, shells: Record<string, string>, tools: Record<string,string> = {}): string[] {
  if (command.mode === 'argv') {
    if (!command.argv[0] || command.argv[0].includes('\0')) throw new Error('Empty or invalid program');
    if (process.platform === 'win32' && /\.(?:cmd|bat)$/i.test(command.argv[0])) {
      throw new Error('Batch files require an explicitly approved shell_script');
    }
    const name=process.platform==='win32'?command.argv[0].replace(/\.exe$/i,'').toLowerCase():command.argv[0];
    if(['git','rg'].includes(name)&&Object.hasOwn(tools,name)) {
      const executable=tools[name];
      if(!isAbsolute(executable))throw new Error('Core tool requires an absolute trusted path');
      return [executable,...command.argv.slice(1)];
    }
    return [...command.argv];
  }
  const shell = shells[command.shell];
  if (!shell || !isAbsolute(shell)) throw new Error('Approved shell is unavailable');
  return command.shell === 'pwsh'
    ? [shell, '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(command.script, 'utf16le').toString('base64')]
    : [shell, '-c', command.script];
}

export function decodeLaunchPayload(raw: Buffer) {
  const payload = strictLoads(raw) as any;
  if (!payload || Object.keys(payload).sort().join(',') !== 'command,shells,tools' || !payload.shells || typeof payload.shells !== 'object'
      || !payload.tools || Object.keys(payload.tools).sort().join(',')!=='git,rg' || !Object.values(payload.tools).every(p=>typeof p==='string'&&isAbsolute(p))) {
    throw new Error('Invalid execution payload');
  }
  validate('command-spec', payload.command);
  const input = payload.command.stdin_base64 === undefined ? undefined : Buffer.from(payload.command.stdin_base64, 'base64');
  if (input && input.toString('base64') !== payload.command.stdin_base64) throw new Error('Noncanonical task stdin');
  return { payload, input };
}

export function commandEnvironment(command: any, tools: Record<string,string>) {
  const environment = { ...process.env, ...command.environment };
  const pathKeys=Object.keys(environment).filter(name=>name.toUpperCase()==='PATH');
  const inheritedPath=pathKeys.map(name=>environment[name]).filter(Boolean).join(delimiter);
  for(const name of pathKeys)delete environment[name];
  environment.PATH=[...new Set(Object.values(tools).map(path=>dirname(path as string))),inheritedPath].filter(Boolean).join(delimiter);
  if(process.platform==='win32') {
    for(const name of Object.keys(environment))if(name.toUpperCase()==='PATHEXT')delete environment[name];
    environment.PATHEXT='.COM;.EXE;.BAT;.CMD';
  }
  // Host control variables are absent; preserve only SRT's own restricted proxy/profile overlays.
  for (const name of Object.keys(environment)) {
    if (/^(NODE_|PYTHON|LD_|DYLD_|ELECTRON_)|(?:KEY|SECRET|PASSWORD|CREDENTIAL)/i.test(name)) delete environment[name];
  }
  return environment;
}

export async function dispatch(): Promise<void> {
  const chunks: Buffer[] = [];
  let total = 0;
  if (process.argv.length > 2) {
    chunks.push(await readLaunchPayload(process.argv.slice(2)));
  } else {
    for await (const chunk of process.stdin) {
      total += chunk.length;
      if (total > MAX_LAUNCH_BYTES) throw new Error('Execution payload exceeds 1 MiB');
      chunks.push(chunk);
    }
  }
  const { payload, input } = decodeLaunchPayload(Buffer.concat(chunks));
  const argv = commandArgv(payload.command, payload.shells, payload.tools);
  const environment = commandEnvironment(payload.command, payload.tools);
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
  dispatch().catch(error => { process.stderr.write('ForgeCode dispatcher failed: ' + (error?.code ?? error?.name ?? 'unknown') + ' (' + String(error?.syscall ?? 'validation').split(' ')[0] + ')' + '\n'); process.exitCode = 125; });
}
