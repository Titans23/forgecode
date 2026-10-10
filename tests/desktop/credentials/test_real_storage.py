"""Actual Electron safeStorage through the owned helper; synthetic keys never use a provider."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


def test_real_current_user_storage_roundtrip_scope_lock_and_delete(tmp_path):
    root = Path(__file__).resolve().parents[3]
    electron = root / 'node_modules/electron/dist' / ('electron.exe' if os.name == 'nt' else 'electron')
    if not electron.is_file() or sys.platform == 'linux' and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        pytest.skip('Actual Electron runtime/display unavailable; native storage is unverified')
    program = tmp_path / 'storage-test.mjs'
    program.write_text(r'''
import assert from 'node:assert/strict';
import { randomBytes, randomUUID } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const [root, directory, electron] = process.argv.slice(2);
const { CredentialCrypto } = await import(pathToFileURL(resolve(root, 'apps/desktop/dist/main/credential_crypto.js')));
const { CredentialBroker } = await import(pathToFileURL(resolve(root, 'apps/desktop/dist/main/credential_broker.js')));
const environment = {};
for (const key of ['SystemRoot','windir','LOCALAPPDATA','HOME','TEMP','TMP','LANG','PATH','DISPLAY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR']) {
  if (process.env[key]) environment[key] = process.env[key];
}
const create = () => new CredentialBroker(resolve(directory, 'blobs'), new CredentialCrypto({ executable: electron,
  arguments: [resolve(root, 'apps/desktop'), '--credential-worker', resolve(directory, 'runtime')], cwd: root, environment }));
const binding = { connection_id: 'conn-' + randomUUID(), revision: 1, provider: 'anthropic', base_url: 'https://example.invalid' };
const secret = 'synthetic-' + randomBytes(32).toString('hex');
const first = create();
try {
  const protection = await first.probe();
  assert.equal(protection.state, 'ready', 'Actual helper must produce a verified result');
  if (process.platform === 'win32') assert.equal(protection.mode, 'os_protected', 'Actual DPAPI must be available');
  const saved = await first.save(binding, secret);
  assert.equal(await first.resolve(binding), secret);
  assert.equal(await first.resolve({ ...binding, base_url: 'https://other.invalid' }), null);
  const second = create();
  try {
    assert.equal(await second.resolve(binding), saved.persisted ? secret : null);
    first.lock(binding.connection_id);
    assert.equal(await first.resolve(binding), null);
    assert.equal(await first.unlock(binding), saved.persisted ? secret : null);
    const blobs = await readdir(resolve(directory, 'blobs'));
    if (!saved.persisted) assert.equal(blobs.length, 0);
    for (const path of blobs) assert.equal((await readFile(resolve(directory, 'blobs', path))).includes(Buffer.from(secret)), false);
    assert.equal(JSON.stringify(first.status()).includes(secret), false);
    await first.remove(binding);
    assert.equal(await first.resolve(binding), null);
    assert.equal((await readdir(resolve(directory, 'blobs'))).length, 0);
    console.log(JSON.stringify({ actual_crypto: true, persisted: saved.persisted, protection: saved.protection.mode,
      restart_checked: true, scope_checked: true, lock_checked: true, plaintext_absent: true, deleted: true }));
  } finally { second.close(); }
} finally { first.close(); }
''', encoding='utf-8')
    result = subprocess.run([shutil.which('node'), str(program), str(root), str(tmp_path), str(electron)],
        cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['actual_crypto'] and report['plaintext_absent'] and report['deleted']
