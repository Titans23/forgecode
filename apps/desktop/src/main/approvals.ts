/** Only Main can select directories, display native confirmations and submit grants. */
import { dialog, type BrowserWindow } from 'electron';
import { randomBytes, randomUUID } from 'node:crypto';
import { isAbsolute } from 'node:path';
import type { EngineSupervisor } from './supervisor.js';

export class NativeApprovals {
  private active = false;
  constructor(private engine: () => EngineSupervisor) {}
  private async exclusive<T>(operation: () => Promise<T>): Promise<T> {
    if (this.active) throw new Error('A native authorization is already open');
    this.active = true;
    try { return await operation(); } finally { this.active = false; }
  }
  selectDirectory(current: () => BrowserWindow) {
    return this.exclusive(async () => {
      const selection = await dialog.showOpenDialog(current(), { title: '选择 ForgeCode 项目', properties: ['openDirectory'] });
      current();
      if (selection.canceled) return null;
      if (selection.filePaths.length !== 1 || !isAbsolute(selection.filePaths[0])) throw new Error('Native directory selection is invalid');
      const nonce = randomBytes(32).toString('hex');
      return this.engine().call('workspace.register', { client_action_id: 'act-' + randomUUID(), path: selection.filePaths[0], selection_nonce: nonce });
    });
  }
  authorizeWorkspace(workspaceId: string, current: () => BrowserWindow) {
    return this.exclusive(async () => {
      const engine = this.engine();
      const workspace = await engine.call('workspace.inspect', { workspace_id: workspaceId });
      const choice = await dialog.showMessageBox(current(), { type: 'warning', title: '授权项目执行',
        message: '允许 ForgeCode 在这个项目内执行任务？', detail: workspace.canonical_path,
        buttons: ['保持只读', '允许执行'], defaultId: 0, cancelId: 0, noLink: true });
      current();
      if (choice.response !== 1) return workspace;
      const fresh = await engine.call('workspace.inspect', { workspace_id: workspaceId });
      if (fresh.revision !== workspace.revision || fresh.canonical_path !== workspace.canonical_path) throw new Error('项目已变化，请重新授权。');
      current();
      const challenge = await engine.call('workspace.prepare_authorization', { workspace_id: workspaceId, expected_revision: fresh.revision });
      current();
      return engine.call('workspace.authorize', { client_action_id: 'act-' + randomUUID(), workspace_id: workspaceId,
        expected_revision: fresh.revision, binding_hash: challenge.binding_hash, confirmation_token: challenge.confirmation_token, allow: true });
    });
  }
  request(approvalId: string, current: () => BrowserWindow) {
    return this.exclusive(async () => {
      const engine = this.engine();
      const value = await engine.call('approval.get', { approval_id: approvalId });
      if (value.state !== 'pending' || Date.parse(value.expires_at_utc) <= Date.now()) throw new Error('审批已失效。');
      const detail = [`工具：${value.tool_name ?? '未知'}`, `风险：${value.risk ?? '未知'}`, `目录：${value.cwd}`,
        `路径：${value.path_delta.join(', ')}`, `网络：${value.network_delta.join(', ')}`, `参数 SHA256：${value.arguments_hash ?? '未提供'}`,
        `脚本 SHA256：${value.script_hash ?? '无'}`, value.reason ?? '', value.preview ?? ''].join('\n');
      const choice = await dialog.showMessageBox(current(), { type: 'warning', title: 'ForgeCode 请求一次授权',
        message: '确认以下操作？', detail: detail.slice(0, 12000), buttons: ['拒绝', '仅此次'], defaultId: 0, cancelId: 0, noLink: true });
      current();
      const fresh = await engine.call('approval.get', { approval_id: approvalId });
      if (fresh.state !== 'pending' || fresh.binding_hash !== value.binding_hash || Date.parse(fresh.expires_at_utc) <= Date.now()) throw new Error('审批内容或期限已变化。');
      current();
      const challenge = await engine.call('approval.prepare_decision', { approval_id: approvalId, binding_hash: value.binding_hash });
      current();
      return engine.call('approval.decide', { client_action_id: 'act-' + randomUUID(), approval_id: approvalId,
        binding_hash: value.binding_hash, confirmation_token: challenge.confirmation_token, decision: choice.response === 1 ? 'approve' : 'deny' });
    });
  }
}
