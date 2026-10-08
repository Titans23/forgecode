/** Controlled synthetic native verifier. No model, no project tools, no public endpoint by default. */
import { randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createServer as httpServer } from 'node:http';
import { createServer as socketServer } from 'node:net';
import { basename, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import os from 'node:os';
import { canonicalHash, strictLoads, validate } from '@forgecode/contracts';
import { SrtAdapter } from './srt-adapter.js';
import { windowsSupported } from './windows-adapter.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const installed=basename(dirname(root))==='bridge';
const checks: any[] = [];
const check = (id: string, status: 'pass' | 'fail' | 'blocked', observations: any) => checks.push({ id, status, observations });
const pause = (ms: number) => new Promise(r => setTimeout(r, ms));

export async function verifyNative(platform: 'linux' | 'win32'): Promise<any> {
  if (process.platform !== platform || process.arch !== 'x64' || platform === 'win32' && !windowsSupported()) return { schema_version: 'forge.native.acceptance.v1',
    status: 'blocked', reason: 'Supported native runner is unavailable', checks, eligible_for_native_pass: false };
  if (process.argv.length !== 6 || process.argv[2] !== '--fixture' || process.argv[4] !== '--owner') throw new Error('Invalid verifier invocation');
  const fixture = resolve(process.argv[3]);
  const owner = strictLoads(process.argv[5]) as any;
  validate('owner-identity', owner);
  if (owner.execution_id !== null) throw new Error('Invalid verifier owner');
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of process.stdin) { size += chunk.length; if (size > 8192) throw new Error('Verifier input too large'); chunks.push(chunk); }
  const options = strictLoads(Buffer.concat(chunks)) as any;
  let endpoint: URL | undefined;
  if (options.allowed_endpoint) {
    endpoint = new URL(options.allowed_endpoint);
    if (endpoint.protocol !== 'http:' || endpoint.username || endpoint.password || endpoint.search || endpoint.hash ||
      !/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/i.test(endpoint.hostname) || /\.(?:local|localhost|internal)$/.test(endpoint.hostname)) {
      throw new Error('Explicit canary endpoint must be a public HTTP hostname with no credentials/query');
    }
  }
  const paths = { project: resolve(fixture, '中文 project with spaces'), sensitive: resolve(fixture, 'synthetic-sensitive'),
    outside: resolve(fixture, 'outside'), control: resolve(fixture, 'control') };
  for (const path of Object.values(paths)) await mkdir(path);
  const canary = randomUUID();
  await writeFile(resolve(paths.sensitive, 'secret.txt'), canary);
  await writeFile(resolve(paths.outside, 'untouched.txt'), 'original');
  let assets:Record<string,string>;
  if(installed) {
    const {verifyInstalled}=await import('../../packaging/verify-installed.mjs');
    const resources=resolve(root,'../..'), manifest=await verifyInstalled(resources);
    if(process.versions.node!==manifest.node_version)throw new Error('Installed native runtime mismatch');
    assets=Object.fromEntries([{...manifest.node,name:'node'},...manifest.native_helpers].map((a:any)=>[a.name,resolve(resources,a.path)]));
  } else {
    const lock = JSON.parse(await readFile(resolve(root, 'release-lock.json'), 'utf8'));
    assets = Object.fromEntries(lock.assets.filter((a: any) => ['all', `${platform}-x64`].includes(a.platform)).map((a: any) => [a.name, resolve(root, a.path)]));
  }
  const output = new Map<string, Buffer[]>();
  const adapter = new SrtAdapter(root, paths.control, owner, assets, event => {
    const id = event.execution_id as string;
    const raw = Buffer.from(event.raw_base64 as string, 'base64');
    if (event.stream === 'stdout') { if (!output.has(id)) output.set(id, []); output.get(id)!.push(raw); }
    return true;
  });
  const workspace = `ws-${randomUUID()}`;
  const policy = { schema_version: 'forge.sandbox.policy.v1', policy_id: `policy-${randomUUID()}`, workspace_id: workspace,
    filesystem: { read_mode: 'backend_default_with_protected_paths', read_roots: [paths.project], write_roots: [paths.project],
      protected_paths: [paths.sensitive, paths.control, resolve(paths.project, '.git'), resolve(paths.project, '.forge'),
        ...(platform === 'win32' && typeof options.worker_root === 'string' ? [options.worker_root] : [])],
      deny_overrides_allow: true, reject_unsafe_links: true },
    network: { mode: endpoint ? 'allowlist' : 'deny_direct', allowed_domains: endpoint ? [endpoint.hostname] : [], dns_isolation_required: false },
    limits: { memory_bytes: null, disk_bytes: null, pids: null, wall_time_seconds: 30, command_output_bytes: 131072, session_artifact_bytes: 2097152 },
    environment_keys: [], fallback: 'deny', session_mutation: 'replace_session' };
  await writeFile(resolve(fixture, 'policy.json'), JSON.stringify({ owner, policy, policy_hash: canonicalHash(policy) }, null, 2));
  async function start(code: string): Promise<string> {
    const id = `exec-${randomUUID()}`;
    const command = { mode: 'argv', argv: [process.execPath, '-e', code], cwd: paths.project, environment: {},
      deadline_utc: new Date(Date.now() + 20000).toISOString(), output_limit_bytes: 131072 };
    await adapter.executeNative({ sandbox_session_id: owner.sandbox_session_id, execution_id: id, command, command_hash: canonicalHash(command) });
    return id;
  }
  async function run(code: string): Promise<any> {
    const id = await start(code);
    const execution = adapter.executions.get(id);
    await Promise.race([execution.done, pause(15000).then(() => { throw new Error('Native canary deadline'); })]);
    const status = adapter.status({ execution_id: id });
    if (status.exit_code !== 0 || status.discarded_bytes) throw new Error('Native canary did not complete');
    return strictLoads(Buffer.concat(output.get(id) ?? []));
  }
  let directConnections = 0;
  let unixConnections = 0;
  const http = httpServer((_, response) => { response.end('private-controlled-canary'); });
  http.on('connection', () => { directConnections++; });
  const unix = socketServer(socket => { unixConnections++; socket.end(); });
  let cleanup: any;
  let initialized = false;
  try {
    const prerequisites = await adapter.probe({ workspace_id: workspace, workspace_path: paths.project });
    if (prerequisites.readiness !== 'ready') {
      check('native-prerequisites', 'blocked', { readiness: prerequisites.readiness });
    } else {
      if (platform === 'win32') {
        check('W01', 'pass', { platform: process.platform, build: os.release(), architecture: process.arch, backend: 'srt-win' });
        const stronger = structuredClone(policy); stronger.network.dns_isolation_required = true;
        try { await adapter.initializeNative(stronger); check('W12', 'fail', { stronger_policy_accepted: true }); }
        catch (error) { check('W12', (error as any).kind === 'CAPABILITY_UNSATISFIED' ? 'pass' : 'blocked', { dns_isolation: false, kind: (error as any).kind }); }
      }
      await adapter.initializeNative(policy);
      initialized = true;
      if (platform === 'win32') {
        const changed = structuredClone(policy); changed.filesystem.write_roots.push(paths.outside);
        try { await adapter.initializeNative(changed); check('N07', 'fail', { session_mutated: true }); }
        catch (error) { check('N07', (error as any).kind === 'POLICY_DENIED' ? 'pass' : 'blocked', { kind: (error as any).kind, session_mutated: false }); }
      }
      try {
        const allowed = await run("const fs=require('node:fs');fs.writeFileSync('allowed.txt','allowed');process.stdout.write(JSON.stringify({value:fs.readFileSync('allowed.txt','utf8')}));");
        check(platform === 'win32' ? 'W03' : 'workspace-write-read', allowed.value === 'allowed' && await readFile(resolve(paths.project, 'allowed.txt'), 'utf8') === 'allowed' ? 'pass' : 'fail', { execution_verified: allowed.value === 'allowed' });
      } catch {
        check('workspace-write-read', 'blocked', { reason: 'Real SRT workload could not start; namespace/seccomp/system policy requires review' });
      }
      if (checks.at(-1).status === 'pass') {
        if (platform === 'win32') {
          const long = await run("const fs=require('node:fs');const p='long/'+('a'.repeat(100))+'/'+('b'.repeat(100))+'/'+('c'.repeat(50));let ok=false;try{fs.mkdirSync(p,{recursive:true});fs.writeFileSync(p+'/crlf.txt','line1\\r\\n中文\\r\\n');ok=fs.readFileSync(p+'/crlf.txt','utf8')==='line1\\r\\n中文\\r\\n'}catch{}process.stdout.write(JSON.stringify({ok}));");
          check('windows-long-path-crlf', long.ok ? 'pass' : 'fail', long);
          const id = `exec-${randomUUID()}`;
          const command = { mode: 'shell_script', shell: 'pwsh', script: "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();[Console]::Write('中文 a&b');exit 7",
            cwd: paths.project.toLowerCase(), environment: {}, deadline_utc: new Date(Date.now() + 20000).toISOString(), output_limit_bytes: 131072 };
          await adapter.executeNative({ sandbox_session_id: owner.sandbox_session_id, execution_id: id, command, command_hash: canonicalHash(command) });
          await adapter.executions.get(id).done;
          const observed = Buffer.concat(output.get(id) ?? []).toString('utf8');
          check('W04', adapter.status({ execution_id: id }).exit_code === 7 && observed === '中文 a&b' ? 'pass' : 'fail',
            { exit_code: adapter.status({ execution_id: id }).exit_code, utf8_equal: observed === '中文 a&b' });
        }
        const protectedResult = await run(`const fs=require('node:fs');let read=false,write=false;try{fs.readFileSync(${JSON.stringify(resolve(paths.sensitive, 'secret.txt'))});read=true}catch{}try{fs.writeFileSync(${JSON.stringify(resolve(paths.outside, 'untouched.txt'))},'changed');write=true}catch{}process.stdout.write(JSON.stringify({read,write}));`);
        check('protected-read-and-outside-write', !protectedResult.read && !protectedResult.write && await readFile(resolve(paths.outside, 'untouched.txt'), 'utf8') === 'original' ? 'pass' : 'fail', protectedResult);
        // This catches point-in-time glob protection gaps; a failure cannot be relabelled a native pass.
        const dynamic = await run("const fs=require('node:fs');let readable=false;try{fs.mkdirSync('dynamic');fs.writeFileSync('dynamic/.env','synthetic');readable=fs.readFileSync('dynamic/.env','utf8')==='synthetic'}catch{}process.stdout.write(JSON.stringify({readable}));");
        check('dynamic-sensitive-file', dynamic.readable ? 'fail' : 'pass', dynamic);
        await new Promise<void>(r => http.listen(0, '127.0.0.1', r));
        const port = (http.address() as any).port;
        const networkCode = (remove: boolean) => `${remove ? "for(const k of Object.keys(process.env))if(/proxy/i.test(k))delete process.env[k];" : ''}const net=require('node:net');const s=net.connect({host:'127.0.0.1',port:${port}});let connected=false;const done=()=>{s.destroy();process.stdout.write(JSON.stringify({connected}));};s.once('connect',()=>{connected=true;done()});s.once('error',done);s.setTimeout(1000,done);`;
        const denied = await run(networkCode(false));
        check('C11', !denied.connected && directConnections === 0 ? 'pass' : 'fail', { direct_connected: denied.connected, host_connections: directConnections });
        const bypass = await run(networkCode(true));
        check('C12', !bypass.connected && directConnections === 0 ? 'pass' : 'fail', { proxy_removed: true, direct_connected: bypass.connected, host_connections: directConnections });
        const socketPath = platform === 'win32' ? '\\\\.\\pipe\\forge-native-' + canary : resolve(paths.project, 'controlled.sock');
        await new Promise<void>(r => unix.listen(socketPath, r));
        const socket = await run(`const net=require('node:net');const s=net.connect({path:${JSON.stringify(socketPath)}});let connected=false;const done=()=>{s.destroy();process.stdout.write(JSON.stringify({connected}));};s.once('connect',()=>{connected=true;done()});s.once('error',done);s.setTimeout(1000,done);`);
        check(platform === 'win32' ? 'named-pipe' : 'unix-socket', !socket.connected && unixConnections === 0 ? 'pass' : 'fail', { connected: socket.connected, host_connections: unixConnections });
        if (!endpoint) check('C10', 'blocked', { reason: 'Explicitly authorized controlled HTTP endpoint was not supplied; no public traffic sent' });
        else {
          const reach = await run(`const http=require('node:http');const u=new URL(${JSON.stringify(endpoint.href)});const p=new URL(process.env.HTTP_PROXY||process.env.http_proxy);const req=http.request({hostname:p.hostname,port:p.port,path:u.href,headers:{Host:u.host,'Proxy-Authorization':'Basic '+Buffer.from(decodeURIComponent(p.username)+':'+decodeURIComponent(p.password)).toString('base64')}},r=>{r.resume();r.on('end',()=>process.stdout.write(JSON.stringify({status:r.statusCode})));});req.setTimeout(5000,()=>req.destroy());req.on('error',()=>{process.stdout.write(JSON.stringify({status:0}));});req.end();`);
          check('C10', reach.status >= 200 && reach.status < 300 ? 'pass' : 'fail', { http_status: reach.status, hostname: endpoint.hostname });
        }
        const processId = await start("const cp=require('node:child_process');cp.spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});process.stdout.write(JSON.stringify({started:true}));setInterval(()=>{},1000);");
        await pause(200);
        const cancellation = await adapter.cancel({ execution_id: processId, reason: 'native-fixture', deadline_utc: new Date(Date.now() + 3000).toISOString() });
        check(platform === 'win32' ? 'W10' : 'owned-namespace-cancellation', cancellation.confirmed && cancellation.cleanup.state === 'clean' ? 'pass' : 'blocked', { cleanup: cancellation.cleanup });
      }
    }
  } catch (error) {
    check('native-operation', 'fail', { kind: (error as any).kind ?? 'COMMAND_FAILED' });
  } finally {
    if (http.listening) await new Promise<void>(r => http.close(() => r()));
    if (unix.listening) await new Promise<void>(r => unix.close(() => r()));
    cleanup = await adapter.close({ sandbox_session_id: owner.sandbox_session_id });
    if (initialized) check(platform === 'win32' ? 'C21' : 'session-cleanup', cleanup.state === 'clean' ? 'pass' : 'blocked', { cleanup });
  }
  if (platform === 'win32') {
    for (const [id, reason] of Object.entries({ W02: 'Native confirmation/UAC/repeat setup needs an authorized interactive Windows 10 workstation; not invoked by this verifier',
      D39: 'UAC rejection/system policy scenario requires a native interactive setup run', W05: 'Native shell and file-worker link parity awaits F11 fixture',
      W06: 'Native file-worker snapshot/special-path acceptance awaits F11 fixture', W07: 'User-level Python/Git discovery and read grant fixture not supplied',
      W08: 'Native replacement-session acceptance awaits verified cleanup', W09: 'Crash ACL/Job ownership reconciliation awaits F25',
      W11: 'Worker lease used; native shared SRT holder references remain unobservable' })) check(id, 'blocked', { reason });
  }
  const status = checks.some(c => c.status === 'fail') ? 'fail' : checks.some(c => c.status === 'blocked') || !checks.length ? 'blocked' : 'pass';
  return { schema_version: 'forge.native.acceptance.v1', status, checks, owner, policy_hash: canonicalHash(policy),
    backend: 'srt', backend_version: '0.0.78', eligible_for_native_pass: status === 'pass', cleanup,
    capabilities_promoted: false, reason: status === 'blocked' ? 'One or more required native checks remain unproven' : undefined };
}

export function printNative(report: any): never {
  process.stdout.write(JSON.stringify(report) + '\n');
  process.exit(report.status === 'pass' ? 0 : report.status === 'blocked' ? 2 : 1);
}
export function failedNative(): never {
  return printNative({ schema_version: 'forge.native.acceptance.v1', status: 'fail', checks,
    reason: 'Native verifier failed; fixture retained for reconciliation', eligible_for_native_pass: false });
}
