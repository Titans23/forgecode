/** The synchronous Electron API runs in an owned helper, away from the UI event loop. */
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { isAbsolute } from 'node:path';
import { strictLoads } from '@forgecode/contracts';

export type Protection = { mode: 'os_protected' | 'memory_only'; backend: string; available: boolean };
export function credentialEnvironment(source: NodeJS.ProcessEnv): Record<string, string> {
  const value: Record<string, string> = {};
  for (const key of ['SystemRoot', 'windir', 'LOCALAPPDATA', 'HOME', 'TEMP', 'TMP', 'LANG', 'PATH',
    'DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR']) {
    if (source[key]) value[key] = source[key]!;
  }
  return value;
}
export function protectionFor(platform: string, backend: string, available: boolean): Protection {
  const known = platform === 'win32' ? backend === 'dpapi' : platform === 'linux' &&
    ['gnome_libsecret', 'kwallet', 'kwallet5', 'kwallet6'].includes(backend);
  return { mode: known && available ? 'os_protected' : 'memory_only', backend, available };
}
type CryptoRequest = { action: 'probe' } | { action: 'encrypt' | 'decrypt'; value: string };
export type CryptoResult = { status: 'pass' | 'blocked' | 'unavailable'; protection: Protection; value?: string; reason?: string };
type Runtime = { executable: string; arguments: string[]; cwd: string; environment: Readonly<Record<string, string>> };

export class CredentialCrypto {
  private children = new Set<ChildProcessWithoutNullStreams>();
  constructor(private runtime: Runtime) {
    if (!isAbsolute(runtime.executable) || !isAbsolute(runtime.cwd)) throw new Error('Crypto worker requires fixed Main-owned paths');
  }
  async request(value: CryptoRequest): Promise<CryptoResult> {
    const bytes = Buffer.from(JSON.stringify(value) + '\n');
    if (bytes.length > 65536 || this.children.size >= 2) throw new Error('Credential operation exceeded its bound');
    return new Promise((resolve, reject) => {
      const child = spawn(this.runtime.executable, [...this.runtime.arguments], { cwd: this.runtime.cwd,
        env: { ...this.runtime.environment }, shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
      this.children.add(child);
      const chunks: Buffer[] = [];
      let size = 0;
      const timer = setTimeout(() => { child.kill(); reject(new Error('Credential storage is temporarily unavailable')); }, 10000);
      child.stdout.on('data', (part: Buffer) => {
        size += part.length;
        if (size > 65536) { child.kill(); reject(new Error('Credential response exceeded its bound')); }
        else chunks.push(part);
      });
      child.stderr.on('data', () => { /* Never persist native/provider output. */ });
      child.stdin.on('error', () => { child.kill(); reject(new Error('Credential private pipe failed')); });
      child.once('error', () => { clearTimeout(timer); this.children.delete(child); reject(new Error('Credential helper unavailable')); });
      child.once('close', code => {
        clearTimeout(timer); this.children.delete(child);
        try {
          const result = strictLoads(Buffer.concat(chunks)) as CryptoResult;
          if (code !== 0 || !['pass', 'blocked', 'unavailable'].includes(result.status) ||
              !['os_protected', 'memory_only'].includes(result.protection?.mode) || typeof result.protection.backend !== 'string' ||
              typeof result.protection.available !== 'boolean' || result.value !== undefined && typeof result.value !== 'string') throw new Error();
          resolve(result);
        } catch { reject(new Error('Credential helper returned no verified result')); }
      });
      child.stdin.end(bytes);
    });
  }
  close() { for (const child of this.children) child.kill(); }
}
