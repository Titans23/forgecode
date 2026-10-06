/** Fixed Windows setup actions. Upstream owns UAC, account, ACL, Job and WFP. */
import { lstatSync } from 'node:fs';
import { basename, isAbsolute, sep } from 'node:path';
import os from 'node:os';
import { checkWindowsSandboxStatusAsync, installWindowsSandboxAsync, verifyWindowsWfpEgress,
  type WindowsSandboxStatus } from '@anthropic-ai/sandbox-runtime/dist/sandbox/windows-sandbox-utils.js';
import { ContractError } from '@forgecode/contracts';

export function fixedSetupAction(value: unknown): 'install' | 'repair' | 'diagnose' {
  if (value !== 'install' && value !== 'repair' && value !== 'diagnose') throw new ContractError('Unknown fixed setup action');
  return value;
}

export function redactWindowsStatus(status: WindowsSandboxStatus): any {
  const pick = (source: any, keys: string[]) => Object.fromEntries(keys.filter(key => Object.hasOwn(source, key)).map(key => [key, source[key]]));
  return { user: pick(status.user, ['provisioned', 'sid', 'groupExists', 'groupSid', 'inBuiltinUsers', 'inSandboxGroup',
    'hiddenFromLogon', 'credPresent', 'markerVersion', 'realUserSid']),
    wfp: pick(status.wfp, ['state', 'filters', 'portRange', 'userSid']) };
}

export function setupDisposition(action: 'install' | 'repair' | 'diagnose', status: WindowsSandboxStatus): { allowed: boolean; reason: string } {
  if (action === 'diagnose') return { allowed: true, reason: 'read_only' };
  if (status.user.sid === status.user.realUserSid) return { allowed: false, reason: 'sandbox_identity_cannot_setup' };
  if (action === 'repair') return { allowed: false, reason: 'shared_activity_unobservable; audited administrator reconciliation required' };
  const fresh = status.user.provisioned === false && status.user.credPresent === false && status.user.groupExists === false &&
    !status.user.sid && status.user.markerVersion === undefined;
  if (!fresh || !['absent', 'cannot-read'].includes(status.wfp.state)) return { allowed: false, reason: 'existing_shared_setup; do not rotate credentials or overwrite filters' };
  // Unelevated BFE cannot-read alone does not block first setup when account/group/marker are absent.
  // The fixed upstream install is explicitly approved and force remains unset: config collisions must fail.
  return { allowed: true, reason: 'fresh_setup_requires_native_confirmation_and_UAC' };
}

export function protectedDirectoryPaths(paths: string[]): string[] {
  return paths.map(path => {
    let directory = false;
    try { directory = lstatSync(path).isDirectory(); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      directory = ['.git', '.forge'].includes(basename(path).toLowerCase());
    }
    // SRT's Windows ACL stamper uses a trailing separator for a missing DIRECTORY placeholder.
    return directory && !path.endsWith(sep) ? path + sep : path;
  });
}

export async function windowsPrerequisites(helper: string): Promise<{ status: any; ready: boolean }> {
  if (!isAbsolute(helper)) throw new ContractError('Trusted helper must be absolute');
  const srtWin = { exe: helper, prependArgs: ['--srt-win'] };
  const status = await checkWindowsSandboxStatusAsync({ srtWin });
  let ready = status.user.provisioned && status.user.credPresent && status.user.inSandboxGroup && status.user.hiddenFromLogon &&
    !status.user.inBuiltinUsers && status.user.sid !== status.user.realUserSid;
  if (ready) {
    // BFE enumeration may be admin-only. The fixed controlled listener is the actual prerequisite check.
    await verifyWindowsWfpEgress({ srtWin });
    ready = true;
  }
  return { status: redactWindowsStatus(status), ready };
}

/** Called only by a fixed trusted Main setup launcher after its native dialog, never Bridge RPC. */
export async function runWindowsSetup(input: unknown, helper: string): Promise<any> {
  const action = fixedSetupAction(input);
  if (process.platform !== 'win32' || process.arch !== 'x64' || Number(os.release().split('.')[2]) < 22000) {
    return { status: 'blocked', action, reason: 'Windows 11 x64 setup host unavailable', administrator_invoked: false };
  }
  if (!isAbsolute(helper)) throw new ContractError('Trusted helper must be absolute');
  const srtWin = { exe: helper, prependArgs: ['--srt-win'] };
  const status = await checkWindowsSandboxStatusAsync({ srtWin });
  const disposition = setupDisposition(action, status);
  if (action === 'diagnose' || !disposition.allowed) return { status: 'blocked', action, reason: disposition.reason,
    administrator_invoked: false, native_status: redactWindowsStatus(status), shared_activity: 'not_observed',
    fresh_install_allowed: setupDisposition('install', status).allowed };
  try {
    const installed = await installWindowsSandboxAsync({ srtWin, timeoutMs: 120000 });
    return { status: 'blocked', action, administrator_invoked: true, cancelled: installed.cancelled === true,
      setup_state: installed.cancelled ? 'cancelled' : installed.user.provisioned && installed.user.credPresent ? 'installed' : 'unknown',
      reason: installed.cancelled ? 'UAC cancelled; no execution fallback' : 'Setup completed; native boundary acceptance still required',
      native_status: redactWindowsStatus(installed), eligible_for_native_pass: false };
  } catch (error) {
    return { status: 'blocked', action, administrator_invoked: true, reason: 'Fixed native setup failed',
      code: typeof (error as any)?.code === 'string' ? (error as any).code : 'setup_failed' };
  }
}
