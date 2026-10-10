/** Retired strict acceptance entry; historical evidence is not light-mode evidence. */
export async function verifyNative(_platform: 'linux' | 'win32'): Promise<any> {
  return { schema_version: 'forge.native.acceptance.v1', status: 'blocked', checks: [],
    reason: 'Strict runtime removed; use the separate workspace-write acceptance probe', eligible_for_native_pass: false };
}
export function printNative(report: any): never {
  process.stdout.write(JSON.stringify(report) + '\n');
  process.exit(report.status === 'pass' ? 0 : report.status === 'blocked' ? 2 : 1);
}
export function failedNative(): never {
  return printNative({ schema_version: 'forge.native.acceptance.v1', status: 'fail', checks: [],
    reason: 'Strict compatibility verifier failed', eligible_for_native_pass: false });
}
