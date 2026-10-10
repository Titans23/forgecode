/** One fixed private pipe operation. No BrowserWindow, Engine, network or raw log. */
import { app, safeStorage } from 'electron';
import { isAbsolute } from 'node:path';
import { mkdir } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import { strictLoads } from '@forgecode/contracts';
import { protectionFor, type CryptoResult } from './credential_crypto.js';

export async function runCredentialWorker(dataDir: string) {
  const unavailable = (): CryptoResult => ({ status: 'unavailable', protection: protectionFor(process.platform, 'unknown', false) });
  let result = unavailable();
  let phase = 'configure';
  let size = 0;
  try {
    if (!isAbsolute(dataDir)) throw new Error('Fixed crypto data directory is missing');
    await mkdir(dataDir, { recursive: true, mode: 0o700 });
    app.setPath('userData', dataDir);
    phase = 'initialize';
    await app.whenReady();
    phase = 'input';
    const chunks: Buffer[] = [];
    // Electron 44 Windows replaces process.stdin with EOF. Read the inherited
    // descriptor directly; credentials still travel only through the owned pipe.
    for await (const value of createReadStream('', { fd: 0, autoClose: false })) {
      const bytes = Buffer.from(value);
      size += bytes.length;
      if (size > 65536) throw new Error('Credential frame too large');
      chunks.push(bytes);
    }
    phase = 'parse';
    const request = strictLoads(Buffer.concat(chunks)) as any;
    phase = 'validate';
    if (!request || typeof request !== 'object' || !['probe', 'encrypt', 'decrypt'].includes(request.action) ||
        Object.keys(request).some(key => !['action', 'value'].includes(key)) ||
        (request.action === 'probe' ? request.value !== undefined : typeof request.value !== 'string' || Buffer.byteLength(request.value) > 48000)) throw new Error();
    phase = 'probe';
    const backend = process.platform === 'win32' ? 'dpapi' : process.platform === 'linux' ? safeStorage.getSelectedStorageBackend() : 'unsupported';
    const protection = protectionFor(process.platform, backend, safeStorage.isEncryptionAvailable());
    result = { status: 'pass', protection };
    if (request.action !== 'probe') {
      phase = 'cryptography';
      if (protection.mode !== 'os_protected') result = { status: 'blocked', protection };
      else if (request.action === 'encrypt') result.value = safeStorage.encryptString(request.value).toString('base64');
      else {
        if (!/^[A-Za-z0-9+/]+={0,2}$/.test(request.value)) throw new Error();
        result.value = safeStorage.decryptString(Buffer.from(request.value, 'base64'));
      }
    }
  } catch { result = { ...unavailable(), reason: phase + ':' + size }; }
  process.stdout.write(JSON.stringify(result) + '\n', () => app.exit(0));
}
