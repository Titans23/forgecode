import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseProcStat } from '../../../sandbox_bridge/dist/linux-ownership.js';

test('proc identity retains start time and handles arbitrary process comm parentheses/spaces', () => {
  const fields = ['S', '42', ...Array(17).fill('0'), '98765432101234567', '0'];
  const value = parseProcStat('101 (a (name) with spaces)) ' + fields.join(' '));
  assert.deepEqual(value, { pid: 101, ppid: 42, state: 'S', start: '98765432101234567' });
});

test('truncated or non-numeric proc identity cannot authorize killing a PID', () => {
  for (const value of ['101 no-parentheses', '101 (x) S 42', 'NaN (x) S 42',
    '101 (x) ' + ['S', '42', ...Array(17).fill('0'), 'invented'].join(' '),
    '-1 (x) ' + ['S', '42', ...Array(17).fill('0'), '123'].join(' ')]) {
    assert.throws(() => parseProcStat(value));
  }
});
