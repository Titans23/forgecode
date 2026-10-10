import { defineConfig } from 'vite';
import { builtinModules } from 'node:module';
export default defineConfig({
  build: {
    lib: { entry: 'src/main.ts', formats: ['cjs'], fileName: () => 'main.js' },
    outDir: '.vite/build',
    rollupOptions: { external: ['electron', ...builtinModules, ...builtinModules.map(name => `node:${name}`)] },
  },
});
