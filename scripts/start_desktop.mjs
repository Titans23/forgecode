/** Build and open the fixed development client without a development HTTP server. */
import { spawn } from 'node:child_process';
import { existsSync, realpathSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const python = resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const electron = resolve(root, 'node_modules/electron/dist', process.platform === 'win32' ? 'electron.exe' : 'electron');
async function run(executable, args, windowsHide = true) {
  const environment = { ...process.env };
  for (const key of ['ELECTRON_RUN_AS_NODE', 'NODE_OPTIONS', 'NODE_PATH']) delete environment[key];
  const child = spawn(executable, args, { cwd: root, env: environment, shell: false, windowsHide, stdio: 'inherit' });
  return new Promise((resolve, reject) => { child.once('error', reject); child.once('close', code => code === 0 ? resolve() : reject(new Error('Desktop build or runtime exited with code ' + code))); });
}
export function launchDesktop(args = [resolve(root, 'apps/desktop')]) {
  // Windows applies the startup visibility flag to Electron's first GUI window too.
  return run(electron, args, false);
}
if (process.argv[1] && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))) {
try {
  const args = process.argv.slice(2);
  if (args.length > 1 || args.length === 1 && !['--check', '--help'].includes(args[0])) throw new Error('Use --check, --help, or no arguments.');
  if (args[0] === '--help') {
    console.log('ForgeCode desktop: builds and opens the local client.\n--check verifies startup prerequisites without building or launching.\nGuide: docs/install/desktop-quickstart.md');
  } else {
    const [major, minor] = process.versions.node.split('.').map(Number);
    if (major < 22 || major === 22 && minor < 13) throw new Error('Node.js 22.13 or newer is required. See docs/install/development.md for the locked toolchain.');
    if (!existsSync(python)) throw new Error('Local Python environment is missing. Run uv sync --locked --all-groups --all-extras in this repository.');
    if (!existsSync(resolve(root, 'node_modules/typescript/bin/tsc')) || !existsSync(resolve(root, 'node_modules/vite/bin/vite.js'))) throw new Error('JavaScript dependencies are missing. Run npm ci --ignore-scripts in this repository.');
    if (!existsSync(electron)) throw new Error('Electron runtime is missing. Run node node_modules/electron/install.js after installing dependencies.');
    console.log('ForgeCode startup prerequisites are present.');
    if (args[0] !== '--check') {
      console.log('Building the current desktop interface. Please wait for the ForgeCode window...');
      await run(python, [resolve(root, 'scripts/build_desktop.py')]);
      await launchDesktop();
    }
  }
} catch (error) {
  console.error('ForgeCode could not start: ' + error.message + '\nGuide: ' + resolve(root, 'docs/install/desktop-quickstart.md'));
  process.exitCode = 1;
}
}
