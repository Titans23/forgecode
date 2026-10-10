/** Compatibility for retired strict execution; no replacement OS evidence is fabricated. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { SrtAdapter } from '../../../sandbox_bridge/dist/srt-adapter.js';
import { canonicalHash, validate } from '../../../packages/contracts/dist/index.js';

test('strict cannot initialize or execute even with a valid historical policy', async t => {
  const root = await mkdtemp(resolve(tmpdir(), 'forge-strict-retired-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const owner = { engine_epoch: 'epoch-' + randomUUID(), sandbox_session_id: 'sandbox-' + randomUUID(), execution_id: null };
  const adapter = new SrtAdapter(root, root, owner, {}, () => assert.fail('No task output allowed'));
  const workspace = 'ws-' + randomUUID();
  const report = await adapter.probe({ workspace_path: root, workspace_id: workspace });
  assert.equal(report.readiness, 'unavailable');
  assert.equal(report.backend, 'strict-unavailable');
  assert.ok(Object.values(report.verification).every(p => p.status === 'unsupported'));
  const policy = JSON.parse(await readFile('contracts/v1/examples/sandbox-policy.valid.json'));
  policy.workspace_id = workspace;
  await assert.rejects(adapter.prepare({ owner, policy, policy_hash: canonicalHash(policy) }), e => e.kind === 'SANDBOX_UNAVAILABLE');
  for (const method of ['execute', 'status', 'cancel']) await assert.rejects(adapter[method]({}), e => e.kind === 'SANDBOX_UNAVAILABLE');
  await assert.rejects(adapter.close({ sandbox_session_id: 'foreign' }), e => e.kind === 'POLICY_DENIED');
  const results = await Promise.all([adapter.close(owner), adapter.close(owner)]);
  assert.deepEqual(results[0], results[1]);
  assert.equal(results[0].state, 'clean');
  validate('cleanup-report', results[0]);
  assert.deepEqual(results[0].diagnostic_refs, []);
  await assert.rejects(adapter.probe({ workspace_path: root, workspace_id: workspace }));
});

test('retired strict preserves workspace identity and policy ownership checks', async t => {
  const root = await mkdtemp(resolve(tmpdir(), 'forge-strict-binding-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(resolve(root, 'other'));
  const owner = { sandbox_session_id: 'sandbox-' + randomUUID() };
  const adapter = new SrtAdapter(root, root, owner, {}, () => false);
  await adapter.probe({ workspace_path: root, workspace_id: 'original' });
  await assert.rejects(adapter.probe({ workspace_path: resolve(root, 'other'), workspace_id: 'original' }), e => e.kind === 'POLICY_DENIED');
  await assert.rejects(adapter.prepare({ owner: {}, policy: {}, policy_hash: 'bad' }), e => e.kind === 'POLICY_DENIED');
});
