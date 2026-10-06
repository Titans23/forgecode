import { app, BrowserWindow, dialog, ipcMain, protocol, session } from 'electron';
import squirrel from 'electron-squirrel-startup';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { resolve, isAbsolute } from 'node:path';
import { randomUUID } from 'node:crypto';
import { validate } from '@forgecode/contracts';
import { loadDevelopmentEngine, loadInstalledEngine, uiAsset } from './assets.js';
import { EngineSupervisor } from './supervisor.js';
import { ownedTcpListeners } from './listeners.js';
import { assertSender, empty, businessId, captureSender } from './ipc.js';
import { NativeApprovals } from './approvals.js';
import { probeSecurity } from './security_probe.js';
import { runCredentialWorker } from './credential_worker.js';
import { CredentialCrypto, credentialEnvironment } from './credential_crypto.js';
import { CredentialBroker } from './credential_broker.js';
import { MainConnections } from './connections.js';
import { probeCredentials } from './credential_probe.js';

protocol.registerSchemesAsPrivileged([{ scheme: 'forge-app', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);

const installation = process.argv.some(arg => ['--squirrel-install', '--squirrel-updated', '--squirrel-uninstall', '--squirrel-obsolete'].includes(arg));
let window: BrowserWindow | null = null;
let engine: EngineSupervisor | null = null;
let connections: MainConnections | null = null;
let stopping = false;
let finalClose = false;
let failure: string | null = null;
let demo: any = null;
let currentSession: string | null = null;
let smoke: any = null;
let packagedReport: string | null = null;
const execute = promisify(execFile);

function argument(name: string): string | undefined {
  const index = process.argv.indexOf(name);
  return index < 0 ? undefined : process.argv[index + 1];
}

function configureDevelopment() {
  const packaged = argument('--desktop-package-smoke-report');
  if (packaged) {
    if (!app.isPackaged || !isAbsolute(packaged)) throw new Error('Packaged inspection requires an installed app and an absolute report path');
    packagedReport = packaged;
    app.setPath('userData', resolve(packaged, '../desktop-data'));
  }
  const path = argument('--desktop-smoke-config');
  if (!path) return;
  if (app.isPackaged || !isAbsolute(path)) throw new Error('Development smoke requires an absolute trusted config');
  smoke = JSON.parse(readFileSync(path, 'utf8'));
  if (smoke.origin !== 'scripted' || !isAbsolute(smoke.directory) || !isAbsolute(smoke.fixture) || !isAbsolute(smoke.output)) throw new Error('Invalid development smoke config');
  demo = smoke.params;
  app.setPath('userData', resolve(smoke.directory, 'desktop-data'));
}

function sender(event: Electron.IpcMainInvokeEvent) {
  assertSender(window, event);
}
function onlyId(value: unknown, key: string, prefix: string): string {
  if (prefix === 'conn') {
    validate('credentials.clear.request', { ...(value as object), client_action_id: 'act-' + randomUUID(), expected_revision: 1 });
    if (!value || Object.keys(value as object).length !== 1) throw new Error('Invalid connection payload');
    return (value as Record<string, string>)[key];
  }
  const schema = prefix === 'ses' ? 'sandbox.cleanup_status.request' : prefix === 'approval' ? 'approval.get.request' : 'workspace.inspect.request';
  if (prefix === 'turn') {
    validate('session.cancel_turn.request', { ...(value as object), client_action_id: 'act-' + randomUUID(), reason: 'UI cancellation' });
    if (Object.keys(value as object).length !== 1) throw new Error('Invalid cancellation payload');
    return (value as Record<string, string>)[key];
  }
  return businessId(value, key, schema);
}
function live(): EngineSupervisor { if (!engine || stopping) throw new Error('Engine is unavailable'); return engine; }
function connectionManager(): MainConnections { live(); if (!connections) throw new Error('Connection storage is unavailable'); return connections; }

async function close() {
  if (stopping) return;
  stopping = true;
  connections?.broker.close();
  const report = engine ? await engine.shutdown('cancel') : { state: 'unknown', cleanup_state: 'unknown', reason: failure ?? 'Engine unavailable' };
  const data = app.getPath('userData');
  try {
    await mkdir(data, { recursive: true });
    await writeFile(resolve(data, 'last-shutdown.json'), JSON.stringify({ ...report, time: new Date().toISOString() }) + '\n');
  } catch { console.error('Shutdown observation could not be persisted; recovery needs reconciliation.'); }
  finally { finalClose = true; app.quit(); }
}

async function createWindow() {
  window = new BrowserWindow({ width: 1240, height: 830, minWidth: 900, minHeight: 650, title: 'ForgeCode',
    backgroundColor: '#11151b', show: false, webPreferences: { preload: resolve(__dirname, 'preload.js'),
      contextIsolation: true, sandbox: true, webSecurity: true, nodeIntegration: false,
      devTools: !app.isPackaged, partition: 'forge-desktop' } });
  engine?.attachRenderer();
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => { if (url !== 'forge-app://ui/index.html') event.preventDefault(); });
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.webContents.on('render-process-gone', () => {
    engine?.detachRenderer();
    if (!stopping && window && !window.isDestroyed()) {
      void window.loadURL('forge-app://ui/index.html').then(() => engine?.attachRenderer()).catch(() => { failure = '界面恢复失败；Engine 状态需要核对。'; });
    }
  });
  window.on('closed', () => { engine?.detachRenderer(); window = null; });
  window.on('close', event => {
    if (finalClose) return;
    event.preventDefault();
    void (async () => {
      if (stopping) return;
      const health = engine?.state === 'ready' ? await engine.call('system.health', {}).catch(() => null) : null;
      if (health?.active_work_items > 0 && !smoke) {
        const result = await dialog.showMessageBox(window!, { type: 'question', title: '任务正在运行',
          message: '关闭 ForgeCode 会取消当前任务。', buttons: ['保持窗口', '取消任务并退出'], defaultId: 0, cancelId: 0, noLink: true });
        if (result.response !== 1) return;
      }
      await close();
    })().catch(() => { failure = '退出未确认；下次启动需要核对运行记录。'; });
  });
  await window.loadURL('forge-app://ui/index.html');
  window.show();
}

async function runSmoke() {
  if (!smoke || !window || !engine) return;
  const checks: any[] = [];
  const check = (id: string, passed: boolean, details: any = {}) => { checks.push({ id, status: passed ? 'pass' : 'fail', details }); };
  const pid = engine.pid;
  try {
    const status = await window.webContents.executeJavaScript('window.forgeDesktop.status()');
    check('real-engine-handshake', status.engine_state === 'ready' && status.mode === 'offline-demo');
    checks.push(...await probeSecurity(window, resolve(__dirname, 'preload.js')));
    checks.push(...await probeCredentials(window, engine, connectionManager()));
    const accepted = await window.webContents.executeJavaScript('window.forgeDesktop.startDemo()');
    const expires = Date.now() + 45000;
    let snapshot;
    while (Date.now() < expires) {
      snapshot = await engine.call('session.get', { session_id: currentSession });
      if (snapshot.turns[0]?.state === 'finished') break;
      await new Promise(r => setTimeout(r, 50));
    }
    check('actual-demo-turn', snapshot?.turns[0]?.outcome === 'completed' && snapshot.turns[0].turn_id === accepted.turn_id);
    const reloaded = new Promise<void>(resolve => window!.webContents.once('did-finish-load', () => resolve()));
    window.webContents.reload();
    await reloaded;
    const after = await window.webContents.executeJavaScript('window.forgeDesktop.status()');
    check('reload-keeps-engine-turn', engine.pid === pid && after.session_id === currentSession);
    const security = await window.webContents.executeJavaScript('window.forgeDesktop.security()');
    check('secure-window', security.contextIsolated === true && security.sandboxed === true, security);
    check('preload-no-raw-node', await window.webContents.executeJavaScript('typeof window.require === "undefined" && typeof window.forgeDesktop.invoke === "undefined"'));
    const second = await execute(process.execPath, [app.getAppPath(), '--desktop-smoke-config', argument('--desktop-smoke-config')!], { timeout: 10000, windowsHide: true });
    check('same-profile-single-instance', engine.pid === pid && !second.stderr.includes('Engine could not start'));
    await execute(process.execPath, [app.getAppPath(), '--squirrel-obsolete', '--desktop-smoke-config', resolve(smoke.output, 'does-not-exist.json')], { timeout: 10000, windowsHide: true });
    check('installation-event-skips-engine-entry', engine.pid === pid && engine.state === 'ready');
    const crashRecovered = new Promise<void>(resolve => window!.webContents.once('did-finish-load', () => resolve()));
    window.webContents.forcefullyCrashRenderer();
    await crashRecovered;
    const recovered = await window.webContents.executeJavaScript('window.forgeDesktop.status()');
    check('renderer-crash-keeps-engine', engine.pid === pid && recovered.session_id === currentSession && engine.state === 'ready');
    check('no-owned-tcp-listener', await ownedTcpListeners([process.pid, pid!]) === 0);
    await new Promise(r => setTimeout(r, 350));
    const png = (await window.webContents.capturePage()).toPNG();
    await writeFile(resolve(smoke.output, 'desktop.png'), png);
    const report = await engine.shutdown('cancel');
    check('owned-shutdown', report.state === 'confirmed' && report.cleanup_state === 'complete', report);
    await writeFile(resolve(smoke.output, 'desktop-report.json'), JSON.stringify({ status: checks.every(x => x.status === 'pass') ? 'pass' : 'fail',
      scope: 'development-desktop', platform: process.platform, eligible_for_native_pass: false, checks, screenshot: 'desktop.png' }, null, 2));
  } catch (error) {
    await writeFile(resolve(smoke.output, 'desktop-report.json'), JSON.stringify({ status: 'fail', checks, reason: (error as Error).message }));
  } finally { await close(); }
}

async function runPackagedInspection() {
  if (!packagedReport || !window || !engine) return;
  const checks: any[] = [];
  const check = (id: string, passed: boolean) => checks.push({ id, status: passed ? 'pass' : 'fail' });
  try {
    const status = await window.webContents.executeJavaScript('window.forgeDesktop.status()');
    check('installed-engine-handshake', status.engine_state === 'ready' && status.mode === 'desktop' &&
      engine.hello.capabilities.features.includes('provider-model') && !engine.hello.capabilities.features.includes('scripted-model'));
    const security = await window.webContents.executeJavaScript('window.forgeDesktop.security()');
    check('installed-window-sandbox', security.contextIsolated === true && security.sandboxed === true);
    check('installed-no-owned-listener', await ownedTcpListeners([process.pid, engine.pid!]) === 0);
    const report = await engine.shutdown('cancel');
    check('installed-owned-shutdown', report.state === 'confirmed' && report.cleanup_state === 'complete');
    await writeFile(packagedReport, JSON.stringify({ status: checks.every(x => x.status === 'pass') ? 'pass' : 'fail',
      scope: 'installed-readonly-preview', eligible_for_native_pass: false, checks }, null, 2));
  } catch { await writeFile(packagedReport, JSON.stringify({ status: 'fail', reason: 'Installed inspection failed', checks })); }
  finally { await close(); }
}

async function ready() {
  const root = app.isPackaged ? process.resourcesPath : resolve(app.getAppPath(), '../..');
  const uiRoot = app.getAppPath();
  const uiManifest = JSON.parse(await readFile(resolve(app.isPackaged ? process.resourcesPath : app.getAppPath(), 'ui-assets.json'), 'utf8'));
  const browserSession = session.fromPartition('forge-desktop');
  browserSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  browserSession.setPermissionCheckHandler(() => false);
  browserSession.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !details.url.startsWith('forge-app://ui/') }));
  browserSession.protocol.handle('forge-app', async request => {
    try {
      const asset = await uiAsset(uiRoot, uiManifest, request.url);
      return new Response(await readFile(asset.path), { headers: { 'content-type': asset.contentType,
        'content-security-policy': "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'", 'x-content-type-options': 'nosniff' } });
    } catch { return new Response('Resource unavailable', { status: 404 }); }
  });
  try {
    const launch = app.isPackaged ? await loadInstalledEngine(root, resolve(app.getPath('userData'), 'engine')) :
      await loadDevelopmentEngine(root, { dataDir: smoke ? resolve(smoke.directory, 'data') : resolve(app.getPath('userData'), 'engine'),
        profile: smoke ? 'test' : 'desktop', ...(smoke ? { fixture: smoke.fixture } : {}) });
    engine = new EngineSupervisor(launch);
    await engine.start();
    const dataDir = launch.arguments[launch.arguments.indexOf('--data-dir') + 1];
    if (!isAbsolute(dataDir)) throw new Error('Credential data root must be fixed by Main');
    const crypto = new CredentialCrypto({ executable: process.execPath,
      arguments: [...(app.isPackaged ? [] : [app.getAppPath()]), '--credential-worker', resolve(dataDir, 'credential-runtime')],
      cwd: launch.cwd, environment: credentialEnvironment(process.env) });
    connections = new MainConnections(live, new CredentialBroker(resolve(dataDir, 'credentials'), crypto));
    await connections.restore();
  } catch { failure = 'Engine 启动或组件校验失败；请查看诊断。'; }

  ipcMain.handle('forge:status', (event, value) => { sender(event); empty(value); return {
    engine_state: engine?.state ?? 'engine_lost', readiness: engine?.hello?.readiness ?? null,
    mode: smoke ? 'offline-demo' : 'desktop', session_id: currentSession, failure }; });
  ipcMain.handle('forge:projects', (event, value) => { sender(event); empty(value); return live().call('workspace.list', { limit: 100 }); });
  ipcMain.handle('forge:session', (event, value) => { sender(event); const session_id = onlyId(value, 'session_id', 'ses'); return live().call('session.get', { session_id }); });
  ipcMain.handle('forge:events', (event, value) => { sender(event); empty(value); return live().events(); });
  const approvals = new NativeApprovals(live);
  ipcMain.handle('forge:select-project', (event, value) => { sender(event); empty(value); return approvals.selectDirectory(captureSender(() => window, event)); });
  ipcMain.handle('forge:authorize-workspace', (event, value) => { sender(event); const id = onlyId(value, 'workspace_id', 'ws'); return approvals.authorizeWorkspace(id, captureSender(() => window, event)); });
  ipcMain.handle('forge:request-approval', (event, value) => { sender(event); const id = onlyId(value, 'approval_id', 'approval'); return approvals.request(id, captureSender(() => window, event)); });
  ipcMain.handle('forge:approvals', (event, value) => { sender(event); empty(value); return live().call('approval.list', { scope: { kind: 'all' }, limit: 100 }); });
  ipcMain.handle('forge:connections', (event, value) => { sender(event); empty(value); return connectionManager().list(); });
  ipcMain.handle('forge:save-connection', (event, value) => { sender(event); return connectionManager().save(value, captureSender(() => window, event)); });
  for (const [channel, operation] of [['delete', 'remove'], ['lock', 'lock'], ['unlock', 'unlock'], ['test', 'test']] as const) {
    ipcMain.handle('forge:' + channel + '-connection', (event, value) => {
      sender(event); const id = onlyId(value, 'connection_id', 'conn');
      return connectionManager()[operation](id, captureSender(() => window, event));
    });
  }
  ipcMain.handle('forge:cancel-turn', (event, value) => { sender(event); const turn_id = onlyId(value, 'turn_id', 'turn'); return live().call('session.cancel_turn', {
    turn_id, client_action_id: 'act-' + randomUUID(), reason: 'Desktop user cancelled' }); });
  ipcMain.handle('forge:start-demo', async (event, value) => {
    sender(event); empty(value);
    if (!demo || app.isPackaged) throw new Error('Offline demo was not enabled by trusted development launch');
    if (currentSession) throw new Error('Demo already started; query the existing turn');
    validate('session.create.request', demo);
    const created = await live().call('session.create', demo);
    currentSession = created.session_id;
    await live().call('events.subscribe', { scope: { kind: 'session', id: currentSession } });
    const turn = { ...demo, client_action_id: 'act-' + randomUUID(), session_id: currentSession,
      input: [{ type: 'text', text: 'Fix integer addition in calculator.py. Preserve the tests; run unittest before and after the repair.' }] };
    delete turn.workspace_id;
    return live().call('session.start_turn', turn);
  });
  await createWindow();
  if (smoke) await runSmoke();
  if (packagedReport) {
    if (!engine || engine.state !== 'ready') {
      await writeFile(packagedReport, JSON.stringify({ status: 'fail', reason: 'Installed Engine initialization was not confirmed', checks: [] }));
      await close();
    } else await runPackagedInspection();
  }
}

if (argument('--credential-worker')) void runCredentialWorker(argument('--credential-worker')!);
else if (installation || squirrel) app.quit();
else {
  configureDevelopment();
  if (!app.requestSingleInstanceLock()) app.quit();
  else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.focus(); } });
  app.on('before-quit', event => { if (!finalClose) { event.preventDefault(); if (window) window.close(); else void close(); } });
  app.on('window-all-closed', () => { if (!stopping) void close(); });
  app.whenReady().then(ready).catch(() => { failure = '客户端资源初始化失败'; finalClose = true; app.exit(2); });
  }
}
