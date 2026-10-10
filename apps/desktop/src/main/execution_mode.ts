/** Main owns this preference. Renderer can only request a native choice, never grant a mode. */
import { randomUUID } from 'node:crypto';
import { readFile, rename, unlink, writeFile } from 'node:fs/promises';

export type ExecutionMode = 'strict' | 'local-trusted' | 'workspace-write';
export function executionMode(value: unknown): ExecutionMode {
  if (value !== 'strict' && value !== 'local-trusted' && value !== 'workspace-write') throw new Error('Invalid execution mode');
  return value;
}

export async function readExecutionMode(file: string): Promise<ExecutionMode | null> {
  let raw: string;
  try { raw = await readFile(file, 'utf8'); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null; throw error; }
  if (raw.length > 4096) throw new Error('Invalid execution mode preference');
  const value = JSON.parse(raw);
  if (!value || value.schema_version !== 'forge.desktop.execution-mode.v1') throw new Error('Invalid execution mode preference');
  return executionMode(value.mode);
}

export async function initialExecutionMode(file: string, confirm: () => Promise<boolean>): Promise<ExecutionMode> {
  const existing = await readExecutionMode(file);
  if (existing !== null) return existing;
  if (!await confirm()) return 'strict'; // Read-only compatibility mode; cancellation persists no consent.
  await saveExecutionMode(file, 'workspace-write');
  return 'workspace-write';
}

async function saveExecutionMode(file: string, mode: ExecutionMode): Promise<void> {
  const temporary = file + '.' + randomUUID() + '.tmp';
  try {
    await writeFile(temporary, JSON.stringify({ schema_version: 'forge.desktop.execution-mode.v1', mode,
      confirmed_at_utc: new Date().toISOString() }) + '\n', { flag: 'wx', mode: 0o600 });
    await rename(temporary, file);
  } catch (error) {
    await unlink(temporary).catch(() => {});
    throw error;
  }
}

export async function changeExecutionMode(file: string, current: ExecutionMode, operations: {
  choose(): Promise<ExecutionMode | null>;
  assertIdle(): Promise<void>;
  shutdown(): Promise<{ state: string; cleanup_state: string }>;
}): Promise<{ mode: ExecutionMode; restart_required: boolean }> {
  await operations.assertIdle();
  const selected = await operations.choose();
  if (selected === null || selected === current) return { mode: current, restart_required: false };
  const mode = executionMode(selected);
  await operations.assertIdle();
  const report = await operations.shutdown();
  if (report.state !== 'confirmed' || report.cleanup_state !== 'complete') throw new Error('当前执行环境的退出或清理未确认，未切换模式。请先核对执行状态。');
  await saveExecutionMode(file, mode);
  return { mode, restart_required: true };
}
