/** Read actual OS listener ownership; an unavailable observation never becomes an empty pass. */
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile, readdir, readlink } from 'node:fs/promises';
import { resolve } from 'node:path';
const execute = promisify(execFile);
export async function ownedTcpListeners(pids: number[]): Promise<number> {
  if (!pids.length || pids.some(pid => !Number.isSafeInteger(pid) || pid <= 0)) throw new Error('Invalid owned process IDs');
  if (process.platform === 'win32') {
    if (!process.env.SystemRoot) throw new Error('Windows system directory is unavailable');
    const powershell = resolve(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
    const script = `$ErrorActionPreference='Stop'; $owned=@(${pids.join(',')}); @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $owned -contains $_.OwningProcess }).Count`;
    const result = await execute(powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script], { timeout: 10000, windowsHide: true });
    if (!/^\d+$/.test(result.stdout.trim())) throw new Error('Listener observation is malformed');
    return Number(result.stdout.trim());
  }
  if (process.platform === 'linux') {
    const sockets = new Set<string>();
    for (const pid of pids) for (const descriptor of await readdir(`/proc/${pid}/fd`)) {
      try { const link = await readlink(`/proc/${pid}/fd/${descriptor}`); const match = /^socket:\[(\d+)\]$/.exec(link); if (match) sockets.add(match[1]); }
      catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    }
    let count = 0;
    for (const table of ['tcp', 'tcp6']) for (const line of (await readFile(`/proc/net/${table}`, 'utf8')).trim().split('\n').slice(1)) {
      const values = line.trim().split(/\s+/);
      if (values[3] === '0A' && sockets.has(values[9])) count++;
    }
    return count;
  }
  throw new Error('Listener observation requires Windows or Linux');
}
