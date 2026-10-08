import { app, BrowserWindow, dialog, ipcMain, nativeTheme, powerMonitor, protocol, session } from 'electron';
import squirrel from 'electron-squirrel-startup';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { resolve, isAbsolute } from 'node:path';
import { randomUUID } from 'node:crypto';
import { validate } from '@forgecode/contracts';
import { loadDevelopmentEngine, loadInstalledEngine, loadSetupRuntime, uiAsset } from './assets.js';
import { EngineSupervisor } from './supervisor.js';
import { ownedTcpListeners } from './listeners.js';
import { assertSender, empty, businessId, captureSender, nativeOperation } from './ipc.js';
import { NativeApprovals } from './approvals.js';
import { NativeFileDialogs } from './dialogs.js';
import { probeSecurity } from './security_probe.js';
import { runCredentialWorker } from './credential_worker.js';
import { CredentialCrypto, credentialEnvironment } from './credential_crypto.js';
import { CredentialBroker } from './credential_broker.js';
import { MainConnections } from './connections.js';
import { probeCredentials } from './credential_probe.js';
import { probeWorkspace } from './workspace_probe.js';
import { probeObservability } from './observability_probe.js';
import { probeEvaluations } from './evaluation_probe.js';
import { probeFailures } from './failure_probe.js';
import { createSetupBroker } from './setup_broker.js';

if (app.isPackaged && process.argv.some(arg => /^--(?:inspect(?:-brk|-port)?|remote-debugging-(?:port|pipe))(?:=|$)/.test(arg))) {
  console.error('FORGE_INSTALLED_DEBUG_DENIED');
  app.exit(2);
}

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
let sessionSubscription: string | null = null;
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
  const overlay = process.platform === 'win32' || process.platform === 'linux';
  const titleBarColors = () => ({ color: nativeTheme.shouldUseDarkColors ? '#1b1b1a' : '#faf9f6',
    symbolColor: nativeTheme.shouldUseDarkColors ? '#e8e7e3' : '#292925', height: 48 });
  window = new BrowserWindow({ width: 1240, height: 830, minWidth: 900, minHeight: 650, title: 'ForgeCode',
    ...(overlay ? { titleBarStyle: 'hidden', titleBarOverlay: titleBarColors() } : {}),
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#1b1b1a' : '#faf9f6', autoHideMenuBar: true, show: false, webPreferences: { preload: resolve(__dirname, 'preload.js'),
      contextIsolation: true, sandbox: true, webSecurity: true, nodeIntegration: false,
      devTools: !app.isPackaged, partition: 'forge-desktop' } });
  const currentWindow = window;
  const updateTitleBar = () => {
    currentWindow.setBackgroundColor(titleBarColors().color);
    if (overlay) currentWindow.setTitleBarOverlay(titleBarColors());
  };
  nativeTheme.on('updated', updateTitleBar);
  currentWindow.once('closed', () => nativeTheme.removeListener('updated', updateTitleBar));
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
    const observedSession=currentSession!;
    checks.push(...await probeWorkspace(window, engine, smoke.directory, resolve(app.getAppPath(), '../..'), smoke.output));
    checks.push(...await probeObservability(window,engine,observedSession,smoke.directory,smoke.output));
    checks.push(...await probeEvaluations(window,engine,resolve(app.getAppPath(),'../..'),smoke.directory,smoke.output));
    checks.push(...await probeFailures(window,engine,smoke.output));
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
    if (window && !window.isDestroyed()) await writeFile(resolve(smoke.output, 'desktop-failure.png'), (await window.webContents.capturePage()).toPNG());
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
    const crypto = new CredentialCrypto({ executable: process.execPath,
      arguments: ['--credential-worker', resolve(app.getPath('userData'), 'hardened-credential-runtime')],
      cwd: process.resourcesPath, environment: credentialEnvironment(process.env) });
    try {
      const protection = await crypto.request({ action: 'probe' });
      if (protection.protection.mode === 'os_protected') {
        const value = 'controlled-credential-' + randomUUID();
        const encrypted = await crypto.request({ action: 'encrypt', value });
        const decrypted = encrypted.value ? await crypto.request({ action: 'decrypt', value: encrypted.value }) : null;
        check('installed-credential-helper', encrypted.status === 'pass' && encrypted.value !== value && decrypted?.value === value);
      } else {
        const denied = await crypto.request({ action: 'encrypt', value: 'controlled-memory-only-probe' });
        check('installed-credential-helper', denied.status === 'blocked' && denied.value === undefined);
      }
    } finally { crypto.close(); }
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
  const uiRoot = app.isPackaged ? process.resourcesPath : app.getAppPath();
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
    engine = new EngineSupervisor(smoke?{...launch,arguments:[...launch.arguments,'--capture-mode','controlled_debug']}:launch);
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
  ipcMain.handle('forge:sessions', (event, value) => { sender(event); validate('session.list.request', value); return live().call('session.list', value); });
  ipcMain.handle('forge:session-snapshot', async (event, value) => {
    sender(event); validate('session.snapshot.request', value);
    const result = await live().call('session.snapshot', value);
    currentSession = result.session.session_id;
    if (!sessionSubscription) {
      // Snapshot first, then subscribe. Replay starts at that durable high watermark.
      let subscription;
      try { subscription = await live().call('events.subscribe', { scope: { kind: 'all' }, after_cursor: result.event_cursor }); }
      catch (reason) {
        if (!(reason as Error).message.includes('INVALID_CURSOR')) throw reason;
        subscription = await live().call('events.subscribe', { scope: { kind: 'all' } });
        live().markEventGap();
      }
      sessionSubscription = subscription.subscription_id;
    }
    return result;
  });
  ipcMain.handle('forge:create-session', async (event, value) => {
    sender(event);
    validate('session.create_default.request', value);
    const created = await live().call('session.create_default', value);
    currentSession = created.session_id;
    return created;
  });
  ipcMain.handle('forge:submit', (event, value) => { sender(event); validate('session.submit.request', value); return live().call('session.submit', value); });
  for (const [channel, method] of [['files', 'workspace.files'], ['read-file', 'workspace.read_file'],
    ['changes', 'workspace.changes'], ['diff', 'workspace.diff'], ['diff-file', 'workspace.diff_file'], ['recovery-inspect','recovery.inspect'],
    ['observation-spans','observability.spans'],['observation-context','observability.context'],
    ['observation-evidence','observability.evidence'],['observation-usage','observability.usage'],
    ['observation-events','observability.events'],['observation-output','observability.output'],
    ['observation-timings','observability.timings'],['artifact-chunk','artifact.read_chunk'],
    ['evaluation-templates','evaluation.templates'],['evaluation-template','evaluation.template'],
    ['evaluation-draft','evaluation.draft'],['evaluation-validate','evaluation.validate'],['evaluation-create','evaluation.create_run'],
    ['evaluation-start','evaluation.start'],['evaluation-cancel','evaluation.cancel'],['evaluation-retry','evaluation.retry'],
    ['evaluation-runs','evaluation.list'],['evaluation-snapshot','evaluation.snapshot'],['evaluation-comparison','evaluation.comparison'],
    ['failure-list','failure.list'],['failure-get','failure.get'],['failure-annotate','failure.annotate'],['failure-save-candidate','failure.save_candidate'],['failure-check-reproduction','failure.check_reproduction'],['failure-candidate','failure.candidate']] as const) {
    ipcMain.handle('forge:' + channel, (event, value) => { sender(event); validate(method + '.request', value); return live().call(method, value); });
  }
  ipcMain.handle('forge:diagnostics', (event, value) => { sender(event); empty(value); return live().call('system.health', {}); });
  for (const [channel, action] of [['sandbox-diagnosis', 'diagnose'], ['install-sandbox', 'install']] as const) {
    ipcMain.handle('forge:' + channel, (event, value) => {
      sender(event); empty(value); const guard = captureSender(() => window, event);
      return nativeOperation(async () => {
        const runtime = await loadSetupRuntime(root, app.isPackaged);
        return createSetupBroker(runtime)(action, guard(), () => { guard(); });
      });
    });
  }
  ipcMain.handle('forge:events', (event, value) => { sender(event); empty(value); return live().events(); });
  const approvals = new NativeApprovals(live);
  const fileDialogs=new NativeFileDialogs(live);
  ipcMain.handle('forge:import-experiment-plan',(event,value)=>{sender(event);empty(value);return fileDialogs.importConfiguration(captureSender(()=>window,event));});
  ipcMain.handle('forge:export-regression-candidate',(event,value)=>{sender(event);validate('failure.candidate.request',value);
    return fileDialogs.exportCandidate((value as {candidate_id:string}).candidate_id,captureSender(()=>window,event));});
  ipcMain.handle('forge:import-results',(event,value)=>{sender(event);empty(value);return fileDialogs.importResults(captureSender(()=>window,event));});
  for(const [channel,operation] of [['export-experiment-plan','exportConfiguration'],['export-results','exportResults']] as const) {
    ipcMain.handle('forge:'+channel,(event,value)=>{sender(event);validate('evaluation.plan_export.request',value);
      return fileDialogs[operation]((value as {run_id:string}).run_id,captureSender(()=>window,event));});
  }
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
  powerMonitor.on('resume', () => { if(engine?.state==='ready') void engine.refreshHealth().catch(() => { failure='系统恢复后连接状态未确认；请查询恢复对账。'; }); });
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
