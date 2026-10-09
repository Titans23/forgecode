/** Only Node builtins load before the installed code inventory is verified. */
import { createHash } from 'node:crypto';
import { readFile, realpath } from 'node:fs/promises';
import { basename, dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { once } from 'node:events';

const bridgeRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const installed = basename(dirname(bridgeRoot)) === 'bridge';
const root = installed ? resolve(bridgeRoot, '../..') : bridgeRoot;
const digest = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');
async function verified(asset: any): Promise<string> {
  if (!asset.path || isAbsolute(asset.path) || asset.path.includes('\\')) throw new Error('Invalid installed asset');
  const path = await realpath(resolve(root, asset.path));
  const part = relative(root, path);
  if (part === '..' || part.startsWith(`..${sep}`) || isAbsolute(part) || digest(await readFile(path)) !== asset.sha256) {
    throw new Error('Installed asset integrity mismatch');
  }
  return path;
}

async function main(): Promise<void> {
  const assets: Record<string, string> = {};
  if (installed) {
    const {verifyInstalled}=await import('../../packaging/verify-installed.mjs');
    const manifest=await verifyInstalled(root);
    if(process.versions.node!==manifest.node_version) throw new Error('Pinned installed Node mismatch');
    if(await verified(manifest.node)!==await realpath(process.execPath)||
        await realpath(resolve(root,manifest.bridge.path))!==await realpath(resolve(bridgeRoot,'entry.mjs'))) throw new Error('Installed runtime identity mismatch');
    assets.node=await verified(manifest.node);
    for(const helper of manifest.native_helpers)assets[helper.name]=await verified(helper);
    for(const [name,tool] of Object.entries(manifest.tools??{}) as [string,any][]) {
      assets[name]=await verified(tool.entry);
      assets[`tool-root-${name}`]=resolve(root,tool.root);
    }
  } else {
    const lock = JSON.parse(await readFile(resolve(root, 'release-lock.json'), 'utf8'));
    const target = `${process.platform}-${process.arch}`;
    if (lock.resolution_status !== 'resolved' || process.versions.node !== lock.node.version) throw new Error('Pinned runtime mismatch');
    for (const asset of lock.assets.filter((item: any) => item.platform === 'all' || item.platform === target)) assets[asset.name] = await verified(asset);
    if (assets.node !== await realpath(process.execPath) || !assets['bridge-runtime-manifest']) throw new Error('Missing trusted runtime assets');
    const inventory = JSON.parse(await readFile(assets['bridge-runtime-manifest'], 'utf8'));
    if (inventory.schema_version !== 'forge.bridge.runtime.v1' || inventory.files.length < 1) throw new Error('Invalid code inventory');
    for (const asset of inventory.files) await verified(asset);
    const {verifyToolBundle}=await import('../../packaging/verify-installed.mjs');
    for(const bundle of lock.tool_bundles??[]) if(bundle.platform===target) {
      assets[bundle.name]=await verifyToolBundle(root,bundle);
      assets[`tool-root-${bundle.name}`]=resolve(root,bundle.path);
    }
  }
  const { strictLoads, validate, ContractError, BRIDGE_METHODS } = await import('@forgecode/contracts');
  if (process.argv.length === 4 && process.argv[2] === '--setup-action') {
    const { runWindowsSetup } = await import('./windows-adapter.js');
    const result = await runWindowsSetup(process.argv[3], assets['srt-win']);
    await new Promise<void>((resolve, reject) => process.stdout.write(JSON.stringify(result) + '\n', error => error ? reject(error) : resolve()));
    process.exitCode = result.status === 'pass' ? 0 : 2;
    return;
  }
  const { SrtAdapter } = await import('./srt-adapter.js');
  if (process.argv.length !== 6 || process.argv[2] !== '--control-root' || process.argv[4] !== '--owner') throw new Error('Invalid trusted launch arguments');
  const control = await realpath(process.argv[3]);
  const owner = strictLoads(process.argv[5]) as any;
  validate('cleanup-report', { owner, state: 'unknown', remaining_processes: 0, diagnostic_refs: [], completed_at_utc: null });
  if (owner.execution_id !== null) throw new Error('Bridge owner must be a session identity');

  const controls: Buffer[] = [];
  const data: Buffer[] = [];
  let writing = false;
  let broken = false;
  async function pump(): Promise<void> {
    if (writing || broken) return;
    writing = true;
    try {
      while (controls.length || data.length) {
        const frame = (controls.shift() ?? data.shift())!;
        if (!process.stdout.write(frame)) await once(process.stdout, 'drain');
      }
    } catch { broken = true; process.stdin.destroy(); }
    finally { writing = false; }
  }
  function send(value: any, priority = true): boolean {
    const queue = priority ? controls : data;
    if (queue.length >= (priority ? 64 : 8) || broken) {
      if (priority) { broken = true; process.stdin.destroy(); }
      return false;
    }
    queue.push(Buffer.from(JSON.stringify({ protocol: 'forge.bridge.v1', ...value }) + '\n'));
    void pump(); return true;
  }
  const adapter = new SrtAdapter(bridgeRoot, control, owner, assets, value => {
    validate('bridge-output', value);
    return send({ jsonrpc: '2.0', method: 'bridge.output', params: value }, false);
  });
  const pending = new Set<Promise<void>>();
  async function dispatch(bytes: Buffer): Promise<void> {
    let id: string | null = null;
    try {
      const input = strictLoads(bytes) as any;
      if (!input || input.protocol !== 'forge.bridge.v1') throw new ContractError('Bridge protocol mismatch');
      const { protocol, ...request } = input;
      validate('rpc-request', request);
      id = request.id ?? null;
      if (!id) throw new ContractError('Bridge requests require an ID');
      const method = Object.hasOwn(BRIDGE_METHODS, request.method) ? (BRIDGE_METHODS as any)[request.method] : undefined;
      if (!method) throw new ContractError('Method is not on the Bridge channel', 'NOT_FOUND', -32601);
      validate(method.request_schema, request.params);
      const result = await (adapter as any)[request.method](request.params);
      validate(method.result_schema, result);
      send({ jsonrpc: '2.0', id, result });
    } catch (error) {
      const failure = error instanceof ContractError ? error : new ContractError('Bridge operation failed', 'COMMAND_FAILED', -32010);
      send({ jsonrpc: '2.0', id, error: { code: failure.code, message: failure.message,
        data: { kind: failure.kind, retryable: false, correlation_id: id ?? 'bridge' } } });
    }
  }
  // Bounded JSONL frames; an oversized frame is drained to LF without parsing its tail.
  let fragments: Buffer[] = [];
  let size = 0;
  let oversized = false;
  for await (const bytes of process.stdin) {
    let offset = 0;
    for (let end = bytes.indexOf(10); end !== -1; end = bytes.indexOf(10, offset)) {
      const part = bytes.subarray(offset, end); size += part.length;
      if (size > 1048576) oversized = true;
      if (!oversized) fragments.push(part);
      if (oversized) send({ jsonrpc: '2.0', id: null, error: { code: -32602, message: 'Bridge frame exceeds 1 MiB',
        data: { kind: 'INVALID_PARAMS', retryable: false, correlation_id: 'bridge' } } });
      else {
        while (pending.size >= 32) await Promise.race(pending);
        const operation = dispatch(Buffer.concat(fragments)); pending.add(operation);
        void operation.finally(() => pending.delete(operation));
      }
      fragments = []; size = 0; oversized = false; offset = end + 1;
    }
    const tail = bytes.subarray(offset); size += tail.length;
    if (size > 1048576) { oversized = true; fragments = []; }
    else if (!oversized && tail.length) fragments.push(tail);
    if (broken) break;
  }
  await Promise.allSettled(pending);
  await adapter.close({ sandbox_session_id: owner.sandbox_session_id });
  // EOF is shutdown. A permanently unread stdout cannot hold cleanup open.
  for (let attempts = 0; writing && attempts < 50 && !broken; attempts++) await new Promise(r => setTimeout(r, 10));
}

main().then(() => process.exit(process.exitCode ?? 0)).catch(() => {
  process.stderr.write('ForgeCode Bridge startup or shutdown failed\n'); process.exit(2);
});
