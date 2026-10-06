/** Development acceptance uses actual Electron windows, frames, IPC and CSP. */
import { BrowserWindow, type WebContents } from 'electron';
import { resolve } from 'node:path';

export async function probeSecurity(main: BrowserWindow, preload: string) {
  const checks: Array<{ id: string; status: string }> = [];
  const check = (id: string, value: boolean) => checks.push({ id, status: value ? 'pass' : 'fail' });
  const rejected = (contents: WebContents, code: string) => contents.executeJavaScript(`(async()=>{try{await (${code});return false}catch{return true}})()`);
  check('unknown-business-interface-denied', await main.webContents.executeJavaScript(
    'typeof window.forgeDesktop.invoke === "undefined" && typeof window.forgeDesktop.approve === "undefined" && typeof window.forgeDesktop.readFile === "undefined" && typeof window.forgeDesktop.credentials === "undefined"'));
  check('malformed-ipc-payload-denied', await rejected(main.webContents, 'window.forgeDesktop.session({session_id:"forged",role:"main"})'));
  check('forged-approval-id-denied', await rejected(main.webContents, 'window.forgeDesktop.requestApproval("forged-approval")'));
  check('csp-eval-denied', await main.webContents.executeJavaScript('(()=>{try{eval("window.__unsafeEval=1");return false}catch{return window.__unsafeEval===undefined}})()'));
  check('project-html-protocol-denied', await main.webContents.executeJavaScript('fetch("forge-app://ui/project.html").then(r=>r.status===404).catch(()=>true)'));
  check('protocol-traversal-denied', await main.webContents.executeJavaScript('fetch("forge-app://ui/%252e%252e/secret.html").then(r=>r.status===404).catch(()=>true)'));
  check('iframe-has-no-preload', await main.webContents.executeJavaScript(`(()=>{const frame=document.createElement('iframe');document.body.appendChild(frame);
    const denied=typeof frame.contentWindow.forgeDesktop==='undefined';frame.remove();return denied})()`));
  check('new-window-denied', await main.webContents.executeJavaScript('window.open("https://example.invalid")===null'));
  // A second real renderer with the same preload is still an unauthorized sender.
  const auxiliary = new BrowserWindow({ show: false, webPreferences: { preload: resolve(preload), contextIsolation: true,
    sandbox: true, nodeIntegration: false, webSecurity: true, partition: 'forge-desktop' } });
  try {
    await auxiliary.loadURL('forge-app://ui/index.html');
    check('foreign-window-ipc-denied', await rejected(auxiliary.webContents, 'window.forgeDesktop.status()'));
    check('foreign-window-directory-dialog-denied', await rejected(auxiliary.webContents, 'window.forgeDesktop.selectProject()'));
    // Forge protocol never executes project markup; data navigation has no grants either.
    let blocked = false;
    try { await auxiliary.loadURL('data:text/html,<script>window.projectScript=1</script>'); } catch { blocked = true; }
    check('untrusted-html-navigation-denied', blocked);
  } finally { auxiliary.destroy(); }
  return checks;
}
