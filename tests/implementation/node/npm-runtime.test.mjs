import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, mkdirSync, existsSync, chmodSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, delimiter, dirname } from 'node:path';
import { createHash } from 'node:crypto';
import { platformKey, requireNode, runtimeEnvironment, runtimeIdentity, run, verifyFile, installTool, prepareRuntime } from '../../../packaging/npm/lib/runtime.mjs';

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
  assert.equal(result.FORGE_TASK_PATH, ['old', 'older'].join(delimiter));
  assert.equal(runtimeEnvironment(['other'], result).FORGE_TASK_PATH, result.FORGE_TASK_PATH);
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

test('cached launch keeps task Python selectable instead of inserting the Harness interpreter', async () => {
  const root = mkdtempSync(join(tmpdir(), 'forge-npm-task-python-'));
  const packageRoot = join(root, 'package');
  mkdirSync(join(packageRoot, 'assets'), { recursive: true });
  mkdirSync(join(packageRoot, 'lib'));
  const files = { 'assets/fixture.whl': 'wheel', 'assets/constraints.txt': 'pins', 'lib/tools.json': '{}' };
  const digest = text => createHash('sha256').update(text).digest('hex');
  for (const [name, content] of Object.entries(files)) writeFileSync(join(packageRoot, name), content);
  const manifest = { python_version: 'fixture', wheel: { file: 'fixture.whl', sha256: digest('wheel') },
    constraints_sha256: digest('pins'), tools_sha256: digest('{}') };
  writeFileSync(join(packageRoot, 'assets/runtime.json'), JSON.stringify(manifest));
  const identity = runtimeIdentity(manifest);
  const windows = process.platform === 'win32';
  const data = join(root, windows ? 'ForgeCode' : 'forgecode');
  const cache = join(data, 'npm', identity);
  const interpreter = join(cache, 'python', windows ? 'Scripts/python.exe' : 'bin/python');
  mkdirSync(dirname(interpreter), { recursive: true });
  writeFileSync(interpreter, '#!/bin/sh\necho harness-python\n', { mode: 0o755 });
  writeFileSync(join(cache, 'ready.json'), JSON.stringify({ identity, tool_directories: [] }));
  const taskBin = join(root, 'task-bin');
  mkdirSync(taskBin);
  const taskPython = join(taskBin, windows ? 'python.cmd' : 'python');
  writeFileSync(taskPython, windows ? '@echo off\r\necho task-python\r\n' : '#!/bin/sh\necho task-python\n');
  if (!windows) chmodSync(taskPython, 0o755);
  const previous = Object.fromEntries(['PATH', 'LOCALAPPDATA', 'XDG_DATA_HOME'].map(key => [key, process.env[key]]));
  try {
    process.env.PATH = taskBin;
    process.env.LOCALAPPDATA = root;
    process.env.XDG_DATA_HOME = root;
    const runtime = await prepareRuntime(packageRoot);
    assert.equal(runtime.python, interpreter);
    assert.equal(runtime.environment.FORGE_TASK_PATH, taskBin);
    const shell = windows ? join(process.env.SystemRoot, 'System32/cmd.exe') : '/bin/sh';
    const result = run(shell, windows ? ['/d', '/c', 'python'] : ['-c', 'python'],
      { env: runtime.environment, stdio: 'pipe', encoding: 'utf8' });
    assert.equal(result.stdout.trim(), 'task-python');
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
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
