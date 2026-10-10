const path = require('node:path');
module.exports = {
  outDir: path.resolve(__dirname, '../../.local/build-smoke/electron'),
  packagerConfig: {
    asar: true,
  },
  makers: [
    { name: '@electron-forge/maker-squirrel', platforms: ['win32'] },
    { name: '@electron-forge/maker-deb', platforms: ['linux'] },
  ],
  plugins: [{
    name: '@electron-forge/plugin-vite',
    config: {
      build: [{ entry: 'src/main.ts', config: 'vite.main.config.mjs' }],
      renderer: [],
    },
  }],
};
