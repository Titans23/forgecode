import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import os from 'node:os';
import { randomUUID } from 'node:crypto';
import { resolve, sep } from 'node:path';
import { fixedSetupAction, setupDisposition, protectedDirectoryPaths, redactWindowsStatus, windowsAccountReady } from '../../../sandbox_bridge/dist/windows-adapter.js';
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
      const owner = { sandbox_session_id: 'sandbox-' + randomUUID() };
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

test('existing/shared setup cannot be refreshed or force-repaired blindly', () => {
  const empty = { user: { provisioned: false, credPresent: false, groupExists: false, realUserSid: 'host' }, wfp: { state: 'absent' } };
  assert.equal(setupDisposition('install', empty).allowed, true);
  assert.equal(setupDisposition('repair', empty).allowed, false);
  assert.equal(setupDisposition('install', { ...empty, user: { ...empty.user, provisioned: true } }).allowed, false);
  assert.equal(setupDisposition('install', { ...empty, wfp: { state: 'cannot-read' } }).allowed, true);
  assert.equal(setupDisposition('install', { ...empty, user: { ...empty.user, markerVersion: 1 } }).allowed, false);
  assert.equal(setupDisposition('install', { ...empty, user: { ...empty.user, groupExists: true } }).allowed, false);
  assert.equal(setupDisposition('install', { ...empty, user: { ...empty.user, sid: 'host' } }).allowed, false);
});

test('status removes certificates and unknown credential fields', () => {
  const safe = redactWindowsStatus({ user: { provisioned: true, sid: 'sandbox', caCertPem: 'PRIVATE', password: 'PRIVATE' },
    wfp: { state: 'installed', filters: 2, token: 'PRIVATE' } });
  assert.ok(!JSON.stringify(safe).includes('PRIVATE'));
  assert.equal(safe.user.provisioned, true);
});

test('fresh SDK account metadata is a prerequisite only; identity and credentials remain mandatory', () => {
  const user={provisioned:true,credPresent:true,groupExists:true,inSandboxGroup:true,hiddenFromLogon:true,
    inBuiltinUsers:true,sid:'sandbox',realUserSid:'host'};
  assert.equal(windowsAccountReady({user}),true);
  for(const key of ['provisioned','credPresent','groupExists','inSandboxGroup','hiddenFromLogon']) {
    assert.equal(windowsAccountReady({user:{...user,[key]:false}}),false);
  }
  assert.equal(windowsAccountReady({user:{...user,sid:'host'}}),false);
  assert.equal(windowsAccountReady({user:{...user,realUserSid:undefined}}),false);
});

test('directory denies preserve .git worktree files and distinguish missing control directories', () => {
  const root = mkdtempSync(resolve(tmpdir(), 'forge-win-path-'));
  try {
    mkdirSync(resolve(root, 'control'));
    writeFileSync(resolve(root, '.git'), 'gitdir: fixture');
    const paths = [resolve(root, 'control'), resolve(root, '.git'), resolve(root, '.forge'), resolve(root, '.env')];
    assert.deepEqual(protectedDirectoryPaths(paths), [paths[0] + sep, paths[1], paths[2] + sep, paths[3]]);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
