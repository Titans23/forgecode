import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('source setup refuses an incomplete prerequisite without reporting installation success', () => {
  const data = mkdtempSync(join(tmpdir(), 'forge-source-install-'));
  const specifications = JSON.parse(readFileSync(new URL('../../../packaging/npm/lib/tools.json', import.meta.url), 'utf8'));
  const uv = specifications[`${process.platform}-${process.arch}`].find(tool => tool.name === 'uv');
  const application = process.platform === 'win32' ? 'ForgeCode' : 'forgecode';
  mkdirSync(join(data, application, 'tools', `uv-${uv.version}`), { recursive: true });
  const script = fileURLToPath(new URL('../../../scripts/install.mjs', import.meta.url));
  const result = spawnSync(process.execPath, [script], {
    cwd: data, encoding: 'utf8',
    env: { ...process.env, LOCALAPPDATA: data, XDG_DATA_HOME: data, npm_execpath: process.execPath },
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /ForgeCode setup failed: Incomplete tool installation/);
  assert.doesNotMatch(result.stderr, /ForgeCode installed\./);
});
