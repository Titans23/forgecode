# ADR 014 — Exact grants across the Main / Engine boundary

Accepted for F14. Test parent commit: c094171f9ce175d8914a777110a3975346474a2e.

Preload exposes named operations. Main authenticates the current BrowserWindow,
webContents, main frame, exact app URL and authoritative payload schema. Native
dialogs capture that identity and recheck it after each asynchronous boundary.
Renderer supplies an ID only for approval; it never submits a decision, token,
directory path, raw method, command or credential read request.

Engine persists immutable pending approval details before waiting for permission.
The context contains the final validated tool arguments, actual workspace tracker,
policy and environment epochs. The normal Harness permission callback is preserved;
an internal ContextVar supplies the exact execution context without changing its
public signature. A pending tool leaves the RPC reader and event pump responsive.
The dispatcher now awaits asynchronous short read/decision handlers as needed.

Main re-queries trusted approval details, presents a native deny/once dialog,
re-queries after the choice and obtains a short-lived Main-only nonce. Engine checks
owner epoch, exact binding, expiry, final arguments, workspace observation and trust,
policy, turn state and environment, then consumes the nonce and decision with CAS.
The grant is consumed again at the Harness boundary before execution. No session or
project grant is added. Durable retries return the accepted result without another
tool dispatch. Cancel/timeout expire the pending grant; restart loses live nonces and
requires reconciliation of the previous execution.

Main's native directory chooser creates an opaque one-use selection nonce; Engine
registers the actual canonical directory with inspect-only trust. Execution trust is
a separate native confirmation bound to file identity and database revision. The
private Main channel authenticates the selection issuer; Renderer cannot construct
a registration call. No project Hook, MCP or activation script runs at registration.

Migration 004 adds immutable binding and consumed nonce tables. Existing migration
backup/transaction behavior remains authoritative. Two ephemeral prepare methods
extend the shared catalog; workspace.authorize replaces its previously unused
contract-only payload. Authoritative generation rejects duplicate definitions.

Real Engine/Harness tests cover actual deletion, changed files and final parameters,
forged/expired/replayed grants, denial, cancellation, reader responsiveness and a
killed/restarted Engine. Actual Electron tests cover the sandboxed preload, foreign
window IPC, iframe exposure, malformed IDs, CSP eval, traversal and project HTML,
blocked data navigation and new windows. None simulate a successful native dialog.

Windows 10 development passed 21 GUI checks and 976 regression tests, zero skips.
Windows 11/Ubuntu, both human native choices, hardened installed assets and live
provider experiments remain separately blocked. The existing fixed Windows setup
broker remains available for F16 settings wiring; no administrator action was run.
