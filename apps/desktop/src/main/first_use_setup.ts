/** The marker only suppresses repeated prompts. It never authorizes a task. */
import { readFile, writeFile } from 'node:fs/promises';

export async function firstUseSetup(marker: string, setup: (action: 'diagnose' | 'install') => Promise<any>) {
  let attempted = false;
  try {
    const raw = await readFile(marker, 'utf8');
    if (raw.length > 4096 || JSON.parse(raw).schema_version !== 'forge.setup-prompt.v1') throw new Error('Invalid setup prompt record');
    attempted = true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  const diagnosis = await setup('diagnose');
  if (attempted || diagnosis.fresh_install_allowed !== true) return diagnosis;
  const result = await setup('install');
  await writeFile(marker, JSON.stringify({ schema_version: 'forge.setup-prompt.v1', attempted_at: new Date().toISOString() }) + '\n', { flag: 'wx' });
  return result;
}
