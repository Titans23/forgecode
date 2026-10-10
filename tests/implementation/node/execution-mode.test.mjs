import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readExecutionMode, initialExecutionMode, changeExecutionMode } from '../../../apps/desktop/dist/main/execution_mode.js';

async function fixture(run) {
  const directory = await mkdtemp(join(tmpdir(), 'forge-mode-'));
  try { await run(join(directory, 'execution-mode.json')); }
  finally { await rm(directory, { recursive: true, force: true }); }
}

test('absent preference and cancelled native choice never writes or stops Engine', () => fixture(async file => {
  assert.equal(await readExecutionMode(file), null);
  const calls = [];
  const result = await changeExecutionMode(file, 'strict', {
    assertIdle: async () => { calls.push('idle'); },
    choose: async () => { calls.push('choose'); return null; },
    shutdown: async () => { throw new Error('cancel must not stop Engine'); }
  });
  assert.deepEqual(result, { mode: 'strict', restart_required: false });
  assert.deepEqual(calls, ['idle', 'choose']);
  await assert.rejects(readFile(file), { code: 'ENOENT' });
}));

test('explicit mode survives restart only after idle recheck and confirmed owned shutdown', () => fixture(async file => {
  const calls = [];
  const dependencies = {
    assertIdle: async () => { calls.push('idle'); },
    choose: async () => { calls.push('choose'); return 'local-trusted'; },
    shutdown: async () => {
      calls.push('shutdown');
      await assert.rejects(readFile(file), { code: 'ENOENT' });
      return { state: 'confirmed', cleanup_state: 'complete' };
    }
  };
  assert.deepEqual(await changeExecutionMode(file, 'strict', dependencies), { mode: 'local-trusted', restart_required: true });
  assert.deepEqual(calls, ['idle', 'choose', 'idle', 'shutdown']);
  assert.equal(await readExecutionMode(file), 'local-trusted');
  assert.deepEqual(Object.keys(JSON.parse(await readFile(file, 'utf8'))).sort(), ['confirmed_at_utc', 'mode', 'schema_version']);
  assert.deepEqual(await changeExecutionMode(file, 'local-trusted', { ...dependencies, choose: async () => 'strict',
    shutdown: async () => ({ state: 'confirmed', cleanup_state: 'complete' }) }), { mode: 'strict', restart_required: true });
  assert.equal(await readExecutionMode(file), 'strict');
}));

test('active work or invalidated requester after confirmation cannot persist a mode', () => fixture(async file => {
  for (const rejectAt of [1, 2]) {
    let checks = 0, shutdowns = 0;
    await assert.rejects(changeExecutionMode(file, 'strict', {
      assertIdle: async () => { if (++checks === rejectAt) throw new Error('busy or stale request'); },
      choose: async () => 'local-trusted',
      shutdown: async () => { shutdowns++; return { state: 'confirmed', cleanup_state: 'complete' }; }
    }), /busy or stale/);
    assert.equal(shutdowns, 0);
    assert.equal(await readExecutionMode(file), null);
  }
}));

test('unconfirmed shutdown and malformed selections never grant local execution', () => fixture(async file => {
  const base = { assertIdle: async () => {}, choose: async () => 'local-trusted',
    shutdown: async () => ({ state: 'confirmed', cleanup_state: 'unknown' }) };
  await assert.rejects(changeExecutionMode(file, 'strict', base), /清理/);
  await assert.rejects(changeExecutionMode(file, 'strict', { ...base, choose: async () => 'unrestricted' }), /Invalid execution mode/);
  assert.equal(await readExecutionMode(file), null);
  for (const value of ['{}', '{"schema_version":"forge.desktop.execution-mode.v1","mode":"unrestricted"}', 'null']) {
    await writeFile(file, value);
    await assert.rejects(readExecutionMode(file), /Invalid execution mode/);
  }
}));


test('workspace-write is explicit, survives restart and does not bypass cleanup confirmation', () => fixture(async file => {
  const operations = { assertIdle: async () => {}, choose: async () => 'workspace-write',
    shutdown: async () => ({ state: 'confirmed', cleanup_state: 'unknown' }) };
  await assert.rejects(changeExecutionMode(file, 'strict', operations), /清理/);
  assert.equal(await readExecutionMode(file), null);
  const result = await changeExecutionMode(file, 'strict', { ...operations,
    shutdown: async () => ({ state: 'confirmed', cleanup_state: 'complete' }) });
  assert.deepEqual(result, { mode: 'workspace-write', restart_required: true });
  assert.equal(await readExecutionMode(file), 'workspace-write');
}));

test('first native confirmation persists workspace-write; cancellation remains read-only', () => fixture(async file => {
  assert.equal(await initialExecutionMode(file, async () => false), 'strict');
  assert.equal(await readExecutionMode(file), null);
  assert.equal(await initialExecutionMode(file, async () => true), 'workspace-write');
  assert.equal(await readExecutionMode(file), 'workspace-write');
  for (const mode of ['strict', 'local-trusted', 'workspace-write']) {
    await writeFile(file, JSON.stringify({ schema_version: 'forge.desktop.execution-mode.v1', mode }));
    assert.equal(await initialExecutionMode(file, async () => { throw new Error('existing consent must not be rewritten'); }), mode);
  }
  await writeFile(file, '{}');
  await assert.rejects(initialExecutionMode(file, async () => true), /Invalid execution mode/);
}));
