/** Named Main operations confirm endpoints and never return plaintext credentials. */
import { dialog, type BrowserWindow } from 'electron';
import { randomUUID } from 'node:crypto';
import { validate } from '@forgecode/contracts';
import { CredentialBroker, type CredentialBinding } from './credential_broker.js';
import type { EngineSupervisor } from './supervisor.js';
import { nativeOperation } from './ipc.js';

export type ConnectionInput = { connection_id?: string; expected_revision?: number; provider: string;
  base_url: string; requested_model: string; credential: string };
function binding(value: any): CredentialBinding {
  return { connection_id: value.connection_id, revision: value.revision, provider: value.provider, base_url: value.base_url };
}
export class MainConnections {
  private locked = new Set<string>();
  constructor(private engine: () => EngineSupervisor, readonly broker: CredentialBroker) {}
  async list() {
    const page = await this.engine().call('connection.list', { limit: 100 });
    return { ...page, items: page.items.map((row: any) => ({ ...row, locked: this.locked.has(row.connection_id) })), protection: this.broker.status() };
  }
  private async get(connectionId: string) {
    let cursor: string | undefined;
    for (let page = 0; page < 10; page++) {
      const result = await this.engine().call('connection.list', { limit: 100, ...(cursor ? { cursor } : {}) });
      const found = result.items.find((row: any) => row.connection_id === connectionId);
      if (found) return found;
      if (!result.next_cursor) break;
      cursor = result.next_cursor;
    }
    throw new Error('Connection is unavailable');
  }
  async restore() {
    await this.broker.probe();
    let cursor: string | undefined;
    for (let page = 0; page < 10; page++) {
      const result = await this.engine().call('connection.list', { limit: 100, ...(cursor ? { cursor } : {}) });
      for (const row of result.items) {
        const credential = await this.broker.resolve(binding(row));
        if (credential) await this.inject(row, credential);
      }
      if (!result.next_cursor) return;
      cursor = result.next_cursor;
    }
  }
  private inject(value: any, credential: string) {
    return this.engine().call('credentials.inject', { client_action_id: 'act-' + randomUUID(), connection_id: value.connection_id,
      expected_revision: value.revision, credential });
  }
  save(value: ConnectionInput, current: () => BrowserWindow) {
    return nativeOperation(async () => {
      if (!value || typeof value !== 'object' || Object.keys(value).some(key =>
        !['connection_id', 'expected_revision', 'provider', 'base_url', 'requested_model', 'credential'].includes(key)) ||
        typeof value.credential !== 'string' || value.credential.length > 0 && !value.credential.trim() ||
        Buffer.byteLength(value.credential) > 16384 || /[\r\n\0]/.test(value.credential)) throw new Error('Invalid connection form');
      const desired = { connection_id: value.connection_id ?? 'conn-' + randomUUID(), expected_revision: value.expected_revision ?? 0,
        provider: value.provider, base_url: value.base_url, requested_model: value.requested_model };
      validate('connection.prepare_set.request', desired);
      // Engine validates the proposed URL before any native grant. This nonce is
      // discarded; a fresh one is obtained after the user decision.
      await this.engine().call('connection.prepare_set', desired);
      await this.broker.probe();
      const previous = desired.expected_revision ? await this.get(desired.connection_id) : null;
      const choice = await dialog.showMessageBox(current(), { type: 'warning', title: '确认模型连接',
        message: '保存这个模型连接？', detail: `${previous ? '原地址：' + previous.base_url + '\n' : ''}新地址：${desired.base_url}\n服务 origin：${new URL(desired.base_url).origin}\n模型：${desired.requested_model}\n` +
          (this.broker.status().mode === 'os_protected' && this.broker.status().state === 'ready' ? '凭证使用当前系统保护。' : '凭证仅保留在本次应用内存中；系统保护可能暂不可用。') + '\n留空会移除旧凭证；保存不会自动发送网络请求。',
        buttons: ['取消', '保存'], defaultId: 0, cancelId: 0, noLink: true });
      current();
      if (choice.response !== 1) return { cancelled: true };
      const engine = this.engine();
      const confirmation = await engine.call('connection.prepare_set', desired);
      current();
      const saved = await engine.call('connection.set', { ...desired, client_action_id: 'act-' + randomUUID(), confirmation_token: confirmation.confirmation_token });
      await this.broker.remove(binding(saved));
      this.locked.delete(saved.connection_id);
      if (value.credential) {
        await this.broker.save(binding(saved), value.credential);
        current();
        await this.inject(saved, value.credential);
      }
      return { connection_id: saved.connection_id, protection: this.broker.status() };
    });
  }
  remove(connectionId: string, current: () => BrowserWindow) {
    return nativeOperation(async () => {
      const row = await this.get(connectionId);
      const choice = await dialog.showMessageBox(current(), { type: 'question', title: '删除模型连接', message: '删除连接和本地凭证？',
        detail: row.base_url, buttons: ['取消', '删除'], defaultId: 0, cancelId: 0, noLink: true });
      current();
      if (choice.response !== 1) return { cancelled: true };
      await this.engine().call('connection.delete', { connection_id: connectionId, expected_revision: row.revision, client_action_id: 'act-' + randomUUID() });
      await this.broker.remove(binding(row)); this.locked.delete(connectionId);
      return { deleted: true };
    });
  }
  lock(connectionId: string, current: () => BrowserWindow) {
    return nativeOperation(async () => {
      const row = await this.get(connectionId); current();
      await this.engine().call('credentials.clear', { connection_id: connectionId, expected_revision: row.revision, client_action_id: 'act-' + randomUUID() });
      this.broker.lock(connectionId); this.locked.add(connectionId);
      return { locked: true };
    });
  }
  unlock(connectionId: string, current: () => BrowserWindow) {
    return nativeOperation(async () => {
      const row = await this.get(connectionId);
      const choice = await dialog.showMessageBox(current(), { type: 'question', title: '解锁本次会话', message: '重新启用这个连接的系统保护凭证？',
        detail: row.base_url, buttons: ['保持锁定', '解锁'], defaultId: 0, cancelId: 0, noLink: true });
      current();
      if (choice.response !== 1) return { locked: true };
      const fresh = await this.get(connectionId);
      if (fresh.revision !== row.revision) throw new Error('Connection changed; confirm again');
      const credential = await this.broker.unlock(binding(row));
      if (!credential) throw new Error('凭证暂不可用，或内存模式需要重新输入。');
      current();
      await this.inject(row, credential); this.locked.delete(connectionId);
      return { locked: false };
    });
  }
  test(connectionId: string, current: () => BrowserWindow) {
    return nativeOperation(async () => {
      const row = await this.get(connectionId);
      const choice = await dialog.showMessageBox(current(), { type: 'question', title: '测试模型连接', message: '向这个地址发送模型目录请求？',
        detail: row.base_url, buttons: ['取消', '发送请求'], defaultId: 0, cancelId: 0, noLink: true });
      current();
      if (choice.response !== 1) return { cancelled: true };
      const grant = await this.engine().call('connection.prepare_test', { connection_id: connectionId, expected_revision: row.revision });
      current();
      return this.engine().call('connection.test', { connection_id: connectionId, expected_revision: row.revision,
        client_action_id: 'act-' + randomUUID(), confirmation_token: grant.confirmation_token });
    });
  }
}
