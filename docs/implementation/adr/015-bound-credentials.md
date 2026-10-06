# ADR 015 — Main-owned credentials and confirmed connections

Accepted for F15. Test parent: e612f360be14dc2a198d65f936b88a7ffd336941.

Main owns a fixed private credential directory inside Engine control data. It uses
the locked Electron safeStorage API in an owned Electron helper with private pipes,
bounded input/output and a ten-second timeout. No key is in argv, environment,
project configuration or diagnostic output. The helper runs the normal app entry;
production does not depend on ELECTRON_RUN_AS_NODE or disabling fuses.

Windows current-user protection was tested with actual DPAPI encrypt/decrypt,
restart, lock, binding mismatch and deletion. Linux only recognizes documented
protected backends when encryption is actually available. basic_text, unknown and
unavailable backends use memory; they never write a plaintext or fallback cipher.
The crypto helper alone receives explicitly allowed Linux desktop session values.
It does not confer those values on Engine, Bridge or task children.

Electron 44.5.1's Windows bootstrap replaces process.stdin with an EOF stream.
The first actual helper test exposed this behavior. The helper reads inherited fd 0
directly; no alternate cipher or fabricated result was introduced. References:
[locked bootstrap](https://raw.githubusercontent.com/electron/electron/v44.5.1/lib/common/init.ts)
and [safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage).

Protected blobs contain a binding and credential inside real system ciphertext.
Main status and connection metadata expose protection/readiness/lock state only.
Renderer's password form clears immediately on submit and has no readback interface.
Named IPC authenticates the current window and frame. All native authorization
dialogs share one Main operation guard; inputs cannot launch generic RPC or paths.

Engine stores connection metadata and keeps credentials only in memory. Credential
scope includes the complete connection revision/configuration. Main-only short-lived
nonces bind saved endpoints and explicit network tests to the exact row and owner
epoch. Editing an endpoint always revokes the previous key; blank input deletes it.
There is no automatic test request. Test requests and all three model SDK adapters
disable redirects. Local HTTP probes verify one confirmed request, durable retry
without another request, and no credential request at a second origin.

Revocation stops new requests and cancels actual running model/test tasks. Slow HTTP
tests are dispatched independently with a limit of sixteen owned requests; ordered
batch members remain ordered. EOF/backpressure cancels owned requests and persists
an honest blocked observation. This was tested with real Engine processes and a
server withholding its response. No language-runtime memory-zeroing claim is made.

Accepted action rows remain immutable. Migration 005, through the existing automatic
backup and transactional migration path, stores connection test observations in a
separate lifecycle table. action.get and durable retries read that observation.
A restarted Engine never claims that a lost memory key was reinstalled by replay.

Final unit 126 / portable 128 / regression 997 passed, zero skips. Actual Windows 10
Electron passed 29 checks. All final evidence uses dirty hash
14ab3eda6e9f1e99bc47402b15f58ea88219b5e748a914cbd27a3ac86a40e4ef.
Windows 11/Ubuntu/actual Linux keyrings, human confirmation choices, installed F28
resources, F24/F27 full exports, administrator setup and paid provider experiments
remain independently blocked or pending. No native isolation or benchmark PASS.
