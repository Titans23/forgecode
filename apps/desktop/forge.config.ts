import type { ForgeConfig } from '@electron-forge/shared-types';
import path from 'node:path';
import { access } from 'node:fs/promises';

const resources = path.resolve(__dirname, '../../.local/desktop-resources');

const config: ForgeConfig = {
  outDir: path.resolve(__dirname, '../../.local/desktop-packages'),
  packagerConfig: { asar: true, extraResource: [path.join(__dirname, 'ui-assets.json'),
    path.join(resources, 'engine'), path.join(resources, 'release-manifest.json')] },
  hooks: { prePackage: async () => {
    try { await access(path.join(resources, 'engine')); await access(path.join(resources, 'release-manifest.json')); }
    catch { throw new Error('Fixed frozen Engine release resources are unavailable; F28 assembly must complete before packaging.'); }
  } },
  makers: [{ name: '@electron-forge/maker-squirrel', platforms: ['win32'] },
    { name: '@electron-forge/maker-deb', platforms: ['linux'] }],
  plugins: [{ name: '@electron-forge/plugin-vite', config: {
    build: [{ entry: 'src/main/index.ts', config: 'vite.main.config.mjs' },
      { entry: 'src/preload/index.ts', config: 'vite.preload.config.mjs' }],
    renderer: [{ name: 'ui', config: 'vite.renderer.config.mjs' }]
  } }]
};
export default config;
