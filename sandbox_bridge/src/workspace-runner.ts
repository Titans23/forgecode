/** Fixed DSH entry. ForgeCode owns the enclosing Job and revokes temp grants after it is empty. */
import { spawn } from 'node:child_process';
import { lstat, realpath } from 'node:fs/promises';
import { delimiter, dirname, isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readLaunchPayload } from './launch-payload.js';
import { commandArgv, commandEnvironment, decodeLaunchPayload } from './dispatcher.js';
import { bubblewrapArgv } from './bubblewrap.js';

async function directory(path: string): Promise<string> {
  if (!isAbsolute(path)) throw new Error('Absolute owned directory required');
  const info = await lstat(path);
  if (!info.isDirectory() || info.isSymbolicLink()) throw new Error('Owned directory changed');
  return realpath(path);
}

async function main() {
  if (!['win32', 'linux'].includes(process.platform)) throw new Error('Unsupported native platform');
  const args = process.argv.slice(2);
  const readOnly = args.at(-1) === '--read-only';
  if (readOnly) args.pop();
  let writeRoots: string[] | undefined;
  if (args.at(-2) === '--write-roots') {
    writeRoots = JSON.parse(args.pop()!);
    args.pop();
    if (!Array.isArray(writeRoots) || !writeRoots.every(root => typeof root === 'string' && isAbsolute(root)))
      throw new Error('Invalid write roots');
  }
  let pythonRuntime: string | undefined;
  if (args.at(-2) === '--python-runtime') {
    pythonRuntime = await directory(args.pop()!);
    args.pop();
  }
  if (args.length === 2 && args[0] === '--cleanup-temp') {
    const temp = await directory(args[1]);
    if (process.platform !== 'win32') throw new Error('ACL cleanup requires Windows');
    const { AclWriteGrant, tempWriteSid } = await import('@deepseek-ai/dsh-sandbox-windows-acl');
    const grant = AclWriteGrant.create(tempWriteSid(temp));
    try { grant.add(temp); } finally { grant.dispose(); }
    // The parent also verifies an empty Job and removes this exact owned tree.
    process.stdout.write(JSON.stringify({ temp_grant_revoked: true }) + '\n');
    return;
  }
  if (args.length !== 8 || args[0] !== '--workspace' || args[2] !== '--temp') throw new Error('Invalid owned launch');
  const workspace = await directory(args[1]), temp = await directory(args[3]);
  writeRoots ??= [workspace];
  writeRoots = await Promise.all(writeRoots.map(directory));
  if (writeRoots.some(root => root !== workspace && !root.startsWith(workspace + (process.platform === 'win32' ? '\\' : '/'))))
    throw new Error('Write scope exceeds workspace');
  const raw = await readLaunchPayload(args.slice(4));
  if (process.platform === 'linux') {
    const binary = await realpath('/usr/bin/bwrap');
    const info = await lstat(binary);
    if (!info.isFile() || info.uid !== 0 || (info.mode & 0o022)) throw new Error('Untrusted bubblewrap executable');
    const command = [resolve(dirname(fileURLToPath(import.meta.url)), 'dispatcher.js'), ...args.slice(4)];
    const child = spawn(binary, bubblewrapArgv(workspace, temp, process.execPath, command, readOnly, writeRoots),
      { cwd: workspace, stdio: 'inherit', shell: false });
    process.exitCode = await new Promise<number>((resolve, reject) => {
      child.once('error', reject); child.once('close', code => resolve(code ?? 125));
    });
    return;
  }
  const { AclSandbox, AclWriteGrant, assertTempRootOutsideWorkspace, tempWriteSid, workspaceWriteSid } = await import('@deepseek-ai/dsh-sandbox-windows-acl');
  assertTempRootOutsideWorkspace(workspace, temp);
  // A standing broad-workspace ACE must never authorize a later narrower scope.
  const writeSid = workspaceWriteSid(JSON.stringify([workspace, writeRoots])), tempSid = tempWriteSid(temp);
  if (!readOnly) {
    const workspaceGrant = AclWriteGrant.create(writeSid);
    try { for (const root of writeRoots) workspaceGrant.add(root, true); } finally { workspaceGrant.dispose(); }
  }
  const tempGrant = AclWriteGrant.create(tempSid);
  tempGrant.add(temp);
  const { payload } = decodeLaunchPayload(raw);
  const argv = commandArgv(payload.command, payload.shells, payload.tools);
  const environment = commandEnvironment(payload.command, payload.tools, temp);
  if (pythonRuntime) {
    if (pythonRuntime === workspace || pythonRuntime.startsWith(workspace + '\\') ||
        pythonRuntime === temp || pythonRuntime.startsWith(temp + '\\')) throw new Error('Python runtime must be outside task writes');
    environment.PATH = pythonRuntime + delimiter + environment.PATH;
    environment.FORGE_NATIVE_SCOPE = JSON.stringify({ temp, tempSid });
  }
  for (const key of Object.keys(process.env)) if (!(key in environment)) delete process.env[key];
  Object.assign(process.env, environment);
  process.chdir(payload.command.cwd);
  // Project read-only still needs a private scratch capability. Use a distinct
  // ungranted workspace SID so standing grants cannot authorize project writes.
  const sandbox = new AclSandbox({ mode: 'workspace-write',
    writableDirs: readOnly ? [] : writeRoots, manageDacls: false,
    tempDir: temp, writeSid: readOnly ? workspaceWriteSid('readonly:' + temp) : writeSid, tempWriteSid: tempSid });
  try {
    await sandbox.init();
    const child = sandbox.spawn({ command: argv[0], args: argv.slice(1), cwd: payload.command.cwd, stdio: 'inherit' });
    process.exitCode = (await child.wait()).exitCode;
  } finally {
    sandbox.dispose();
  }
}
main().catch(error => {
  process.stderr.write('ForgeCode workspace sandbox unavailable: ' + (error instanceof Error ? error.message : String(error)) + '\n');
  process.exitCode = 125;
});
