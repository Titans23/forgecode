/** Trusted development probe exercises real Main crypto and private Engine injection. */
import { randomBytes, randomUUID } from 'node:crypto';
import type { BrowserWindow } from 'electron';
import type { EngineSupervisor } from './supervisor.js';
import type { MainConnections } from './connections.js';

export async function probeCredentials(window: BrowserWindow, engine: EngineSupervisor, main: MainConnections) {
  const checks: Array<{ id: string; status: string }> = [];
  const check = (id: string, passed: boolean) => checks.push({ id, status: passed ? 'pass' : 'fail' });
  const action = () => 'act-' + randomUUID();
  const desired = { connection_id: 'conn-' + randomUUID(), expected_revision: 0, provider: 'anthropic',
    base_url: 'https://credential-probe.invalid', requested_model: 'synthetic-no-model-request' };
  const grant = await engine.call('connection.prepare_set', desired);
  const row = await engine.call('connection.set', { ...desired, client_action_id: action(), confirmation_token: grant.confirmation_token });
  const binding = { connection_id: row.connection_id, revision: row.revision, provider: row.provider, base_url: row.base_url };
  const key = 'synthetic-' + randomBytes(32).toString('hex');
  try {
    const saved = await main.broker.save(binding, key);
    check('actual-credential-storage', saved.protection.state === 'ready' &&
      (process.platform !== 'win32' || saved.persisted && saved.protection.backend === 'dpapi'));
    await engine.call('credentials.inject', { client_action_id: action(), connection_id: row.connection_id, expected_revision: 1, credential: key });
    let publicPage = await window.webContents.executeJavaScript('window.forgeDesktop.connections()');
    check('renderer-metadata-no-credential-readback', !JSON.stringify(publicPage).includes(key) &&
      publicPage.items.find((value: any) => value.connection_id === row.connection_id)?.credential_present === true);
    await window.webContents.executeJavaScript(`window.forgeDesktop.lockConnection(${JSON.stringify(row.connection_id)})`);
    publicPage = await window.webContents.executeJavaScript('window.forgeDesktop.connections()');
    const locked = publicPage.items.find((value: any) => value.connection_id === row.connection_id);
    check('named-session-lock-revokes-engine-key', locked?.locked === true && locked.credential_present === false && await main.broker.resolve(binding) === null);
    const changed = { ...desired, expected_revision: 1, base_url: 'https://changed-probe.invalid' };
    const changedGrant = await engine.call('connection.prepare_set', changed);
    const next = await engine.call('connection.set', { ...changed, client_action_id: action(), confirmation_token: changedGrant.confirmation_token });
    check('endpoint-change-requires-new-key', next.credential_present === false &&
      await main.broker.resolve({ ...binding, revision: next.revision, base_url: next.base_url }) === null);
  } finally {
    const page = await engine.call('connection.list', { limit: 100 });
    const actual = page.items.find((value: any) => value.connection_id === row.connection_id);
    if (actual) await engine.call('connection.delete', { client_action_id: action(), connection_id: actual.connection_id, expected_revision: actual.revision });
    await main.broker.remove(binding);
  }
  check('credential-deletion-revokes-readback', await main.broker.resolve(binding) === null);
  return checks;
}
