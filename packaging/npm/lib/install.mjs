import { prepareRuntime, run } from './runtime.mjs';

try {
  const runtime = await prepareRuntime();
  run(runtime.python, ['-I', '-X', 'utf8', '-m', 'forge', 'config', 'init'], { env: runtime.environment });
  console.error('ForgeCode installed. Configure your model with forge config setup, then run forge in your project.');
} catch (error) {
  console.error('ForgeCode installation failed: ' + error.message);
  process.exitCode = 1;
}
