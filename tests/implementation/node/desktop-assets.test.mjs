import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, writeFile, mkdir, readFile, copyFile, realpath, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { uiAsset, loadInstalledEngine, loadDevelopmentEngine } from '../../../apps/desktop/dist/main/assets.js';

test('fixed UI protocol refuses traversal, wrong origins and replaced assets', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'forge-ui-assets-'));
  const path = join(directory, 'index.html');
  await writeFile(path, '<p>owned built asset</p>');
  const manifest = { '/index.html': { path: 'index.html', sha256: createHash('sha256').update('<p>owned built asset</p>').digest('hex') } };
  assert.equal((await uiAsset(directory, manifest, 'forge-app://ui/index.html')).path, await realpath(path));
  for (const url of ['file:///index.html', 'forge-app://other/index.html', 'forge-app://ui/%2e%2e/index.html',
    'forge-app://ui/../index.html', 'forge-app://ui/private.txt', 'forge-app://ui/index.html?path=secret', 'forge-app://user@ui/index.html']) {
    await assert.rejects(uiAsset(directory, manifest, url));
  }
  await writeFile(path, '<p>replaced bytes</p>');
  await assert.rejects(uiAsset(directory, manifest, 'forge-app://ui/index.html'), /integrity/);
});

test('installed Engine refuses incomplete manifest without using development Python', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'forge-installed-assets-'));
  await assert.rejects(loadInstalledEngine(directory, join(directory, 'data')), /ENOENT/);
  await writeFile(join(directory, 'release-manifest.json'), JSON.stringify({ schema_version: 'forge.release.manifest.v1', engine: { path: 'python.exe' } }));
  await assert.rejects(loadInstalledEngine(directory, join(directory, 'data')), /incomplete/);
  await assert.rejects(loadDevelopmentEngine('relative', { dataDir: directory }), /absolute/);
});

test('development Python is fixed to the venv alias and verified at its actual target', async () => {
  const root = resolve(fileURLToPath(new URL('../../..', import.meta.url)));
  const directory = await mkdtemp(join(tmpdir(), 'forge-development-python-'));
  const relative = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python';
  const python = join(directory, relative);
  await mkdir(resolve(python, '..'), { recursive: true });
  if (process.platform === 'linux') await symlink(await realpath(join(root, relative)), python);
  else await copyFile(join(root, relative), python);
  await mkdir(join(directory, 'apps/desktop'), { recursive: true });
  await writeFile(join(directory, 'source.json'), '{}');
  const source = { path: 'source.json', sha256: createHash('sha256').update('{}').digest('hex') };
  const manifest = { schema_version: 'forge.desktop.development-assets.v1', engine_sources: [source], locks: [], contracts: source,
    python: { path: relative, sha256: createHash('sha256').update(await readFile(python)).digest('hex') } };
  const path = join(directory, 'apps/desktop/development-assets.json');
  await writeFile(path, JSON.stringify(manifest));
  const launch = await loadDevelopmentEngine(directory, { dataDir: join(directory, 'data') });
  assert.equal(launch.executable, join(await realpath(directory), relative));
  assert.equal(launch.profile, 'desktop');
  assert.equal(launch.arguments[launch.arguments.indexOf('--execution-mode') + 1], 'strict');
  const workspace = await loadDevelopmentEngine(directory, { dataDir: join(directory, 'data'), executionMode: 'workspace-write' });
  assert.equal(workspace.arguments[workspace.arguments.indexOf('--execution-mode') + 1], 'workspace-write');
  const local = await loadDevelopmentEngine(directory, { dataDir: join(directory, 'data'), executionMode: 'local-trusted' });
  assert.equal(local.profile, 'desktop');
  assert.equal(local.arguments[local.arguments.indexOf('--execution-mode') + 1], 'local-trusted');
  assert.ok(!local.arguments.includes('--scripted-fixture'));
  await assert.rejects(loadDevelopmentEngine(directory, { dataDir: join(directory, 'data'), executionMode: 'automatic' }), /Invalid execution mode/);
  manifest.python.path = 'elsewhere/python.exe';
  await writeFile(path, JSON.stringify(manifest));
  await assert.rejects(loadDevelopmentEngine(directory, { dataDir: join(directory, 'data') }), /fixed virtual environment/);
  manifest.python.path = relative; manifest.python.sha256 = '0'.repeat(64);
  await writeFile(path, JSON.stringify(manifest));
  await assert.rejects(loadDevelopmentEngine(directory, { dataDir: join(directory, 'data') }), /integrity/);
});

test('development launch preserves the actual isolated venv and installed Engine imports', async () => {
  const root = await realpath(resolve(fileURLToPath(new URL('../../..', import.meta.url))));
  const launch = await loadDevelopmentEngine(root, { dataDir: join(root, '.local/asset-prefix-probe') });
  const child = spawnSync(launch.executable, ['-I', '-B', '-c',
    'import json,sys,forge.engine; print(json.dumps({"prefix":sys.prefix,"base":sys.base_prefix,"engine":forge.engine.__file__}))'],
    { cwd: root, env: launch.environment, encoding: 'utf8', timeout: 30000 });
  assert.equal(child.status, 0, child.stderr);
  const actual = JSON.parse(child.stdout);
  assert.equal(await realpath(actual.prefix), await realpath(join(root, '.venv')));
  assert.notEqual(await realpath(actual.prefix), await realpath(actual.base));
  assert.ok(actual.engine.endsWith('engine' + (process.platform === 'win32' ? '\\' : '/') + '__init__.py'));
});
