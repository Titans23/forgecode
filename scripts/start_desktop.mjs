/** Build and open the fixed development client without a development HTTP server. */
import { spawn } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const python = resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
async function run(executable, args) {
  const environment = { ...process.env };
  for (const key of ['ELECTRON_RUN_AS_NODE', 'NODE_OPTIONS', 'NODE_PATH']) delete environment[key];
  const child = spawn(executable, args, { cwd: root, env: environment, shell: false, windowsHide: true, stdio: 'inherit' });
  return new Promise((resolve, reject) => { child.once('error', reject); child.once('close', code => code === 0 ? resolve() : reject(new Error('Fixed desktop process failed'))); });
}
await run(python, [resolve(root, 'scripts/build_desktop.py')]);
await run(resolve(root, 'node_modules/electron/dist', process.platform === 'win32' ? 'electron.exe' : 'electron'), [resolve(root, 'apps/desktop')]);
