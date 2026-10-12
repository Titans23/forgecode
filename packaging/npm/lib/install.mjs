import { prepareRuntime } from './runtime.mjs';

try {
  await prepareRuntime();
  console.error('ForgeCode installed. Run forge in your project directory.');
} catch (error) {
  console.error('ForgeCode installation failed: ' + error.message);
  process.exitCode = 1;
}
