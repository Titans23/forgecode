import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, mkdirSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, delimiter, dirname } from 'node:path';
import { createHash } from 'node:crypto';
import { platformKey, requireNode, runtimeEnvironment, runtimeIdentity, run, verifyFile, installTool } from '../../../packaging/npm/lib/runtime.mjs';

test('Windows and Linux choose their own package tools; unsupported architectures refuse', () => {
  assert.equal(platformKey('win32', 'x64'), 'win32-x64');
  assert.equal(platformKey('linux', 'x64'), 'linux-x64');
  assert.throws(() => platformKey('linux', 'arm64'), /Supported platforms/);
  assert.throws(() => platformKey('darwin', 'x64'), /Supported platforms/);
});

test('managed tools precede inherited tools without dropping unrelated environment variables', () => {
  const result = runtimeEnvironment(['managed', 'managed'], { PATH: 'old', Path: 'older', KEEP: 'value' });
  assert.equal(result.PATH, ['managed', 'old', 'older'].join(delimiter));
  assert.equal(result.KEEP, 'value');
  assert.ok(!Object.hasOwn(result, 'Path'));
});

test('existing launcher Node is sufficient; the installer does not download a second Node', () => {
  for (const version of ['20.6.0', '22.12.9']) assert.throws(() => requireNode(version), /22.13/);
  for (const version of ['22.13.0', '22.17.1', '22.23.3', '24.0.0']) assert.doesNotThrow(() => requireNode(version));
  const specifications = JSON.parse(readFileSync(new URL('../../../packaging/npm/lib/tools.json', import.meta.url), 'utf8'));
  assert.ok(Object.values(specifications).every(tools => tools.every(tool => tool.name !== 'node')));
  const host = dirname(process.execPath);
  const environment = runtimeEnvironment([host, 'managed'], { PATH: 'older-node' });
  assert.equal(environment.PATH.split(delimiter)[0], host);
});

test('a failed prerequisite command cannot report successful installation', () => {
  assert.throws(() => run(process.execPath, ['-e', 'process.exit(17)'], { stdio: 'pipe' }), /exited 17/);
});

test('changing dependency pins or tool versions does not overwrite an active older runtime', () => {
  const manifest = { wheel: { sha256: 'wheel' }, constraints_sha256: 'deps', tools_sha256: 'tools', python_version: '3.12.13' };
  const before = runtimeIdentity(manifest);
  assert.notEqual(runtimeIdentity({ ...manifest, constraints_sha256: 'new-deps' }), before);
  assert.notEqual(runtimeIdentity({ ...manifest, tools_sha256: 'new-tools' }), before);
});

test('corrupt archive is refused before extraction', async () => {
  const root = mkdtempSync(join(tmpdir(), 'forge-npm-hash-'));
  writeFileSync(join(root, 'fixture-1.zip'), 'corrupt');
  await assert.rejects(installTool({ name: 'fixture', version: '1', format: 'zip', executable: 'tool.exe',
    sha256: '0'.repeat(64), url: 'https://invalid.example/unused' }, root), /SHA256 verification failed/);
  assert.ok(!existsSync(join(root, 'fixture-1')));
});

test('verified installed tool is reused without downloading or changing user files', async () => {
  const root = mkdtempSync(join(tmpdir(), 'forge-npm-repeat-'));
  const directory = join(root, 'fixture-1');
  mkdirSync(directory);
  const executable = join(directory, 'tool.exe');
  writeFileSync(executable, 'preserve');
  writeFileSync(join(directory, '.archive-sha256'), 'declared-hash\n');
  const actual = await installTool({ name: 'fixture', version: '1', executable: 'tool.exe',
    sha256: 'declared-hash', url: 'https://invalid.example/unused' }, root);
  assert.equal(actual, executable);
  assert.equal(readFileSync(executable, 'utf8'), 'preserve');
});

test('the immutable wheel is checked before it can be installed', () => {
  const root = mkdtempSync(join(tmpdir(), 'forge-npm-wheel-'));
  const file = join(root, 'wheel.whl');
  writeFileSync(file, 'first');
  const expected = createHash('sha256').update('first').digest('hex');
  verifyFile(file, expected);
  writeFileSync(file, 'changed');
  assert.throws(() => verifyFile(file, expected), /SHA256 verification failed/);
});
