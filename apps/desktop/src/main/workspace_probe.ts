/** Development-only checks drive actual rendered components and the real Engine. */
import { nativeTheme, type BrowserWindow } from 'electron';
import { readFile, realpath, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import type { EngineSupervisor } from './supervisor.js';
import { probeLayout } from './layout_probe.js';

export async function probeWorkspace(window: BrowserWindow, engine: EngineSupervisor, directory: string, sourceRoot: string, output: string) {
  const checks: Array<{ id: string; status: string }> = [];
  const check = (id: string, passed: boolean) => checks.push({ id, status: passed ? 'pass' : 'fail' });
  const js = (code: string) => window.webContents.executeJavaScript(code, true);
  async function screenshot(name: string) {
    await js('document.fonts.ready');
    // DOM assertions can finish before Chromium presents the next painted frame.
    await new Promise(resolve => setTimeout(resolve, 200));
    await writeFile(resolve(output, name), (await window.webContents.capturePage()).toPNG());
  }
  async function until(code: string) {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      if (await js(code)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error('Workspace UI condition did not become ready: ' + code);
  }
  await until('!!document.querySelector("textarea[aria-label=任务输入]")');
  const diagnosis = await js('window.forgeDesktop.diagnoseSandbox()');
  check('named-fixed-sandbox-diagnosis', ['pass','blocked'].includes(diagnosis.status) && diagnosis.administrator_invoked !== true);
  const before = await js('window.forgeDesktop.status()');
  const original = await engine.call('session.get', { session_id: before.session_id });
  await js(`(()=>{const input=document.querySelector('textarea');input.focus();
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'中文输入测试');
    input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
    input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,keyCode:229,bubbles:true}));})()`);
  await new Promise(resolve => setTimeout(resolve, 100));
  const during = await engine.call('session.get', { session_id: before.session_id });
  check('actual-composer-ime-enter-no-submit', original.turns.length === during.turns.length && await js('document.querySelector("textarea").value==="中文输入测试"'));
  await js(`(()=>{const input=document.querySelector('textarea');input.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}));
    input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',shiftKey:true,bubbles:true}));})()`);
  check('actual-composer-shift-enter-no-submit', (await engine.call('session.get', { session_id: before.session_id })).turns.length === original.turns.length);

  // Collapse only changes shell layout: the mounted composer and Engine session survive.
  window.focus();
  await js(`document.querySelector('textarea').dataset.smokeDraft='preserved';document.querySelector('[data-testid=sidebar-toggle]').focus()`);
  await until(`document.hasFocus()&&document.activeElement.matches('[data-testid=sidebar-toggle]')`);
  window.webContents.sendInputEvent({type:'keyDown',keyCode:'Enter'});
  window.webContents.sendInputEvent({type:'char',keyCode:'Enter'});
  window.webContents.sendInputEvent({type:'keyUp',keyCode:'Enter'});
  await until(`document.querySelector('[data-testid=sidebar-toggle]').getAttribute('aria-expanded')==='false'`);
  check('actual-sidebar-keyboard-collapse-preserves-draft', await js(`!document.querySelector('.sidebar').checkVisibility()&&document.querySelector('textarea').dataset.smokeDraft==='preserved'&&document.querySelector('textarea').value==='中文输入测试'`) &&
    (await js('window.forgeDesktop.status()')).session_id === before.session_id &&
    (await engine.call('session.get', {session_id:before.session_id})).turns.length === original.turns.length);
  checks.push(...await probeLayout(window, output, 'workspace-collapsed'));
  const toggle = await js(`(()=>{const r=document.querySelector('[data-testid=sidebar-toggle]').getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}})()`);
  window.webContents.sendInputEvent({type:'mouseDown',button:'left',clickCount:1,...toggle});
  window.webContents.sendInputEvent({type:'mouseUp',button:'left',clickCount:1,...toggle});
  await until(`document.querySelector('.sidebar').checkVisibility()`);
  check('actual-sidebar-expands-with-current-page', await js(`document.querySelector('[data-page=workspace]').getAttribute('aria-current')==='page'&&document.querySelector('textarea').dataset.smokeDraft==='preserved'&&document.querySelector('textarea').value==='中文输入测试'`));
  await js(`delete document.querySelector('textarea').dataset.smokeDraft`);

  // Only the smoke-owned synthetic project is reset, so the ordinary GUI path
  // can run the same genuine repair/test script independently of the demo path.
  const project = resolve(directory, 'project');
  if (await realpath(project) !== project || await realpath(resolve(project, 'calculator.py')) !== resolve(project, 'calculator.py')) throw new Error('Smoke fixture identity changed');
  await writeFile(resolve(project, 'calculator.py'), await readFile(resolve(sourceRoot, 'tests/implementation/fixtures/fix-python-add/project/calculator.py')));
  const testHash = await readFile(resolve(project, 'test_calculator.py'));
  await js(`Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='新建会话').click()`);
  await until(`document.querySelector('textarea')&&!document.querySelector('textarea').disabled&&document.querySelector('[data-testid=turn-outcome]')?.dataset.outcome===''`);
  const current = await js('window.forgeDesktop.status()');
  check('gui-creates-default-session', current.session_id !== before.session_id && !!current.session_id);
  await js(`(()=>{const input=document.querySelector('textarea');input.focus();
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'Fix integer addition in calculator.py. Preserve the tests; run unittest before and after the repair.');
    input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Enter' });
  window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Enter' });
  await until(`document.querySelector('[data-testid=turn-outcome]')?.dataset.outcome==='completed'`);
  const finished = await engine.call('session.get', { session_id: current.session_id });
  check('gui-submit-real-harness-repair-and-tests', finished.turns.length === 1 && finished.turns[0].outcome === 'completed' &&
    (await readFile(resolve(project, 'test_calculator.py'))).equals(testHash) && (await readFile(resolve(project, 'calculator.py'), 'utf8')).includes('return left + right'));
  await until(`!!Array.from(document.querySelectorAll('[data-testid=task-diff] button')).find(button=>button.textContent.includes('calculator.py'))`);
  await js(`Array.from(document.querySelectorAll('[data-testid=task-diff] button')).find(button=>button.textContent.includes('calculator.py')).click()`);
  await until(`document.querySelector('[data-testid=reverse-patch]')?.textContent.includes('return left - right')`);
  check('gui-diff-actual-reverse-patch', await js(`document.querySelector('[data-testid=reverse-patch]').textContent.includes('return left + right')`));
  await js(`document.querySelector('[data-testid=reverse-patch]').closest('details').open=true`);
  await until(`document.querySelector('[data-testid=reverse-patch]').getBoundingClientRect().height>0`);
  check('diff-selectable-as-text', await js(`(()=>{const element=document.querySelector('[data-testid=reverse-patch]');const range=document.createRange();range.selectNodeContents(element);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);const normalize=value=>value.replace(/\\r\\n/g,'\\n').trimEnd();const passed=normalize(selection.toString())===normalize(element.textContent);selection.removeAllRanges();return passed})()`));
  const oldTheme = nativeTheme.themeSource;
  check('theme-default-follows-system', oldTheme === 'system');
  if (process.platform === 'win32' || process.platform === 'linux') {
    check('actual-native-window-controls-overlay', await js(`navigator.windowControlsOverlay?.visible===true&&navigator.windowControlsOverlay.getTitlebarAreaRect().width<innerWidth`));
    check('actual-native-titlebar-drag-region', await js(`getComputedStyle(document.querySelector('.page-header')).webkitAppRegion==='drag'&&Array.from(document.querySelectorAll('.page-header button,[data-page=home]')).every(button=>getComputedStyle(button).webkitAppRegion==='no-drag')`));
  }
  try {
    nativeTheme.themeSource = 'light';
    await until(`matchMedia('(prefers-color-scheme:light)').matches&&getComputedStyle(document.querySelector('main')).backgroundColor==='rgb(255, 255, 255)'`);
    check('actual-light-theme', await js(`getComputedStyle(document.querySelector('textarea')).color==='rgb(39, 52, 60)'&&getComputedStyle(document.querySelector('.app')).backgroundColor==='rgb(245, 247, 249)'`));
    check('actual-readable-turn-status', await js(`document.querySelector('[data-testid=turn-outcome]').dataset.outcome==='completed'&&document.querySelector('[data-testid=turn-outcome]').textContent==='已完成'`));
    await js(`document.querySelector('[data-testid=reverse-patch]').closest('details').open=false;scrollTo(0,0)`);
    await screenshot('workspace-light.png');
    await js(`document.querySelector('.header-new-task').click()`);
    await until(`!!document.querySelector('.home-start button:not(:disabled)')`);
    check('actual-titlebar-new-task-navigation', await js(`document.querySelector('[data-page=home]').getAttribute('aria-current')==='page'`));
    check('home-project-action-ready', await js(`document.querySelector('.home-start button').textContent.includes('打开项目')&&document.querySelectorAll('.recent-project').length>0`));
    await screenshot('home-light.png');
    checks.push(...await probeLayout(window, output, 'home'));
    await js(`document.querySelector('[data-page=guide]').click()`);
    await until(`!!document.querySelector('[data-testid=getting-started]')`);
    await js(`document.querySelector('.guide-launch').open=true`);
    check('guide-explains-launch-and-shortcuts', await js(`document.querySelector('.guide-launch').textContent.includes('Start-ForgeCode.cmd')&&document.querySelector('[data-testid=getting-started]').textContent.includes('Shift + Enter')`));
    await js(`document.querySelector('[data-testid=guide-connections]').click()`);
    await until(`!!document.querySelector('.connection-form')`);
    check('guide-opens-real-connection-settings', await js(`document.querySelector('[data-page=settings]').getAttribute('aria-current')==='page'`));
    checks.push(...await probeLayout(window, output, 'connections'));
    nativeTheme.themeSource = 'dark';
    await until(`matchMedia('(prefers-color-scheme:dark)').matches&&getComputedStyle(document.querySelector('main')).backgroundColor==='rgb(23, 29, 35)'`);
    check('actual-dark-form-theme', await js(`getComputedStyle(document.querySelector('.connection-form input')).color==='rgb(230, 237, 239)'&&getComputedStyle(document.querySelector('.connection-form input')).backgroundColor==='rgb(28, 36, 43)'`));
    await js(`document.querySelector('[data-page=diagnostics]').click()`);
    await until(`!!document.querySelector('.diagnostics-summary')`);
    check('diagnostics-starts-with-readable-summary', await js(`document.querySelector('.diagnostics-summary').textContent.includes('演示环境')&&document.querySelector('.diagnostics-summary').textContent.includes('离线演示')&&!document.querySelector('.diagnostic-details').open`));
    checks.push(...await probeLayout(window, output, 'diagnostics'));
    await js(`Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='刷新诊断').click()`);
    await until(`document.querySelector('.diagnostic-details').open&&document.querySelector('.diagnostic-details pre').textContent.includes('active_work_items')`);
    check('diagnostics-expands-actual-health-result', await js(`JSON.parse(document.querySelector('.diagnostic-details pre').textContent).active_work_items===0`));
    await js(`document.querySelector('[data-page=home]').click();scrollTo(0,0)`);
    await until(`!!document.querySelector('.home-start')`);
    await screenshot('home-dark.png');
    await js(`document.querySelector('[data-page=guide]').click()`);
    await until(`!!document.querySelector('[data-testid=getting-started]')`);
    await screenshot('guide-dark.png');
    await js(`document.querySelector('[data-page=workspace]').click()`);
    await until(`!!document.querySelector('textarea')&&document.querySelector('[data-testid=turn-outcome]')?.dataset.outcome==='completed'`);
    check('secondary-panels-collapsed-by-default',await js(`Array.from(document.querySelectorAll('.workspace-panel,.workspace-sessions,.message-history')).every(panel=>!panel.open)`));
    await screenshot('workspace-dark.png');
    checks.push(...await probeLayout(window, output, 'workspace'));
    for (const zoom of [1, 1.5, 2]) {
      window.webContents.setZoomFactor(zoom);
      await until(`(()=>{document.querySelector('textarea').scrollIntoView({block:'center'});const rect=document.querySelector('textarea').getBoundingClientRect();return rect.top>=0&&rect.bottom<=innerHeight&&rect.left>=0&&rect.right<=innerWidth})()`);
      check('actual-zoom-' + Math.round(zoom*100), await js(`(()=>{const rect=document.querySelector('textarea').getBoundingClientRect();return rect.top>=0&&rect.bottom<=innerHeight&&rect.left>=0&&rect.right<=innerWidth&&Array.from(document.querySelectorAll('.virtual-list')).every(list=>list.querySelectorAll('[data-virtual-row]').length<=20)})()`));
    }
  } finally { window.webContents.setZoomFactor(1); nativeTheme.themeSource = oldTheme; }
  const loaded = new Promise<void>(resolve => window.webContents.once('did-finish-load', () => resolve()));
  window.webContents.reload(); await loaded;
  await until(`document.querySelector('[data-testid=turn-outcome]')?.dataset.outcome==='completed'&&document.querySelector('[data-message-sequence]')`);
  check('actual-reload-message-sequence-dedup', await js(`(()=>{const values=Array.from(document.querySelectorAll('[data-message-sequence]')).map(item=>item.dataset.messageSequence);return values.length===new Set(values).size})()`) &&
    (await engine.call('session.get', { session_id: current.session_id })).turns.length === 1);
  const preserved = await readFile(resolve(project, 'calculator.py'));
  await js(`Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='新建会话').click()`);
  await until(`document.querySelector('textarea')&&!document.querySelector('textarea').disabled&&document.querySelector('[data-testid=turn-outcome]')?.dataset.outcome===''`);
  await js(`(()=>{const input=document.querySelector('textarea');input.focus();Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'Cancel this task before any changes.');input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  window.webContents.sendInputEvent({ type:'keyDown', keyCode:'Enter' }); window.webContents.sendInputEvent({ type:'keyUp', keyCode:'Enter' });
  await until(`!!Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='取消任务')`);
  check('active-task-explains-disabled-composer', await js(`document.querySelector('textarea').disabled&&document.getElementById(document.querySelector('textarea').getAttribute('aria-describedby')).textContent.includes('任务正在进行')&&!document.querySelector('.composer-shortcuts')`));
  await js(`Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='取消任务').click()`);
  await until(`document.querySelector('[data-testid=turn-outcome]')?.dataset.outcome==='cancelled'`);
  check('gui-cancel-real-turn-preserves-files', (await readFile(resolve(project,'calculator.py'))).equals(preserved));
  check('cancel-restores-composer-shortcuts', await js(`!document.querySelector('textarea').disabled&&document.querySelectorAll('.composer-shortcuts kbd').length===3&&!document.querySelector('.composer-hint')`));
  await js(`document.querySelector('[data-testid=inspect-recovery]').click()`);
  await until(`!!document.querySelector('[data-testid=recovery-report]')`);
  check('actual-readonly-recovery-report',await js(`document.querySelector('[data-testid=recovery-report]').textContent.includes('Journal')&&document.querySelector('[data-testid=recovery-report]').textContent.includes('清理 clean')`));
  const recovered=await engine.call('recovery.inspect',{turn_id:(await engine.call('session.get',{session_id:(await js('window.forgeDesktop.status()')).session_id})).turns.at(-1).turn_id});
  check('actual-recovery-preserves-cancelled-result',recovered.state==='finished'&&recovered.cleanup_state==='clean'&&recovered.cancel_state==='confirmed');
  return checks;
}
