import { createHash } from 'node:crypto';
import { readFile, realpath } from 'node:fs/promises';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

export async function verifyAsset(root, asset) {
  const base = await realpath(root);
  if (!asset.path || isAbsolute(asset.path) || asset.path.includes('\\')) throw new Error('Asset path is outside trusted root');
  const path = resolve(base, asset.path);
  const lexical = relative(base, path);
  if (lexical === '..' || lexical.startsWith(`..${sep}`)) throw new Error('Asset path is outside trusted root');
  const resolved = await realpath(path);
  const actual = relative(base, resolved);
  if (actual === '..' || actual.startsWith(`..${sep}`)) throw new Error('Asset link is outside trusted root');
  const hash = createHash('sha256').update(await readFile(resolved)).digest('hex');
  if (hash !== asset.sha256) throw new Error(`Asset integrity mismatch: ${asset.path}`);
  return resolved;
}

export async function verifyReleaseLock(root, target = `${process.platform}-${process.arch}`, release = false) {
  const lock = JSON.parse(await readFile(resolve(root, 'release-lock.json'), 'utf8'));
  if (lock.resolution_status !== 'resolved') throw new Error('Release dependencies are unresolved');
  if (release && lock.security?.status !== 'pass') {
    throw Object.assign(new Error('Production release blocked by dependency security review'), { code: 'SECURITY_BLOCKED' });
  }
  if (!['win32-x64', 'linux-x64'].includes(target)) throw new Error(`Unsupported release target: ${target}`);
  if (!Array.isArray(lock.components) || !lock.components.length || !Array.isArray(lock.assets) || !lock.assets.length) {
    throw new Error('Resolved lock must include components and native assets');
  }
  for (const component of lock.components.filter(c => c.kind === 'npm')) {
    const installed = JSON.parse(await readFile(resolve(root, 'node_modules', component.name, 'package.json'), 'utf8'));
    if (installed.version !== component.version) throw new Error(`Installed version mismatch: ${component.name}`);
    if (!component.integrity || !component.source || !component.license) throw new Error(`Incomplete component: ${component.name}`);
  }
  for (const [path, hash] of Object.entries(lock.lockfile_hashes)) await verifyAsset(root, { path, sha256: hash });
  for (const asset of lock.assets.filter(a => a.platform === target || a.platform === 'all')) await verifyAsset(root, asset);
  return lock;
}

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const lock = await verifyReleaseLock(root, undefined, process.argv.includes('--release'));
    console.log(JSON.stringify({ status: 'pass', scope: 'dependency-integrity', security_status: lock.security?.status,
      platform: process.platform, components: lock.components.length }));
  } catch (error) {
    console.error(JSON.stringify({ status: error.code === 'SECURITY_BLOCKED' ? 'blocked' : 'fail', reason: error.message }));
    process.exitCode = error.code === 'SECURITY_BLOCKED' ? 2 : 1;
  }
}
