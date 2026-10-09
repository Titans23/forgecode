/** Windows SRT 0.0.78 consumes its runner stdin instead of forwarding task input.
 * This per-session directory is granted read-only access by the native adapter.
 */
import { createHash } from 'node:crypto';
import { lstat, mkdir, open, realpath, writeFile } from 'node:fs/promises';
import { isAbsolute, resolve } from 'node:path';

export const MAX_LAUNCH_BYTES = 1048576;
export type LaunchPayload = { path: string; sha256: string };

export async function createLaunchDirectory(localAppData: string, sessionId: string): Promise<string> {
  if (!isAbsolute(localAppData) || !/^sandbox-[0-9a-f-]{36}$/.test(sessionId)) throw new Error('Invalid launch owner');
  let directory = await realpath(localAppData);
  for (const name of ['ForgeCode', 'launch-payloads']) {
    directory = resolve(directory, name);
    try { await mkdir(directory); } catch (error: any) { if (error.code !== 'EEXIST') throw error; }
    const identity = await lstat(directory);
    if (!identity.isDirectory() || identity.isSymbolicLink()) throw new Error('Launch parent is not an owned directory');
  }
  directory = resolve(directory, sessionId);
  await mkdir(directory); // Never adopt a stale session, link, or directory supplied by another owner.
  return directory;
}

export async function writeLaunchPayload(directory: string, executionId: string, raw: Buffer): Promise<LaunchPayload> {
  if (!isAbsolute(directory) || !/^exec-[0-9a-f-]{36}$/.test(executionId) || raw.length > MAX_LAUNCH_BYTES) {
    throw new Error('Invalid launch payload');
  }
  const path = resolve(directory, `${executionId}.json`);
  await writeFile(path, raw, { flag: 'wx' });
  return { path, sha256: createHash('sha256').update(raw).digest('hex') };
}

export async function readLaunchPayload(args: string[]): Promise<Buffer> {
  if (args.length !== 4 || args[0] !== '--payload-file' || args[2] !== '--payload-sha256' ||
      !isAbsolute(args[1]) || !/^[0-9a-f]{64}$/.test(args[3])) throw new Error('Invalid launch reference');
  const before = await lstat(args[1]);
  if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1) throw new Error('Unsafe launch file');
  const file = await open(args[1], 'r');
  try {
    const opened = await file.stat();
    if (opened.dev !== before.dev || opened.ino !== before.ino || opened.nlink !== 1 || opened.size > MAX_LAUNCH_BYTES) {
      throw new Error('Launch file identity or size changed');
    }
    const data = Buffer.alloc(MAX_LAUNCH_BYTES + 1);
    let length = 0;
    while (length < data.length) {
      const { bytesRead } = await file.read(data, length, data.length - length, null);
      if (!bytesRead) break;
      length += bytesRead;
    }
    const raw = data.subarray(0, length);
    if (length > MAX_LAUNCH_BYTES || createHash('sha256').update(raw).digest('hex') !== args[3]) {
      throw new Error('Launch payload integrity check failed');
    }
    return raw;
  } finally { await file.close(); }
}
