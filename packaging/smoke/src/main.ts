// F01 executes a real installed Harness probe. F13 supplies the desktop UI.
import { app } from 'electron';
import { spawnSync } from 'node:child_process';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import squirrelStartup from 'electron-squirrel-startup';

if (squirrelStartup) {
  app.quit();
} else {
  app.whenReady().then(() => {
    const executable = process.env.FORGE_BUILD_SMOKE_HARNESS;
    if (!executable) {
      console.error('Set FORGE_BUILD_SMOKE_HARNESS to the F01 frozen Harness probe.');
      app.exit(2);
      return;
    }
    const child = spawnSync(executable, [], { encoding: 'utf8', timeout: 30000, windowsHide: true });
    if (child.error || child.status !== 0) {
      console.error(child.error?.message ?? child.stderr);
      app.exit(1);
      return;
    }
    const harness = JSON.parse(child.stdout);
    if (!harness.frozen || !harness.conversation_stream.includes('prompt')) {
      console.error('Frozen Harness did not expose the audited Conversation API.');
      app.exit(1);
      return;
    }
    console.log(JSON.stringify({ status: 'pass', electron: process.versions.electron,
      node: process.versions.node, harness,
      react_render: renderToStaticMarkup(createElement('span', null, harness.version)) }));
    app.exit(0);
  }).catch(error => { console.error(error); app.exit(1); });
}
