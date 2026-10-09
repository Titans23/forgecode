/** Fixed DSH entry. ForgeCode owns the enclosing Job and revokes temp grants after it is empty. */
import { spawn } from 'node:child_process';
import { lstat, realpath } from 'node:fs/promises';
import { dirname, isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readLaunchPayload } from './launch-payload.js';
import { commandArgv, commandEnvironment, decodeLaunchPayload } from './dispatcher.js';

async function directory(path: string): Promise<string> {
  if (!isAbsolute(path)) throw new Error('Absolute owned directory required');
  const info = await lstat(path);
  if (!info.isDirectory() || info.isSymbolicLink()) throw new Error('Owned directory changed');
  return realpath(path);
}

async function main() {
  if (!['win32', 'linux'].includes(process.platform)) throw new Error('Unsupported native platform');
  const args = process.argv.slice(2);
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
  const raw = await readLaunchPayload(args.slice(4));
  if (process.platform === 'linux') {
    const { wrapCommandWithSandboxLinux, cleanupBwrapMountPoints } = await import('@anthropic-ai/sandbox-runtime/dist/sandbox/linux-sandbox-utils.js');
    const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
    const command = [process.execPath, resolve(dirname(fileURLToPath(import.meta.url)), 'dispatcher.js'), ...args.slice(4)].map(quote).join(' ');
    const wrapped = await wrapCommandWithSandboxLinux({ command, needsNetworkRestriction: false,
      readConfig: { denyOnly: [] }, writeConfig: { allowOnly: [workspace, temp], denyWithinAllow: [] },
      binShell: '/bin/bash', bwrapPath: '/usr/bin/bwrap', ripgrepConfig: { command: '/usr/bin/rg' },
      setEnvVars: { TMPDIR: temp, TMP: temp, TEMP: temp }, allowGitConfig: false });
    const child = spawn('/bin/bash', ['-c', wrapped], { cwd: workspace, stdio: 'inherit', shell: false });
    process.exitCode = await new Promise<number>((resolve, reject) => {
      child.once('error', reject); child.once('close', code => resolve(code ?? 125));
    });
    cleanupBwrapMountPoints();
    return;
  }
  const { AclWriteGrant, assertTempRootOutsideWorkspace, tempWriteSid, workspaceWriteSid } = await import('@deepseek-ai/dsh-sandbox-windows-acl');
  assertTempRootOutsideWorkspace(workspace, temp);
  const writeSid = workspaceWriteSid(workspace), tempSid = tempWriteSid(temp);
  const workspaceGrant = AclWriteGrant.create(writeSid);
  try { workspaceGrant.add(workspace, true); } finally { workspaceGrant.dispose(); }
  const tempGrant = AclWriteGrant.create(tempSid);
  // Register the path before applying, so a partially applied grant can be revoked by the parent.
  tempGrant.add(temp);
  const { payload } = decodeLaunchPayload(raw);
  const argv = commandArgv(payload.command, payload.shells, payload.tools);
  const environment = commandEnvironment(payload.command, payload.tools);
  for (const key of Object.keys(process.env)) if (!(key in environment)) delete process.env[key];
  Object.assign(process.env, environment);
  process.chdir(payload.command.cwd);
  process.argv = [process.execPath, process.argv[1], '--workspace', workspace, '--temp', temp,
    '--mode', 'workspace-write', '--write-sid', writeSid, '--temp-write-sid', tempSid,
    '--', ...argv];
  await import('@deepseek-ai/dsh-sandbox-windows-acl/runner');
}
main().catch(error => {
  process.stderr.write('ForgeCode workspace sandbox unavailable: ' + (error instanceof Error ? error.message : String(error)) + '\n');
  process.exitCode = 125;
});
