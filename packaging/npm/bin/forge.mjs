#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { prepareRuntime } from '../lib/runtime.mjs';

try {
  const runtime = await prepareRuntime();
  const child = spawn(runtime.python, ['-I', '-X', 'utf8', '-m', 'forge', ...process.argv.slice(2)],
    { env: runtime.environment, stdio: 'inherit', shell: false });
  child.on('error', error => { console.error(error.message); process.exitCode = 1; });
  child.on('exit', (code, signal) => { process.exitCode = code ?? (signal ? 130 : 1); });
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
} catch (error) {
  console.error('ForgeCode installation unavailable: ' + error.message);
  process.exitCode = 1;
}
