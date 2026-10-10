/** Retired strict setup compatibility; never invokes UAC or shared SRT. */
import os from 'node:os';
import { ContractError } from '@forgecode/contracts';
export function windowsSupported(): boolean {
  const [major, , build] = os.release().split('.').map(Number);
  return process.platform === 'win32' && process.arch === 'x64' && major === 10 && build >= 19045
    && /^Windows (10|11)(?:\s|$)/.test(os.version());
}
export function fixedSetupAction(value: unknown): 'install' | 'repair' | 'diagnose' {
  if (value !== 'install' && value !== 'repair' && value !== 'diagnose') throw new ContractError('Unknown fixed setup action');
  return value;
}
export async function runWindowsSetup(input: unknown, _helper?: string): Promise<any> {
  return { status: 'blocked', action: fixedSetupAction(input), reason: 'strict_runtime_removed',
    administrator_invoked: false, fresh_install_allowed: false, shared_activity: 'not_observed' };
}
