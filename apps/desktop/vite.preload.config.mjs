import { defineConfig } from 'vite';
export default defineConfig({ build: {
  lib: { entry: 'src/preload/index.ts', formats: ['cjs'], fileName: () => 'preload.js' },
  outDir: '.vite/build', emptyOutDir: false, rollupOptions: { external: ['electron'] }
} });
