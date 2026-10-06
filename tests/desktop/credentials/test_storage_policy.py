"""Protection decisions never treat a Linux fallback or unknown backend as protected."""
from pathlib import Path
import shutil
import subprocess


def test_linux_fallback_unknown_and_unavailable_are_memory_only():
    root = Path(__file__).resolve().parents[3]
    code = r'''import { protectionFor, credentialEnvironment } from './apps/desktop/dist/main/credential_crypto.js';
import assert from 'node:assert/strict';
for (const backend of ['basic_text', 'unknown', 'unexpected-provider']) {
  assert.equal(protectionFor('linux', backend, true).mode, 'memory_only');
}
assert.equal(protectionFor('linux', 'gnome_libsecret', false).mode, 'memory_only');
assert.equal(protectionFor('linux', 'gnome_libsecret', true).mode, 'os_protected');
assert.equal(protectionFor('win32', 'dpapi', true).mode, 'os_protected');
assert.equal(protectionFor('win32', 'dpapi', false).mode, 'memory_only');
assert.deepEqual(credentialEnvironment({DBUS_SESSION_BUS_ADDRESS:'unix:path=/owned/session', XDG_RUNTIME_DIR:'/run/user/1000',
  API_KEY:'synthetic', NODE_OPTIONS:'--require=evil', ELECTRON_RUN_AS_NODE:'1'}),
  {DBUS_SESSION_BUS_ADDRESS:'unix:path=/owned/session',XDG_RUNTIME_DIR:'/run/user/1000'});
console.log('Protection selection passed; no native crypto result inferred.');'''
    result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', code], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
