/** Main owns protected blobs; Renderer can observe status but cannot recover a key. */
import { mkdir, open, readFile, realpath, rename, unlink, lstat } from 'node:fs/promises';
import { resolve, isAbsolute, relative, sep } from 'node:path';
import { randomUUID } from 'node:crypto';
import { strictLoads } from '@forgecode/contracts';
import type { CredentialCrypto, Protection } from './credential_crypto.js';

export type CredentialBinding = { connection_id: string; revision: number; provider: string; base_url: string };
type Entry = CredentialBinding & { credential: string };
export class CredentialBroker {
  private memory = new Map<string, Entry>();
  private locked = new Set<string>();
  private protection: Protection = { mode: 'memory_only', backend: 'unknown', available: false };
  private state: 'ready' | 'locked' | 'temporarily_unavailable' = 'temporarily_unavailable';
  constructor(private directory: string, private crypto: CredentialCrypto) {
    if (!isAbsolute(directory)) throw new Error('Credential storage needs a fixed private directory');
  }
  async probe() {
    try {
      const value = await this.crypto.request({ action: 'probe' });
      this.protection = value.protection;
      this.state = value.status === 'pass' ? 'ready' : 'temporarily_unavailable';
    } catch { this.state = 'temporarily_unavailable'; }
    return this.status();
  }
  status() { return { ...this.protection, state: this.state, stored_in_memory: this.memory.size }; }
  private validate(binding: CredentialBinding) {
    if (!/^conn-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(binding.connection_id) ||
        !Number.isSafeInteger(binding.revision) || binding.revision < 1 || !binding.provider || !binding.base_url) throw new Error('Invalid credential binding');
  }
  private same(value: CredentialBinding, expected: CredentialBinding) {
    return ['connection_id', 'revision', 'provider', 'base_url'].every(key => value[key as keyof CredentialBinding] === expected[key as keyof CredentialBinding]);
  }
  private async path(binding: CredentialBinding) {
    this.validate(binding);
    await mkdir(this.directory, { recursive: true, mode: 0o700 });
    const root = await realpath(this.directory);
    if (root !== resolve(this.directory) || (await lstat(this.directory)).isSymbolicLink()) throw new Error('Credential root identity changed');
    const path = resolve(root, binding.connection_id + '.json');
    const part = relative(root, path);
    if (part.startsWith('..' + sep) || isAbsolute(part)) throw new Error('Credential path is outside its root');
    return path;
  }
  async save(binding: CredentialBinding, credential: string) {
    this.validate(binding);
    if (!credential.trim() || Buffer.byteLength(credential) > 16384 || /[\r\n\0]/.test(credential)) throw new Error('Invalid credential input');
    await this.probe();
    const entry: Entry = { ...binding, credential };
    this.memory.delete(binding.connection_id);
    this.locked.delete(binding.connection_id);
    const path = await this.path(binding);
    if (this.protection.mode === 'os_protected' && this.state === 'ready') {
      const encrypted = await this.crypto.request({ action: 'encrypt', value: JSON.stringify(entry) });
      if (encrypted.status !== 'pass' || encrypted.protection.mode !== 'os_protected' || !encrypted.value) {
        this.state = 'temporarily_unavailable'; throw new Error('System credential protection is temporarily unavailable');
      }
      const temporary = path + '.' + randomUUID() + '.tmp';
      const file = await open(temporary, 'wx', 0o600);
      try { await file.writeFile(JSON.stringify({ schema_version: 'forge.credential.blob.v1', ...binding, encrypted: encrypted.value }) + '\n'); await file.sync(); }
      finally { await file.close(); }
      try { await rename(temporary, path); } catch { await unlink(temporary).catch(() => {}); throw new Error('Protected credential could not be stored'); }
    } else {
      await unlink(path).catch(error => { if (error.code !== 'ENOENT') throw error; });
    }
    this.memory.set(binding.connection_id, entry);
    return { persisted: this.protection.mode === 'os_protected' && this.state === 'ready', protection: this.status() };
  }
  async resolve(binding: CredentialBinding): Promise<string | null> {
    this.validate(binding);
    if (this.locked.has(binding.connection_id)) return null;
    const memory = this.memory.get(binding.connection_id);
    if (memory) return this.same(memory, binding) ? memory.credential : null;
    const path = await this.path(binding);
    let blob: any;
    try {
      const stat = await lstat(path);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 65536) throw new Error();
      blob = strictLoads(await readFile(path));
    } catch (error: any) {
      if (error.code !== 'ENOENT') this.state = 'temporarily_unavailable';
      return null;
    }
    if (blob.schema_version !== 'forge.credential.blob.v1' || !this.same(blob, binding) || typeof blob.encrypted !== 'string') return null;
    try {
      const decrypted = await this.crypto.request({ action: 'decrypt', value: blob.encrypted });
      if (decrypted.status !== 'pass' || decrypted.protection.mode !== 'os_protected' || !decrypted.value) throw new Error();
      const entry = strictLoads(decrypted.value) as Entry;
      if (!this.same(entry, binding) || typeof entry.credential !== 'string') throw new Error();
      this.memory.set(binding.connection_id, entry);
      this.state = 'ready'; this.protection = decrypted.protection;
      return entry.credential;
    } catch { this.state = 'temporarily_unavailable'; return null; }
  }
  lock(connectionId: string) {
    this.memory.delete(connectionId); this.locked.add(connectionId); this.state = 'locked';
  }
  async unlock(binding: CredentialBinding) { this.locked.delete(binding.connection_id); return this.resolve(binding); }
  async remove(binding: CredentialBinding) {
    this.memory.delete(binding.connection_id); this.locked.delete(binding.connection_id);
    await unlink(await this.path(binding)).catch(error => { if (error.code !== 'ENOENT') throw error; });
  }
  close() { this.memory.clear(); this.crypto.close(); }
}
