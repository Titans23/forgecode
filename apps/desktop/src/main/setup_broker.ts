/** Main-only setup entry. Renderer receives no path, environment, argv or administrator command API. */
import { BrowserWindow, dialog } from 'electron';
import { spawn } from 'node:child_process';
import { isAbsolute } from 'node:path';

type SetupAction = 'install' | 'repair' | 'diagnose';
type VerifiedRuntime = Readonly<{ node: string; entry: string; root: string; manifestHash: string;
  environment: Readonly<Record<string, string>> }>;

export function createSetupBroker(runtime: VerifiedRuntime) {
  if (![runtime.node, runtime.entry, runtime.root].every(isAbsolute) || !/^[a-f0-9]{64}$/.test(runtime.manifestHash)) {
    throw new Error('Setup requires Main-owned verified installed assets');
  }
  const { node, entry, root } = runtime;
  const environment = Object.fromEntries(Object.entries(runtime.environment).filter(([name]) =>
    ['PATH', 'SystemRoot', 'windir', 'ProgramFiles', 'HOME', 'TEMP', 'TMP', 'LANG'].includes(name)));
  let running = false;
  async function launch(action: SetupAction): Promise<any> {
    return new Promise((resolve, reject) => {
      const child = spawn(node, [entry, '--setup-action', action], {
        cwd: root, env: environment, shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
      const chunks: Buffer[] = [];
      let size = 0;
      const timer = setTimeout(() => { child.kill(); reject(new Error('Fixed setup timed out; installation status requires diagnosis')); }, 135000);
      child.stderr.on('data', () => { /* Diagnostics never become authoritative approval or output. */ });
      child.stdout.on('data', bytes => {
        size += bytes.length;
        if (size > 65536) { child.kill(); reject(new Error('Setup report exceeded its bound')); }
        else chunks.push(bytes);
      });
      child.once('error', reject);
      child.once('close', code => {
        clearTimeout(timer);
        try {
          if (code !== 0 && code !== 2) throw new Error('Fixed setup helper failed');
          const result = JSON.parse(Buffer.concat(chunks).toString('utf8'));
          if (result.status !== 'blocked' && result.status !== 'pass') throw new Error('Invalid setup report');
          resolve(result);
        } catch (error) { reject(error); }
      });
    });
  }
  return async function setup(action: SetupAction, window: BrowserWindow, assertCurrent: () => void = () => {}): Promise<any> {
    if (!['install', 'repair', 'diagnose'].includes(action)) throw new Error('Unknown fixed setup action');
    if (running) return { status: 'blocked', reason: 'setup_already_running', administrator_invoked: false };
    if (window.isDestroyed() || window.webContents.isDestroyed()) throw new Error('Main window is unavailable');
    running = true;
    try {
      assertCurrent();
      if (action !== 'install') return await launch(action);
      const diagnosis = await launch('diagnose');
      assertCurrent();
      if (!diagnosis.native_status || diagnosis.fresh_install_allowed !== true) {
        return { ...diagnosis, reason: diagnosis.native_status ? 'existing_shared_setup; administrator reconciliation required' : diagnosis.reason };
      }
      const choice = await dialog.showMessageBox(window, { type: 'question', title: '安装原生沙盒',
        message: '允许为 ForgeCode 设置 Windows 原生沙盒吗？',
        detail: '此操作会创建低权限账户和网络限制，Windows 随后会请求管理员确认。普通任务无需管理员权限。',
        buttons: ['取消', '继续设置'], defaultId: 0, cancelId: 0, noLink: true });
      if (choice.response !== 1 || window.isDestroyed()) return { status: 'blocked', reason: 'setup_declined', administrator_invoked: false };
      assertCurrent();
      // The fixed child rechecks live setup immediately before UAC; no caller-supplied parameters.
      return await launch('install');
    } finally { running = false; }
  };
}
