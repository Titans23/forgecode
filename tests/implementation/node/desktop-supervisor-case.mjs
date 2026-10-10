import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { loadDevelopmentEngine } from '../../../apps/desktop/dist/main/assets.js';
import { EngineSupervisor } from '../../../apps/desktop/dist/main/supervisor.js';
import { ownedTcpListeners } from '../../../apps/desktop/dist/main/listeners.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const input = JSON.parse(await readFile(process.argv[2], 'utf8'));
const launch = await loadDevelopmentEngine(root, { dataDir: resolve(input.directory, 'data'),
  profile: 'test', fixture: input.fixture });
const engine = new EngineSupervisor(input.mode === 'incompatible' ? { ...launch, manifestHash: '0'.repeat(64) } : launch);
try {
  if (input.mode === 'incompatible') {
    await assert.rejects(engine.start(), /Engine RPC failed/);
    assert.equal(engine.state, 'incompatible');
    await assert.rejects(engine.call('session.start_turn', input.turn), /handshake/);
    process.stdout.write(JSON.stringify({ incompatible_disables_turn: true }));
  } else {
  await engine.start();
  const pid = engine.pid;
  assert.equal(engine.state, 'ready');
  if (input.mode === 'crash') {
    const exited = new Promise(resolve => engine.child.once('close', resolve));
    engine.child.kill(); await exited;
    assert.equal(engine.state, 'engine_lost');
    assert.equal(engine.pid, pid);
    await assert.rejects(engine.call('session.start_turn', input.turn), /handshake/);
    await assert.rejects(engine.start(), /already owned/);
    process.stdout.write(JSON.stringify({ lost_without_respawn: true }));
  } else {
  const accepted = await engine.call('session.start_turn', input.turn);
  if (input.mode === 'active-close') {
    const report = await engine.shutdown('cancel');
    assert.equal(report.state, 'confirmed', JSON.stringify(report));
    assert.equal(report.cleanup_state, 'complete', JSON.stringify(report));
    process.stdout.write(JSON.stringify({ cancelled_owned_active_turn: true }));
  } else {
  let snapshot;
  const deadline = Date.now() + 15000;
  do {
    snapshot = await engine.call('session.get', { session_id: input.turn.session_id });
    if (snapshot.turns[0]?.state === 'finished') break;
    await new Promise(r => setTimeout(r, 20));
  } while (Date.now() < deadline);
  assert.equal(snapshot.turns[0].outcome, 'completed');
  // Reattaching a UI only queries the same supervisor; it never starts a child.
  engine.attachRenderer(); engine.detachRenderer(); engine.attachRenderer();
  assert.equal(engine.pid, pid);
  const retry = await engine.call('session.start_turn', input.turn);
  assert.equal(retry.turn_id, accepted.turn_id);
  assert.equal(retry.reused_existing_action, true);
  const cleanup = await engine.call('sandbox.cleanup_status', { session_id: input.turn.session_id });
  assert.equal(cleanup.state, 'complete', JSON.stringify(cleanup));
  const listeners = await ownedTcpListeners([process.pid, pid]);
  assert.equal(listeners, 0);
  const close = await engine.shutdown('cancel');
  assert.equal(close.state, 'confirmed');
  assert.equal(close.cleanup_state, 'complete', JSON.stringify(close));
  process.stdout.write(JSON.stringify({ handshake: true, same_engine_after_reload: true,
    real_completed_turn: true, closed_owned_engine: engine.state === 'closed', no_http_listener: listeners === 0 }));
  }
  }
  }
} finally {
  if (engine.state !== 'closed') await engine.shutdown('cancel');
}
