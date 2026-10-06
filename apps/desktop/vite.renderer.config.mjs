import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
export default defineConfig({ root: fileURLToPath(new URL('../../packages/ui/', import.meta.url)), base: './',
  build: { outDir: fileURLToPath(new URL('./.vite/renderer/ui/', import.meta.url)), emptyOutDir: true }
});
