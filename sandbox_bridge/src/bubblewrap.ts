/** One explicit Linux write boundary. No proxy, runtime framework or host fallback. */
import { isAbsolute } from 'node:path';

export function bubblewrapArgv(workspace: string, temp: string, executable: string, args: string[]): string[] {
  if (![workspace, temp, executable].every(isAbsolute) || workspace === '/' ||
      workspace === temp || temp.startsWith(workspace + '/') || workspace.startsWith(temp + '/')) {
    throw new Error('Separate absolute workspace and private temp required');
  }
  return ['--new-session', '--die-with-parent', '--unshare-user', '--unshare-pid', '--cap-drop', 'ALL',
    '--ro-bind', '/', '/', '--bind', workspace, workspace, '--bind', temp, temp,
    '--proc', '/proc', '--dev', '/dev', '--chdir', workspace,
    '--setenv', 'TMPDIR', temp, '--setenv', 'TMP', temp, '--setenv', 'TEMP', temp,
    '--', executable, ...args];
}
