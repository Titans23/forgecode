import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { installTool, platformKey, requireNode, run, runtimeEnvironment, verifyFile } from '../packaging/npm/lib/runtime.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));

try {
  requireNode();
  const platform = platformKey();
  const npm = process.env.npm_execpath;
  if (!npm) throw new Error('Run this installer with npm run setup.');
  const specifications = JSON.parse(readFileSync(join(root, 'packaging/npm/lib/tools.json'), 'utf8'))[platform];
  const data = process.platform === 'win32'
    ? join(process.env.LOCALAPPDATA, 'ForgeCode')
    : join(process.env.XDG_DATA_HOME || join(homedir(), '.local/share'), 'forgecode');
  console.error('Preparing the source build environment...');
  const uv = await installTool(specifications.find(tool => tool.name === 'uv'), join(data, 'tools'));
  const environment = runtimeEnvironment([dirname(process.execPath), dirname(uv)]);
  const options = { cwd: root, env: environment };
  const python = readFileSync(join(root, '.python-version'), 'utf8').trim();
  run(uv, ['python', 'install', python, '--no-bin'], options);
  const interpreter = run(uv, ['python', 'find', '--system', '--managed-python', python], {
    ...options, stdio: ['ignore', 'pipe', 'inherit'], encoding: 'utf8',
  }).stdout.trim();
  console.error('Building ForgeCode (details: dist/package-build.log)...');
  run(interpreter, ['scripts/package_cli.py'], options);
  const report = JSON.parse(readFileSync(join(root, 'dist/npm-package-report.json'), 'utf8'));
  if (report.status !== 'pass') throw new Error('ForgeCode build did not pass.');
  verifyFile(report.archive, report.sha256);
  console.error('Installing the built ForgeCode package...');
  run(process.execPath, [npm, 'install', '--global', '--foreground-scripts', report.archive], options);
  console.error('ForgeCode installed. Run forge in your target project directory.');
} catch (error) {
  console.error(`ForgeCode setup failed: ${error.message}`);
  process.exitCode = 1;
}
