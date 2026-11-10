/** DSH ACL token adapter for Forge's explicit multiple/empty write-root option.
 * Default workspace policies use DSH's published runner unchanged.
 * Grants are owned by native-session, never by this process.
 */
import { AclSandbox } from '@deepseek-ai/dsh-sandbox-windows-acl';
import { SUBPROCESS_CONTROL_ENV, SUBPROCESS_CONTROL_FD } from '@deepseek-ai/dsh-subprocess/control';
import { closeSync } from 'node:fs';

async function main() {
  const args = process.argv.slice(2);
  if (args[0] !== '--temp' || args[2] !== '--write-sid' || args[4] !== '--temp-write-sid'
      || args[6] !== '--' || !args[7]) throw new Error('Invalid scoped runner arguments');
  const sandbox = new AclSandbox({ writableDirs: [], tempDir: args[1], mode: 'workspace-write',
    writeSid: args[3], tempWriteSid: args[5], manageDacls: false });
  await sandbox.init();
  try {
    const managed = process.env[SUBPROCESS_CONTROL_ENV] === 'pipe';
    const child = sandbox.spawn({ command: args[7], args: args.slice(8), stdio: 'inherit',
      ...(managed ? { controlFileDescriptor: SUBPROCESS_CONTROL_FD } : {}) });
    if (managed) closeSync(SUBPROCESS_CONTROL_FD);
    return (await child.wait()).exitCode;
  } finally { sandbox.dispose(); }
}

main().then(code => { process.exitCode = code; }, error => {
  process.stderr.write('windows-acl-run: ' + String(error) + '\n');
  process.exitCode = 127;
});
