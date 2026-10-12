import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { rename, mkdir, rm } from 'node:fs/promises';
import { homedir } from 'node:os';
import { delimiter, dirname, join, resolve, sep } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const packageRoot = fileURLToPath(new URL('../', import.meta.url));
const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');

export function platformKey(platform = process.platform, architecture = process.arch) {
  if (!['win32', 'linux'].includes(platform) || architecture !== 'x64')
    throw new Error('Supported platforms: Windows x64 and Ubuntu 22.04/24.04 x64.');
  return `${platform}-${architecture}`;
}

export function requireNode(version = process.versions.node) {
  const [major, minor] = version.split('.').map(Number);
  if (major < 22 || (major === 22 && minor < 13))
    throw new Error('Node.js 22.13 or newer is required by the native sandbox.');
}

export function run(program, args, options = {}) {
  const result = spawnSync(program, args, { stdio: 'inherit', shell: false, ...options });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${program} exited ${result.status}; installation did not complete.`);
  return result;
}

export function runtimeEnvironment(directories, source = process.env) {
  const environment = { ...source };
  const pathNames = Object.keys(environment).filter(name => name.toUpperCase() === 'PATH');
  const inherited = pathNames.map(name => environment[name]).filter(Boolean).join(delimiter);
  for (const name of pathNames) delete environment[name];
  environment.PATH = [...new Set(directories), inherited].filter(Boolean).join(delimiter);
  if (process.platform === 'win32') environment.NoDefaultCurrentDirectoryInExePath = '1';
  return environment;
}

export function runtimeIdentity(manifest) {
  return createHash('sha256').update(JSON.stringify([manifest.wheel.sha256, manifest.constraints_sha256,
    manifest.tools_sha256, manifest.python_version])).digest('hex').slice(0, 16);
}

export function verifyFile(path, expected) {
  if (hash(path) !== expected) throw new Error(`SHA256 verification failed: ${path}`);
}

export async function installTool(tool, root) {
  const directory = join(root, `${tool.name}-${tool.version}`);
  const executable = join(directory, tool.executable);
  const marker = join(directory, '.archive-sha256');
  if (existsSync(executable) && existsSync(marker) && readFileSync(marker, 'utf8').trim() === tool.sha256)
    return executable;
  if (existsSync(directory)) throw new Error(`Incomplete tool installation: ${directory}`);
  await mkdir(root, { recursive: true });
  const archive = join(root, `${tool.name}-${tool.version}.${tool.format}`);
  if (!existsSync(archive)) {
    console.error(`Preparing ${tool.name} ${tool.version}...`);
    const response = await fetch(tool.url, { signal: AbortSignal.timeout(300000) });
    if (!response.ok) throw new Error(`Download failed (${response.status}): ${tool.url}`);
    const temporary = archive + '.download';
    writeFileSync(temporary, Buffer.from(await response.arrayBuffer()));
    verifyFile(temporary, tool.sha256);
    await rename(temporary, archive);
  }
  verifyFile(archive, tool.sha256);
  const staging = directory + '.extract';
  const prefix = resolve(root) + sep;
  if (![staging, directory].every(path => resolve(path).startsWith(prefix)))
    throw new Error('Tool directory exceeds its installation root.');
  await rm(staging, { recursive: true, force: true });
  await mkdir(staging);
  if (tool.format === 'zip') {
    const literal = value => "'" + value.replaceAll("'", "''") + "'";
    const script = "$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.IO.Compression.FileSystem; " +
      `[IO.Compression.ZipFile]::ExtractToDirectory(${literal(archive)},${literal(staging)})`;
    const powershell = join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
    run(powershell, ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')]);
  } else {
    run('/bin/tar', ['-xzf', archive, '-C', staging]);
  }
  if (!existsSync(join(staging, tool.executable))) throw new Error(`Archive entry missing: ${tool.executable}`);
  writeFileSync(join(staging, '.archive-sha256'), tool.sha256 + '\n');
  await rename(staging, directory);
  return executable;
}

export async function prepareRuntime(root = packageRoot) {
  const platform = platformKey();
  requireNode();
  const manifest = JSON.parse(readFileSync(join(root, 'assets/runtime.json'), 'utf8'));
  const wheel = join(root, 'assets', manifest.wheel.file);
  const constraints = join(root, 'assets/constraints.txt');
  verifyFile(wheel, manifest.wheel.sha256);
  verifyFile(constraints, manifest.constraints_sha256);
  verifyFile(join(root, 'lib/tools.json'), manifest.tools_sha256);
  const data = process.platform === 'win32'
    ? join(process.env.LOCALAPPDATA, 'ForgeCode')
    : join(process.env.XDG_DATA_HOME || join(homedir(), '.local/share'), 'forgecode');
  const identity = runtimeIdentity(manifest);
  const cache = join(data, 'npm', identity);
  const python = join(cache, 'python', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const ready = join(cache, 'ready.json');
  if (existsSync(ready) && existsSync(python)) {
    const value = JSON.parse(readFileSync(ready, 'utf8'));
    if (value.identity === identity)
      return { python, environment: runtimeEnvironment([dirname(process.execPath), ...value.tool_directories, dirname(python)]) };
  }
  mkdirSync(cache, { recursive: true });
  const specifications = JSON.parse(readFileSync(join(root, 'lib/tools.json'), 'utf8'))[platform];
  const tools = {};
  for (const specification of specifications)
    tools[specification.name] = await installTool(specification, join(data, 'tools'));
  const directories = Object.values(tools).map(dirname);
  const environment = runtimeEnvironment([dirname(process.execPath), ...directories, dirname(python)]);
  console.error('Preparing the Python Harness...');
  run(tools.uv, ['python', 'install', manifest.python_version, '--no-bin'], { env: environment });
  const venv = join(cache, 'python');
  if (!resolve(venv).startsWith(resolve(cache) + sep)) throw new Error('Python environment exceeds its private cache.');
  run(tools.uv, ['venv', '--clear', '--managed-python', '--python', manifest.python_version, venv], { env: environment });
  run(tools.uv, ['pip', 'install', '--python', python, '--constraints', constraints, wheel], { env: environment });
  run(python, ['-I', '-c', 'from forge.sandbox.launcher import verify_code_inventory; verify_code_inventory()'], { env: environment });
  if (process.platform === 'linux' && !existsSync('/usr/bin/bwrap'))
    console.error('Ubuntu native sandbox prerequisite missing: bubblewrap. Install it with your system package manager before running tasks.');
  writeFileSync(ready, JSON.stringify({ identity, tool_directories: directories }) + '\n');
  return { python, environment };
}
