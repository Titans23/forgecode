import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { firstUseSetup } from '../../../apps/desktop/dist/main/first_use_setup.js';

test('first use attempts fixed setup once; later launches still diagnose without promoting readiness', async () => {
  const directory=await mkdtemp(join(tmpdir(),'forge-setup-prompt-'));
  try {
    const marker=join(directory,'prompt.json'),calls=[];
    // This double tests prompt orchestration only; it claims no native installation.
    const setup=async action=>{calls.push(action);return {status:'blocked',fresh_install_allowed:true,reason:'setup_declined'};};
    assert.equal((await firstUseSetup(marker,setup)).status,'blocked');
    assert.deepEqual(calls,['diagnose','install']);
    const record=JSON.parse(await readFile(marker,'utf8'));
    assert.deepEqual(Object.keys(record).sort(),['attempted_at','schema_version']);
    calls.length=0;
    assert.equal((await firstUseSetup(marker,setup)).status,'blocked');
    assert.deepEqual(calls,['diagnose']);
  } finally {await rm(directory,{recursive:true,force:true});}
});

test('existing shared setup or a corrupt prompt record never triggers automatic repair/install', async () => {
  const directory=await mkdtemp(join(tmpdir(),'forge-setup-prompt-'));
  try {
    const marker=join(directory,'prompt.json'),calls=[];
    const setup=async action=>{calls.push(action);return {status:'blocked',fresh_install_allowed:false};};
    await firstUseSetup(marker,setup);
    assert.deepEqual(calls,['diagnose']);
    await assert.rejects(readFile(marker),{code:'ENOENT'});
    await writeFile(marker,'{"schema_version":"fake-ready"}');
    await assert.rejects(firstUseSetup(marker,setup),/Invalid setup/);
    assert.deepEqual(calls,['diagnose']);
  } finally {await rm(directory,{recursive:true,force:true});}
});
