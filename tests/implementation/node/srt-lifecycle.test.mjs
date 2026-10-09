import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, realpath, stat, rm, access, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { SandboxManager } from '@anthropic-ai/sandbox-runtime';
import { canonicalHash } from '../../../packages/contracts/dist/index.js';
import { SrtAdapter } from '../../../sandbox_bridge/dist/srt-adapter.js';

function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}

async function fixture(t) {
  const directory = await mkdtemp(resolve(tmpdir(), 'forge-srt-lifecycle-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  for (const name of ['install', 'workspace', 'control', 'local']) await mkdir(resolve(directory, name));
  const workspace = await realpath(resolve(directory, 'workspace'));
  const info = await stat(workspace);
  const owner = { engine_epoch: `epoch-${randomUUID()}`, sandbox_session_id: `sandbox-${randomUUID()}`, execution_id: null };
  const adapter = new SrtAdapter(resolve(directory, 'install'), resolve(directory, 'control'), owner,
    { powershell: process.execPath, 'srt-win': process.execPath }, () => true);
  // Lifecycle doubles never call native initialize/spawn and never prove isolation.
  adapter.workspace = { id: `ws-${randomUUID()}`, path: workspace, identity: `${info.dev}:${info.ino}` };
  adapter.capabilities = { readiness: 'ready' };
  const policy = JSON.parse(await readFile('contracts/v1/examples/sandbox-policy.valid.json'));
  policy.workspace_id = adapter.workspace.id;
  policy.filesystem.write_roots = [workspace];
  policy.filesystem.protected_paths = [adapter.controlRoot];
  const previous = process.env.LOCALAPPDATA;
  process.env.LOCALAPPDATA = resolve(directory, 'local');
  t.after(() => { if (previous === undefined) delete process.env.LOCALAPPDATA; else process.env.LOCALAPPDATA = previous; });
  const command = { mode: 'argv', argv: [process.execPath, '-e', 'process.exit(0)'], cwd: workspace,
    environment: {}, deadline_utc: new Date(Date.now() + 10000).toISOString(), output_limit_bytes: 4096 };
  const request = { sandbox_session_id: owner.sandbox_session_id, execution_id: `exec-${randomUUID()}`,
    command, command_hash: canonicalHash(command) };
  return { adapter, policy, request, close: () => adapter.close({ sandbox_session_id: owner.sandbox_session_id }) };
}

test('closed adapter rejects direct native initialization without touching SRT', async t => {
  const { adapter, policy, close } = await fixture(t);
  let calls = 0;
  t.mock.method(SandboxManager, 'initialize', async () => { calls++; });
  await close();
  await assert.rejects(adapter.initializeNative(policy), /closed/);
  assert.equal(calls, 0);
});

for (const fails of [false, true]) test(`close waits for direct native initialization${fails ? ' failure' : ''} before resetting`, async t => {
  const { adapter, policy, close } = await fixture(t);
  const entered = deferred(), release = deferred();
  let resets = 0, closed = false;
  t.mock.method(SandboxManager, 'initialize', async () => {
    entered.resolve();
    await release.promise;
    if (fails) throw new Error('partial initialization');
  });
  t.mock.method(SandboxManager, 'reset', async () => { resets++; });
  const initializing = adapter.initializeNative(policy).then(() => null, error => error);
  await entered.promise;
  const closing = close().then(result => { closed = true; return result; });
  try {
    await new Promise(r => setImmediate(r));
    assert.equal(resets, 0, 'Reset must follow the last initialization side effect');
    assert.equal(closed, false);
  } finally { release.resolve(); await initializing; await closing; }
  assert.equal(resets, 1);
  assert.equal((await closing).state, 'unknown');
});

test('close during cwd validation rejects execution before acceptance or wrapping', async t => {
  const { adapter, policy, request, close } = await fixture(t);
  adapter.initialized = true;
  adapter.nativePolicy = policy;
  let wraps = 0;
  t.mock.method(SandboxManager, 'reset', async () => {});
  t.mock.method(SandboxManager, 'wrapWithSandboxArgv', async () => { wraps++; throw new Error('must not wrap'); });
  const executing = adapter.executeNative(request);
  const rejected = assert.rejects(executing, /closed/);
  await close();
  await rejected;
  assert.equal(adapter.executions.values.size, 0);
  assert.equal(wraps, 0);
});

test('cancel and close wait for pending wrapper and payload work before reset', async t => {
  const { adapter, policy, request, close } = await fixture(t);
  t.mock.method(SandboxManager, 'initialize', async () => {});
  await adapter.initializeNative(policy);
  const entered = deferred(), release = deferred();
  let resets = 0, cancelled = false;
  t.mock.method(SandboxManager, 'reset', async () => { resets++; });
  t.mock.method(SandboxManager, 'wrapWithSandboxArgv', async () => {
    entered.resolve();
    await release.promise;
    return { argv: [process.execPath, '-e', 'throw new Error("must not spawn")'], env: {} };
  });
  const executing = adapter.executeNative(request);
  const rejected = assert.rejects(executing, /SRT operation failed/);
  await entered.promise;
  const cancelling = adapter.cancel({ execution_id: request.execution_id,
    deadline_utc: new Date(Date.now() + 3000).toISOString() }).then(result => { cancelled = true; return result; });
  const closing = close();
  try {
    await new Promise(r => setImmediate(r));
    assert.equal(cancelled, false, 'Cancellation cannot complete while launch work can still create resources');
    assert.equal(resets, 0);
    for (const path of adapter.launchFiles) await access(path);
  } finally { release.resolve(); await rejected; await cancelling; await closing; }
  assert.equal(adapter.executions.get(request.execution_id).child, undefined);
  assert.equal(resets, 1);
  assert.equal((await closing).state, 'unknown');
  assert.equal(adapter.launchFiles.size, 0);
});

test('expired cancellation returns unknown while close still waits for launch work', async t => {
  const { adapter, policy, request, close } = await fixture(t);
  t.mock.method(SandboxManager, 'initialize', async () => {});
  await adapter.initializeNative(policy);
  const entered = deferred(), release = deferred();
  let resets = 0;
  t.mock.method(SandboxManager, 'reset', async () => { resets++; });
  t.mock.method(SandboxManager, 'wrapWithSandboxArgv', async () => {
    entered.resolve();
    await release.promise;
    throw new Error('aborted wrapper');
  });
  const rejected = assert.rejects(adapter.executeNative(request), /SRT operation failed/);
  await entered.promise;
  const result = await adapter.cancel({ execution_id: request.execution_id, deadline_utc: new Date().toISOString() });
  assert.equal(result.confirmed, false);
  assert.equal(result.cleanup.state, 'unknown');
  const closing = close();
  try {
    await new Promise(r => setImmediate(r));
    assert.equal(resets, 0);
  } finally { release.resolve(); await rejected; await closing; }
  assert.equal(resets, 1);
  assert.equal((await closing).state, 'unknown');
});

test('prepare completing during close cannot publish a ready session', async t => {
  const { adapter, policy, close } = await fixture(t);
  const entered = deferred(), release = deferred();
  // Only tests the prepare/close ordering; no supplied proof is accepted over RPC.
  adapter.capabilities.verification = Object.fromEntries(['read_isolation', 'write_isolation',
    'direct_network_isolation', 'socket_isolation', 'process_cleanup'].map(name =>
    [name, { status: 'verified', evidence_refs: ['lifecycle-double'] }]));
  t.mock.method(SandboxManager, 'initialize', async () => { entered.resolve(); await release.promise; });
  t.mock.method(SandboxManager, 'reset', async () => {});
  const rejected = assert.rejects(adapter.prepare({ owner: adapter.owner, policy, policy_hash: canonicalHash(policy) }), /closed/);
  await entered.promise;
  const closing = close();
  release.resolve();
  await rejected;
  assert.equal((await closing).state, 'unknown');
  assert.equal(adapter.preparation, undefined);
});

test('stalled initialization closes as unknown without racing reset', { timeout: 6000 }, async t => {
  const { adapter, policy, close } = await fixture(t);
  const entered = deferred(), release = deferred();
  let resets = 0;
  t.mock.method(SandboxManager, 'initialize', async () => { entered.resolve(); await release.promise; });
  t.mock.method(SandboxManager, 'reset', async () => { resets++; });
  const initializing = adapter.initializeNative(policy);
  await entered.promise;
  try {
    assert.equal((await close()).state, 'unknown');
    assert.equal(resets, 0);
    await assert.rejects(adapter.initializeNative(policy), /closed/);
  } finally { release.resolve(); await initializing; }
});

test('stalled aborted launch closes as unknown and retains its owned payload', { timeout: 6000 }, async t => {
  const { adapter, policy, request, close } = await fixture(t);
  t.mock.method(SandboxManager, 'initialize', async () => {});
  await adapter.initializeNative(policy);
  const entered = deferred(), release = deferred();
  let resets = 0;
  t.mock.method(SandboxManager, 'reset', async () => { resets++; });
  t.mock.method(SandboxManager, 'wrapWithSandboxArgv', async () => {
    entered.resolve(); await release.promise; throw new Error('aborted wrapper');
  });
  const rejected = assert.rejects(adapter.executeNative(request), /SRT operation failed/);
  await entered.promise;
  await adapter.cancel({ execution_id: request.execution_id, deadline_utc: new Date().toISOString() });
  try {
    assert.equal((await close()).state, 'unknown');
    assert.equal(resets, 0);
    for (const path of adapter.launchFiles) await access(path);
    assert.equal(adapter.executions.get(request.execution_id).child, undefined);
  } finally { release.resolve(); await rejected; }
});
