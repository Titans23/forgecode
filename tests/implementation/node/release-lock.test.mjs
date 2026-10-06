import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { verifyReleaseLock, verifyAsset } from '../../../packaging/verify-release.mjs';

test('unresolved lock prevents build before running package tools', async () => {
  const root = await mkdtemp(join(tmpdir(), 'forge-release-'));
  try {
    await writeFile(join(root, 'release-lock.json'), JSON.stringify({ resolution_status: 'unresolved' }));
    await assert.rejects(verifyReleaseLock(root), /unresolved/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('loader refuses tampered native assets', async () => {
  const root = await mkdtemp(join(tmpdir(), 'forge-asset-'));
  try {
    await mkdir(join(root, 'native'));
    const content = Buffer.from('verified asset bytes');
    const asset = { path: 'native/helper', sha256: createHash('sha256').update(content).digest('hex') };
    await writeFile(join(root, asset.path), content);
    assert.equal(await verifyAsset(root, asset), join(root, asset.path));
    await writeFile(join(root, asset.path), 'modified');
    await assert.rejects(verifyAsset(root, asset), /integrity/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('asset loader rejects paths outside its trusted root', async () => {
  await assert.rejects(verifyAsset(process.cwd(), { path: '../outside', sha256: '0'.repeat(64) }), /outside/);
});

test('production release remains blocked when a dependency advisory is unresolved', async () => {
  const root = await mkdtemp(join(tmpdir(), 'forge-security-'));
  try {
    await writeFile(join(root, 'release-lock.json'), JSON.stringify({
      resolution_status: 'resolved', security: { status: 'blocked' },
    }));
    await assert.rejects(verifyReleaseLock(root, 'win32-x64', true), /security review/);
  } finally { await rm(root, { recursive: true, force: true }); }
});
