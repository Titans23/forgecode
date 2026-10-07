/** Development-only checks drive actual rendered components and the real Engine. */
import { nativeTheme, type BrowserWindow } from 'electron';
import { readFile, realpath, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import type { EngineSupervisor } from './supervisor.js';

export async function probeWorkspace(window: BrowserWindow, engine: EngineSupervisor, directory: string, sourceRoot: string) {
  const checks: Array<{ id: string; status: string }> = [];
  const check = (id: string, passed: boolean) => checks.push({ id, status: passed ? 'pass' : 'fail' });
  const js = (code: string) => window.webContents.executeJavaScript(code, true);
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

  // Only the smoke-owned synthetic project is reset, so the ordinary GUI path
  // can run the same genuine repair/test script independently of the demo path.
  const project = resolve(directory, 'project');
  if (await realpath(project) !== project || await realpath(resolve(project, 'calculator.py')) !== resolve(project, 'calculator.py')) throw new Error('Smoke fixture identity changed');
  await writeFile(resolve(project, 'calculator.py'), await readFile(resolve(sourceRoot, 'tests/implementation/fixtures/fix-python-add/project/calculator.py')));
  const testHash = await readFile(resolve(project, 'test_calculator.py'));
  await js(`Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='新建会话').click()`);
  await until(`document.querySelector('textarea')&&!document.querySelector('textarea').disabled&&document.querySelector('[data-testid=turn-outcome]')?.textContent===''`);
  const current = await js('window.forgeDesktop.status()');
  check('gui-creates-default-session', current.session_id !== before.session_id && !!current.session_id);
  await js(`(()=>{const input=document.querySelector('textarea');input.focus();
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'Fix integer addition in calculator.py. Preserve the tests; run unittest before and after the repair.');
    input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Enter' });
  window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Enter' });
  await until(`document.querySelector('[data-testid=turn-outcome]')?.textContent==='completed'`);
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
  try {
    nativeTheme.themeSource = 'light';
    await new Promise(resolve => setTimeout(resolve, 50));
    check('actual-light-theme', await js(`matchMedia('(prefers-color-scheme:light)').matches&&getComputedStyle(document.querySelector('.app')).backgroundColor==='rgb(243, 245, 247)'`));
    nativeTheme.themeSource = 'dark';
    for (const zoom of [1, 1.5, 2]) {
      window.webContents.setZoomFactor(zoom);
      await until(`(()=>{document.querySelector('textarea').scrollIntoView({block:'center'});const rect=document.querySelector('textarea').getBoundingClientRect();return rect.top>=0&&rect.bottom<=innerHeight&&rect.left>=0&&rect.right<=innerWidth})()`);
      check('actual-zoom-' + Math.round(zoom*100), await js(`(()=>{const rect=document.querySelector('textarea').getBoundingClientRect();return rect.top>=0&&rect.bottom<=innerHeight&&rect.left>=0&&rect.right<=innerWidth&&Array.from(document.querySelectorAll('.virtual-list')).every(list=>list.querySelectorAll('[data-virtual-row]').length<=20)})()`));
    }
  } finally { window.webContents.setZoomFactor(1); nativeTheme.themeSource = oldTheme; }
  const loaded = new Promise<void>(resolve => window.webContents.once('did-finish-load', () => resolve()));
  window.webContents.reload(); await loaded;
  await until(`document.querySelector('[data-testid=turn-outcome]')?.textContent==='completed'&&document.querySelector('[data-message-sequence]')`);
  check('actual-reload-message-sequence-dedup', await js(`(()=>{const values=Array.from(document.querySelectorAll('[data-message-sequence]')).map(item=>item.dataset.messageSequence);return values.length===new Set(values).size})()`) &&
    (await engine.call('session.get', { session_id: current.session_id })).turns.length === 1);
  const preserved = await readFile(resolve(project, 'calculator.py'));
  await js(`Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='新建会话').click()`);
  await until(`document.querySelector('textarea')&&!document.querySelector('textarea').disabled&&document.querySelector('[data-testid=turn-outcome]')?.textContent===''`);
  await js(`(()=>{const input=document.querySelector('textarea');input.focus();Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'Cancel this task before any changes.');input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  window.webContents.sendInputEvent({ type:'keyDown', keyCode:'Enter' }); window.webContents.sendInputEvent({ type:'keyUp', keyCode:'Enter' });
  await until(`!!Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='取消任务')`);
  await js(`Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='取消任务').click()`);
  await until(`document.querySelector('[data-testid=turn-outcome]')?.textContent==='cancelled'`);
  check('gui-cancel-real-turn-preserves-files', (await readFile(resolve(project,'calculator.py'))).equals(preserved));
  await js(`document.querySelector('[data-testid=inspect-recovery]').click()`);
  await until(`!!document.querySelector('[data-testid=recovery-report]')`);
  check('actual-readonly-recovery-report',await js(`document.querySelector('[data-testid=recovery-report]').textContent.includes('Journal')&&document.querySelector('[data-testid=recovery-report]').textContent.includes('清理 clean')`));
  const recovered=await engine.call('recovery.inspect',{turn_id:(await engine.call('session.get',{session_id:(await js('window.forgeDesktop.status()')).session_id})).turns.at(-1).turn_id});
  check('actual-recovery-preserves-cancelled-result',recovered.state==='finished'&&recovered.cleanup_state==='clean'&&recovered.cancel_state==='confirmed');
  return checks;
}
