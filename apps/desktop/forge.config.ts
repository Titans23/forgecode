import type { ForgeConfig } from '@electron-forge/shared-types';
import path from 'node:path';
import { FusesPlugin } from '@electron-forge/plugin-fuses';
import { FuseVersion, FuseV1Options } from '@electron/fuses';
import { verifyInstalled } from '../../packaging/verify-installed.mjs';

const resources = path.resolve(__dirname, '../../.local/desktop-resources');

const config: ForgeConfig = {
  outDir: path.resolve(__dirname, '../../.local/desktop-packages'),
  packagerConfig: { asar: true, ...(process.platform==='linux'?{executableName:'forgecode'}:{}),
    extraResource: [...['ui-assets.json','ui','client','contracts','engine','bridge','runtimes','tools','native-helpers','licenses','sbom','release-manifest.json'].map(name=>path.join(resources,name)),
      ...(process.platform==='linux'?[path.join(resources,'linux')]:[])] },
  hooks: { prePackage: async () => {
    await verifyInstalled(resources);
    if(process.platform==='linux') {
      const {readFile}=await import('node:fs/promises');
      const osRelease=await readFile('/etc/os-release','utf8');
      if(!/^ID=ubuntu$/m.test(osRelease)||!/^VERSION_ID="?22\.04"?$/m.test(osRelease)) throw new Error('deb must build on the oldest supported Ubuntu 22.04 baseline');
    }
  } },
  makers: [{ name: '@electron-forge/maker-squirrel', platforms: ['win32'], config: {name:'ForgeCode'} },
    { name: '@electron-forge/maker-deb', platforms: ['linux'],config:{options:{name:'forgecode',bin:'forgecode',
      maintainer:'ForgeCode contributors',homepage:'https://github.com/Titans23/forgecode',
      // electron-installer-debian appends these to its Electron runtime dependencies.
      depends:['bubblewrap','socat','ripgrep','git','bash','apparmor'],
      scripts:{postinst:path.resolve(__dirname,'../../packaging/linux/postinst'),
        prerm:path.resolve(__dirname,'../../packaging/linux/prerm')}}} }],
  plugins: [new FusesPlugin({
    version: FuseVersion.V1,
    [FuseV1Options.RunAsNode]: false,
    [FuseV1Options.EnableCookieEncryption]: true,
    [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
    [FuseV1Options.EnableNodeCliInspectArguments]: false,
    [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: process.platform !== 'linux',
    [FuseV1Options.OnlyLoadAppFromAsar]: true,
    [FuseV1Options.GrantFileProtocolExtraPrivileges]: false
  }), { name: '@electron-forge/plugin-vite', config: {
    build: [{ entry: 'src/main/index.ts', config: 'vite.main.config.mjs' },
      { entry: 'src/preload/index.ts', config: 'vite.preload.config.mjs' }],
    renderer: [{ name: 'ui', config: 'vite.renderer.config.mjs' }]
  } }]
};
export default config;
