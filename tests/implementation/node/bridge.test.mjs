import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { linkSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { canonicalHash, validate } from '../../../packages/contracts/dist/index.js';
import { createLaunchDirectory, writeLaunchPayload } from '../../../sandbox_bridge/dist/launch-payload.js';

const owner = { engine_epoch: `epoch-${randomUUID()}`, sandbox_session_id: `sandbox-${randomUUID()}`, execution_id: null };
const command = (cwd, argv) => ({ mode: 'argv', argv, cwd, environment: {},
  deadline_utc: new Date(Date.now() + 10000).toISOString(), output_limit_bytes: 65536 });
const dispatcher = resolve('sandbox_bridge/dist/dispatcher.js');
function run(spec, shells = {}, timeout = 10000) {
  return spawnSync(process.execPath, [dispatcher], { input: JSON.stringify({ command: spec, shells,
    tools: {git:process.execPath,rg:process.execPath} }),
    env: { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot }, encoding: 'utf8', timeout });
}

test('actual dispatcher preserves empty, quotes, Chinese, metacharacters and multiline argv', () => {
  const values = ['', 'with spaces', '"quoted"', "'single'", '中文', 'line\nbreak', '& | $(write-me)', 'tail\\'];
  const result = run(command(process.cwd(), [process.execPath, '-e', 'process.stdout.write(JSON.stringify(process.argv.slice(1)))', ...values]));
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), values);
  assert.throws(() => validate('command-spec', command(process.cwd(), [''])));
});

test('core argv uses the supplied owned executable instead of ambient PATH', () => {
  // Node stands in for the Git executable to expose which argv entry actually ran.
  const result=run(command(process.cwd(),['git','-e','process.stdout.write("owned-tool")']));
  assert.equal(result.status,0,result.stderr);
  assert.equal(result.stdout,'owned-tool');
});

test('dispatcher rejects a nonabsolute core tool before task execution', () => {
  const spec=command(process.cwd(),[process.execPath,'-e','process.stdout.write("must-not-run")']);
  const result=spawnSync(process.execPath,[dispatcher],{input:JSON.stringify({command:spec,shells:{},tools:{git:'project-git',rg:process.execPath}})});
  assert.equal(result.status,125);
  assert.equal(result.stdout.length,0);
});

test('explicit script executes only in dispatcher with original shell semantics', () => {
  const spec = { ...command(process.cwd(), []), mode: 'shell_script', deadline_utc: new Date(Date.now() + 60000).toISOString() };
  delete spec.argv;
  let shells;
  if (process.platform === 'win32') {
    spec.shell = 'pwsh';
    // Actual host PowerShell tests only script transport; Windows 10/pwsh7 native acceptance is separate.
    shells = { pwsh: resolve(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe') };
    spec.script = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); $v=@('中文','a&b','x$(bad)'); [Console]::Write(($v|ConvertTo-Json -Compress))";
  } else {
    spec.shell = 'bash'; shells = { bash: '/bin/bash' };
    spec.script = "printf '%s' '中文&a|b$(bad)'";
  }
  const result = run(spec, shells, 65000);
  assert.equal(result.status, 0, JSON.stringify({stderr:result.stderr,error:result.error?.code,signal:result.signal}));
  if (process.platform === 'win32') assert.deepEqual(JSON.parse(result.stdout), ['中文', 'a&b', 'x$(bad)']);
  else assert.equal(result.stdout, '中文&a|b$(bad)');
});

test('actual dispatcher forwards binary task stdin without interpreting it as control RPC', () => {
  const input = Buffer.from([0, 255, ...Buffer.from('中文\r\n{"jsonrpc":"2.0"}')]);
  const spec = command(process.cwd(), [process.execPath, '-e', 'process.stdin.pipe(process.stdout)']);
  spec.stdin_base64 = input.toString('base64');
  const child = spawnSync(process.execPath, [dispatcher], { input: JSON.stringify({ command: spec, shells: {},
    tools: {git:process.execPath,rg:process.execPath} }), timeout: 10000 });
  assert.equal(child.status, 0, child.stderr.toString());
  assert.deepEqual(child.stdout, input);
  assert.equal(run({ ...spec, stdin_base64: 'Zh==' }).status, 125);
  assert.throws(() => validate('command-spec', { ...spec, stdin_base64: 'not base64' }));
});

test('file transport works with closed stdin and preserves binary worker input', async () => {
  const root = mkdtempSync(resolve(tmpdir(), 'forge-launch-'));
  try {
    const directory = await createLaunchDirectory(root, owner.sandbox_session_id);
    await assert.rejects(createLaunchDirectory(root, owner.sandbox_session_id), /EEXIST/);
    const input = Buffer.from([0, 255, ...Buffer.from('中文\r\n')]);
    const spec = { ...command(root, [process.execPath, '-e', 'process.stdin.pipe(process.stdout)']), stdin_base64: input.toString('base64') };
    const payload = await writeLaunchPayload(directory, `exec-${randomUUID()}`, Buffer.from(JSON.stringify({ command: spec, shells: {},
      tools: { git: process.execPath, rg: process.execPath } })));
    const argv = [dispatcher, '--payload-file', payload.path, '--payload-sha256', payload.sha256];
    const result = spawnSync(process.execPath, argv, { stdio: ['ignore', 'pipe', 'pipe'], timeout: 10000 });
    assert.equal(result.status, 0, result.stderr.toString());
    assert.deepEqual(result.stdout, input);
    // These are actual dispatcher launches: altered content cannot start the task.
    writeFileSync(payload.path, '{}');
    const changed = spawnSync(process.execPath, argv, { timeout: 10000 });
    assert.equal(changed.status, 125);
    assert.equal(changed.stdout.length, 0);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('launch files reject overwrite, hard links and oversize input before dispatch', async () => {
  const root = mkdtempSync(resolve(tmpdir(), 'forge-launch-'));
  try {
    const directory = await createLaunchDirectory(root, owner.sandbox_session_id);
    const id = `exec-${randomUUID()}`;
    const payload = await writeLaunchPayload(directory, id, Buffer.from('{}'));
    await assert.rejects(writeLaunchPayload(directory, id, Buffer.from('{}')), /EEXIST/);
    await assert.rejects(writeLaunchPayload(directory, `exec-${randomUUID()}`, Buffer.alloc(1048577)), /Invalid launch/);
    linkSync(payload.path, resolve(directory, 'linked'));
    const result = spawnSync(process.execPath, [dispatcher, '--payload-file', payload.path, '--payload-sha256', payload.sha256], { timeout: 10000 });
    assert.equal(result.status, 125);
    assert.equal(result.stdout.length, 0);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
