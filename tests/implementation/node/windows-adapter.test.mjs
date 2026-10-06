import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, sep } from 'node:path';
import { fixedSetupAction, setupDisposition, protectedDirectoryPaths, redactWindowsStatus } from '../../../sandbox_bridge/dist/windows-adapter.js';

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

test('directory denies preserve .git worktree files and distinguish missing control directories', () => {
  const root = mkdtempSync(resolve(tmpdir(), 'forge-win-path-'));
  try {
    mkdirSync(resolve(root, 'control'));
    writeFileSync(resolve(root, '.git'), 'gitdir: fixture');
    const paths = [resolve(root, 'control'), resolve(root, '.git'), resolve(root, '.forge'), resolve(root, '.env')];
    assert.deepEqual(protectedDirectoryPaths(paths), [paths[0] + sep, paths[1], paths[2] + sep, paths[3]]);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
