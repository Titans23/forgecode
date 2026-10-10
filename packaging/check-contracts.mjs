import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const localPython = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.FORGE_PYTHON || (existsSync(localPython) ? localPython : 'python');
const result = spawnSync(python, [path.join(root, 'scripts/check_contracts.py'), '--check'], { cwd: root, stdio: 'inherit', windowsHide: true });
if (result.error) process.stderr.write(result.error.message + '\n');
process.exit(result.status ?? 1);
