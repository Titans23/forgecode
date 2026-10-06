/** Controlled synthetic native verifier. No model, no project tools, no public endpoint by default. */
import { randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createServer as httpServer } from 'node:http';
import { createServer as socketServer } from 'node:net';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { canonicalHash, strictLoads, validate } from '@forgecode/contracts';
import { SrtAdapter } from './srt-adapter.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const checks: any[] = [];
const check = (id: string, status: 'pass' | 'fail' | 'blocked', observations: any) => checks.push({ id, status, observations });
const pause = (ms: number) => new Promise(r => setTimeout(r, ms));

async function verify(): Promise<any> {
  if (process.platform !== 'linux' || process.arch !== 'x64') return { schema_version: 'forge.native.acceptance.v1',
    status: 'blocked', reason: 'Supported Ubuntu native runner is unavailable', checks, eligible_for_native_pass: false };
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
  const paths = { project: resolve(fixture, 'project'), sensitive: resolve(fixture, 'synthetic-sensitive'),
    outside: resolve(fixture, 'outside'), control: resolve(fixture, 'control') };
  for (const path of Object.values(paths)) await mkdir(path);
  const canary = randomUUID();
  await writeFile(resolve(paths.sensitive, 'secret.txt'), canary);
  await writeFile(resolve(paths.outside, 'untouched.txt'), 'original');
  const lock = JSON.parse(await readFile(resolve(root, 'release-lock.json'), 'utf8'));
  const assets = Object.fromEntries(lock.assets.filter((a: any) => ['all', 'linux-x64'].includes(a.platform)).map((a: any) => [a.name, resolve(root, a.path)]));
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
      protected_paths: [paths.sensitive, paths.control, resolve(paths.project, '.git'), resolve(paths.project, '.forge')],
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
      await adapter.initializeNative(policy);
      initialized = true;
      try {
        const allowed = await run("const fs=require('node:fs');fs.writeFileSync('allowed.txt','allowed');process.stdout.write(JSON.stringify({value:fs.readFileSync('allowed.txt','utf8')}));");
        check('workspace-write-read', allowed.value === 'allowed' && await readFile(resolve(paths.project, 'allowed.txt'), 'utf8') === 'allowed' ? 'pass' : 'fail', { execution_verified: allowed.value === 'allowed' });
      } catch {
        check('workspace-write-read', 'blocked', { reason: 'Real SRT workload could not start; namespace/seccomp/system policy requires review' });
      }
      if (checks.at(-1).status === 'pass') {
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
        const socketPath = resolve(paths.project, 'controlled.sock');
        await new Promise<void>(r => unix.listen(socketPath, r));
        const socket = await run(`const net=require('node:net');const s=net.connect({path:${JSON.stringify(socketPath)}});let connected=false;const done=()=>{s.destroy();process.stdout.write(JSON.stringify({connected}));};s.once('connect',()=>{connected=true;done()});s.once('error',done);s.setTimeout(1000,done);`);
        check('unix-socket', !socket.connected && unixConnections === 0 ? 'pass' : 'fail', { connected: socket.connected, host_connections: unixConnections });
        if (!endpoint) check('C10', 'blocked', { reason: 'Explicitly authorized controlled HTTP endpoint was not supplied; no public traffic sent' });
        else {
          const reach = await run(`const http=require('node:http');const u=new URL(${JSON.stringify(endpoint.href)});const p=new URL(process.env.HTTP_PROXY||process.env.http_proxy);const req=http.request({hostname:p.hostname,port:p.port,path:u.href,headers:{Host:u.host,'Proxy-Authorization':'Basic '+Buffer.from(decodeURIComponent(p.username)+':'+decodeURIComponent(p.password)).toString('base64')}},r=>{r.resume();r.on('end',()=>process.stdout.write(JSON.stringify({status:r.statusCode})));});req.setTimeout(5000,()=>req.destroy());req.on('error',()=>{process.stdout.write(JSON.stringify({status:0}));});req.end();`);
          check('C10', reach.status >= 200 && reach.status < 300 ? 'pass' : 'fail', { http_status: reach.status, hostname: endpoint.hostname });
        }
        const processId = await start("const cp=require('node:child_process');cp.spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});process.stdout.write(JSON.stringify({started:true}));setInterval(()=>{},1000);");
        await pause(200);
        const cancellation = await adapter.cancel({ execution_id: processId, reason: 'native-fixture', deadline_utc: new Date(Date.now() + 3000).toISOString() });
        check('owned-namespace-cancellation', cancellation.confirmed && cancellation.cleanup.state === 'clean' ? 'pass' : 'blocked', { cleanup: cancellation.cleanup });
      }
    }
  } catch (error) {
    check('native-operation', 'fail', { kind: (error as any).kind ?? 'COMMAND_FAILED' });
  } finally {
    if (http.listening) await new Promise<void>(r => http.close(() => r()));
    if (unix.listening) await new Promise<void>(r => unix.close(() => r()));
    cleanup = await adapter.close({ sandbox_session_id: owner.sandbox_session_id });
    if (initialized) check('session-cleanup', cleanup.state === 'clean' ? 'pass' : 'blocked', { cleanup });
  }
  const status = checks.some(c => c.status === 'fail') ? 'fail' : checks.some(c => c.status === 'blocked') || !checks.length ? 'blocked' : 'pass';
  return { schema_version: 'forge.native.acceptance.v1', status, checks, owner, policy_hash: canonicalHash(policy),
    backend: 'srt', backend_version: '0.0.78', eligible_for_native_pass: status === 'pass', cleanup,
    capabilities_promoted: false, reason: status === 'blocked' ? 'One or more required native checks remain unproven' : undefined };
}

verify().then(report => { process.stdout.write(JSON.stringify(report) + '\n'); process.exit(report.status === 'pass' ? 0 : report.status === 'blocked' ? 2 : 1); })
  .catch(() => { process.stdout.write(JSON.stringify({ schema_version: 'forge.native.acceptance.v1', status: 'fail', checks,
    reason: 'Native verifier failed; fixture retained for reconciliation', eligible_for_native_pass: false }) + '\n'); process.exit(1); });
