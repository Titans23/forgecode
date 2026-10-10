import test from 'node:test';
import assert from 'node:assert/strict';
import { bubblewrapArgv } from '../../../sandbox_bridge/dist/bubblewrap.js';

test('Linux boundary uses argv and retains metacharacters without a shell wrapper', () => {
  const args = ['--payload-file', '/control/中文 $(bad).json', '--payload-sha256', 'abc'];
  const result = bubblewrapArgv('/work/project with spaces', '/control/temp', '/runtime/node', args);
  assert.deepEqual(result.slice(result.indexOf('--') + 1), ['/runtime/node', ...args]);
  assert.deepEqual(result.slice(result.indexOf('--ro-bind'), result.indexOf('--ro-bind') + 4), ['--ro-bind', '/', '/', '--bind']);
  assert.ok(result.includes('--unshare-pid') && result.includes('--unshare-user'));
  assert.ok(result.includes('--die-with-parent') && result.includes('--new-session'));
  assert.ok(!result.includes('--unshare-net') && !result.includes('-c'));
  assert.equal(result.filter(arg => arg === '--bind').length, 2);
  assert.deepEqual(result.slice(result.indexOf('--cap-drop'), result.indexOf('--cap-drop') + 2), ['--cap-drop', 'ALL']);
});

test('Linux boundary rejects ambiguous or overlapping roots', () => {
  for (const [workspace, temp, node] of [['/', '/temp', '/node'], ['/work', '/work/tmp', '/node'],
    ['/tmp/work', '/tmp', '/node'], ['work', '/temp', '/node'], ['/work', '/temp', 'node']]) {
    assert.throws(() => bubblewrapArgv(workspace, temp, node, []));
  }
});
