import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import os from 'node:os';
import { randomUUID } from 'node:crypto';
import { resolve, sep } from 'node:path';
import { fixedSetupAction, runWindowsSetup } from '../../../sandbox_bridge/dist/windows-adapter.js';
import { SrtAdapter } from '../../../sandbox_bridge/dist/srt-adapter.js';

test('Windows 10 desktop metadata reaches prerequisite diagnosis without native verification', async t => {
  // Only OS metadata classification is controlled. No setup/helper is supplied or invoked.
  const descriptors = Object.fromEntries(['platform', 'arch'].map(key => [key, Object.getOwnPropertyDescriptor(process, key)]));
  const root = mkdtempSync(resolve(tmpdir(), 'forge-win10-host-'));
  let host;
  t.mock.method(os, 'release', () => host.release);
  t.mock.method(os, 'version', () => host.version);
  try {
    for (host of [
      { release: '10.0.19045', version: 'Windows 10 Pro', arch: 'x64', expected: true },
      { release: '10.0.19044', version: 'Windows 10 Pro', arch: 'x64', expected: false },
      { release: '10.0.26100', version: 'Windows 11 Pro', arch: 'x64', expected: true },
      { release: '10.0.26100', version: 'Windows Server 2025', arch: 'x64', expected: false },
      { release: '10.0.19045', version: 'Windows 10 Pro', arch: 'arm64', expected: false },
    ]) {
      Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
      Object.defineProperty(process, 'arch', { value: host.arch, configurable: true });
      const owner = { engine_epoch: 'epoch-' + randomUUID(), sandbox_session_id: 'sandbox-' + randomUUID(), execution_id: null };
      const adapter = new SrtAdapter(root, root, owner, {}, () => true);
      const report = await adapter.probe({ workspace_path: root, workspace_id: 'ws-' + randomUUID() });
      assert.equal(report.platform === 'windows-native', host.expected, JSON.stringify(host));
      assert.equal(report.readiness, 'unavailable');
      assert.ok(Object.values(report.verification).every(proof => proof.status !== 'verified'));
      await adapter.close({ sandbox_session_id: owner.sandbox_session_id });
    }
  } finally {
    for (const [key, descriptor] of Object.entries(descriptors)) Object.defineProperty(process, key, descriptor);
    t.mock.restoreAll();
    rmSync(root, { recursive: true, force: true });
  }
});

test('setup only accepts fixed actions, never admin command parameters', () => {
  for (const action of ['install', 'repair', 'diagnose']) assert.equal(fixedSetupAction(action), action);
  for (const action of ['uninstall', 'force', 'install --force', { action: 'install', command: 'whoami' }]) assert.throws(() => fixedSetupAction(action));
});

test('retired setup never touches shared accounts, filters or credentials', async () => {
  for (const action of ['install', 'repair', 'diagnose']) {
    const report = await runWindowsSetup(action);
    assert.equal(report.status, 'blocked');
    assert.equal(report.reason, 'strict_runtime_removed');
    assert.equal(report.administrator_invoked, false);
    assert.equal(report.fresh_install_allowed, false);
    assert.equal(report.shared_activity, 'not_observed');
  }
});
