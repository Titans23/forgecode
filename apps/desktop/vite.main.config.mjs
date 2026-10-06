import { defineConfig } from 'vite';
import { builtinModules } from 'node:module';
export default defineConfig({ define: { 'import.meta.url': 'undefined' }, build: {
  lib: { entry: 'src/main/index.ts', formats: ['cjs'], fileName: () => 'main.js' },
  outDir: '.vite/build', emptyOutDir: false,
  rollupOptions: { external: ['electron', ...builtinModules, ...builtinModules.map(name => `node:${name}`)], output: { inlineDynamicImports: true } }
} });
