/** Fixed Main-owned assets. Development and installed launch paths never fall back to one another. */
import { createHash } from 'node:crypto';
import { readFile, realpath } from 'node:fs/promises';
import { delimiter, dirname, isAbsolute, resolve } from 'node:path';
import { verifyInstalled } from '../../../../packaging/verify-installed.mjs';
import { MANIFEST_HASH } from '@forgecode/contracts';
import { verifyAsset, type Asset } from '../../../../packaging/verify-release.mjs';
import { executionMode, type ExecutionMode } from './execution_mode.js';

export type EngineLaunch = Readonly<{ executable: string; arguments: readonly string[]; cwd: string;
  environment: Readonly<Record<string, string>>; manifestHash: string; profile: 'desktop' | 'test' }>;

function environment(): Record<string, string> {
  const output: Record<string, string> = {};
  for (const name of ['SystemRoot', 'windir', 'ProgramFiles', 'LOCALAPPDATA', 'HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'TEMP', 'TMP', 'LANG']) {
    if (process.env[name]) output[name] = process.env[name]!;
  }
  // Project toolchains are inherited only through PATH; loader/config/key variables are absent.
  if (process.env.PATH) output.PATH = process.env.PATH;
  output.PYTHONDONTWRITEBYTECODE = '1';
  return output;
}

export async function loadDevelopmentEngine(root: string, options: { dataDir: string; profile?: 'desktop' | 'test'; fixture?: string; executionMode?: ExecutionMode }): Promise<EngineLaunch> {
  if (!isAbsolute(root) || !isAbsolute(options.dataDir)) throw new Error('Main paths must be absolute');
  root = await realpath(root);
  const manifest = JSON.parse(await readFile(resolve(root, 'apps/desktop/development-assets.json'), 'utf8'));
  if (manifest.schema_version !== 'forge.desktop.development-assets.v1' || !Array.isArray(manifest.engine_sources) || !manifest.engine_sources.length) {
    throw new Error('Development Engine inventory is missing');
  }
  for (const asset of [...manifest.engine_sources, ...manifest.locks]) await verifyAsset(root, asset);
  const pythonPath = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python';
  if (manifest.python?.path !== pythonPath) throw new Error('Development Python must use the fixed virtual environment');
  // Linux venv executables normally link to the trusted developer runtime outside the source tree.
  // Only this fixed alias may resolve outside; its actual executable bytes must match the inventory.
  const executable = resolve(root, pythonPath);
  const target = await realpath(executable);
  if (createHash('sha256').update(await readFile(target)).digest('hex') !== manifest.python.sha256) throw new Error('Development Python integrity mismatch');
  // Launch through the venv alias: invoking its resolved Linux target loses pyvenv.cfg
  // and the isolated Engine can no longer import the locked environment's packages.
  const contractPath = await verifyAsset(root, manifest.contracts);
  const contractText = (await readFile(contractPath, 'utf8')).replace(/\r\n?/g, '\n');
  const manifestHash = createHash('sha256').update(contractText, 'utf8').digest('hex');
  const profile = options.profile ?? 'desktop';
  if (profile === 'test' && !options.fixture || profile !== 'test' && options.fixture) throw new Error('Scripted fixture requires explicit development test profile');
  const args = ['-I', '-B', '-m', 'forge.engine', '--data-dir', options.dataDir, '--profile', profile, '--principal', 'main'];
  if (options.fixture) args.push('--execution-mode', 'local-trusted', '--scripted-fixture', await realpath(options.fixture));
  else if (executionMode(options.executionMode ?? 'strict') !== 'strict') args.push('--execution-mode', options.executionMode!);
  return Object.freeze({ executable, arguments: Object.freeze(args), cwd: root,
    environment: Object.freeze(environment()), manifestHash, profile });
}

export async function loadInstalledEngine(resources: string, dataDir: string, selectedMode: ExecutionMode = 'strict'): Promise<EngineLaunch> {
  if (!isAbsolute(resources) || !isAbsolute(dataDir)) throw new Error('Installed paths must be absolute');
  const mode = executionMode(selectedMode);
  const manifest = await verifyInstalled(resources,{contractHash:MANIFEST_HASH});
  if (manifest.schema_version !== 'forge.release.manifest.v1' || !manifest.engine || !Array.isArray(manifest.engine_dependencies) || !manifest.engine_dependencies.length ||
      !/^engine\//.test(manifest.engine.path) || !/^[0-9a-f]{64}$/.test(manifest.contract_manifest_hash)) {
    throw new Error('Installed Engine manifest is incomplete');
  }
  for (const asset of manifest.engine_dependencies) await verifyAsset(resources, asset);
  const executable = await verifyAsset(resources, manifest.engine);
  const args = ['--data-dir', dataDir, '--profile', 'desktop', '--principal', 'main'];
  const env = environment();
  if (mode !== 'strict') args.push('--execution-mode', mode);
  if (mode === 'local-trusted') {
    // The complete bundles were verified above. Supply core tools to local Shell commands too.
    if (process.platform === 'win32') env.PATH = [...['powershell', 'git', 'ripgrep']
      .map(name => dirname(resolve(resources, manifest.tools[name].entry.path))), env.PATH ?? ''].join(delimiter);
  }
  return Object.freeze({ executable, arguments: Object.freeze(args), cwd: resources,
    environment: Object.freeze(env), manifestHash: manifest.contract_manifest_hash, profile: 'desktop' });
}

/** Setup uses the same fixed, hashed runtime closure; it accepts no UI paths. */
export async function loadSetupRuntime(root: string, installed: boolean) {
  if (!isAbsolute(root)) throw new Error('Setup root must be fixed by Main');
  root = await realpath(root);
  let node: string, entry: string, manifestHash: string;
  if (installed) {
    const manifest = await verifyInstalled(root,{contractHash:MANIFEST_HASH});
    if (!manifest.bridge || !manifest.node || !Array.isArray(manifest.bridge_dependencies) || !manifest.bridge_dependencies.length ||
        !/^bridge\//.test(manifest.bridge.path) || !/^runtimes\/node\//.test(manifest.node.path)) throw new Error('Installed setup assets are incomplete');
    for (const asset of manifest.bridge_dependencies) await verifyAsset(root, asset);
    node = await verifyAsset(root, manifest.node); entry = await verifyAsset(root, manifest.bridge);
    manifestHash = manifest.contract_manifest_hash;
  } else {
    const release = JSON.parse(await readFile(resolve(root, 'release-lock.json'), 'utf8'));
    const nodeAsset = release.assets.find((value: any) => value.name === 'node' && value.platform === process.platform + '-' + process.arch);
    const inventoryAsset = release.assets.find((value: any) => value.name === 'bridge-runtime-manifest');
    if (!nodeAsset || !inventoryAsset) throw new Error('Fixed development setup runtime is unavailable');
    node = await verifyAsset(root, nodeAsset);
    const inventory = JSON.parse(await readFile(await verifyAsset(root, inventoryAsset), 'utf8'));
    if (inventory.schema_version !== 'forge.bridge.runtime.v1' || !Array.isArray(inventory.files) || !inventory.files.length) throw new Error('Invalid setup dependency closure');
    for (const asset of inventory.files) await verifyAsset(root, asset);
    const bridgeAsset = inventory.files.find((value: any) => value.path === 'sandbox_bridge/dist/main.js');
    if (!bridgeAsset) throw new Error('Fixed Bridge setup entry is absent');
    entry = await verifyAsset(root, bridgeAsset); manifestHash = inventoryAsset.sha256;
  }
  if (!/^[0-9a-f]{64}$/.test(manifestHash)) throw new Error('Setup manifest hash is invalid');
  return Object.freeze({ node, entry, root, manifestHash, environment: Object.freeze(environment()) });
}

export async function uiAsset(root: string, manifest: Record<string, Asset>, url: string): Promise<{ path: string; contentType: string }> {
  if (url.includes('%') || url.includes('\\') || /\/\.{1,2}(?:\/|$)/.test(url)) throw new Error('Encoded or relative resource path is forbidden');
  const parsed = new URL(url);
  if (parsed.protocol !== 'forge-app:' || parsed.host !== 'ui' || parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error('Unknown resource origin');
  const key = parsed.pathname === '/' ? '/index.html' : parsed.pathname;
  const asset = Object.hasOwn(manifest, key) ? manifest[key] : null;
  if (!asset) throw new Error('Resource is absent from the built manifest');
  const path = await verifyAsset(root, asset);
  const extension = path.split('.').pop();
  const contentType = extension === 'html' ? 'text/html; charset=utf-8' : extension === 'js' ? 'text/javascript; charset=utf-8' :
    extension === 'css' ? 'text/css; charset=utf-8' : extension === 'svg' ? 'image/svg+xml' : 'application/octet-stream';
  return { path, contentType };
}
