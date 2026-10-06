# ADR 013 — Fixed desktop boundary and independently owned Engine

Accepted for F13. Parent source commit: 8875bfbaf9c14cce9834cb1e93f596a1055403b8.

The desktop owns one actual Python Engine over private stdio. Main verifies the
fixed development inventory or installed frozen manifest before spawning; the
two loaders never fall back to one another. Development's fixed Linux venv alias
may resolve outside the source checkout, with actual executable hash verification.
Installed dependencies must remain inside their verified resource root.

BrowserWindow explicitly enables isolation, sandbox and web security, disables
Node integration and loads only hash-verified `forge-app://ui/` assets. The
sandboxed Preload exposes named operations. Main validates the current window,
webContents, main frame, exact origin and payload. Permission requests, webviews,
new windows and remote navigation are denied. UI uses actual Engine snapshots
and events through DesktopTransport; no default MockTransport or HTTP server.
The launcher builds fixed assets and directly opens Electron, without Forge's
development web server. This follows Electron's documented
[security boundaries](https://www.electronjs.org/docs/latest/tutorial/security).

Main continuously drains bounded Engine stdout/stderr independently of the UI.
Protocol corruption, missing action responses and Engine exit never cause an
automatic replay or respawn. Renderer reload and actual crash reconstruct the
same window query state while keeping the owned Engine and turn. The current
bounded notification queue explicitly reports an overflow gap; durable business
facts remain in Engine. Broader history/reducer recovery belongs to F16/F25.

Shutdown pages workspaces and their sessions, cancels owned active work when
requested, reads persisted lifecycle cleanup facts, then requests shutdown and
waits for the owned Engine exit. Engine exit confirmation and cleanup confirmation
are separate. `sandbox.cleanup_status` preserves unknown/residual observations;
no SDK Job/ACL cleanup guarantee is inferred from process exit. Main stores a
small shutdown observation; a failed write does not hang exit.

Actual Windows 10 Electron smoke ran the read/test-fail/patch/test-pass demo,
reload, renderer crash recovery, a second same-profile instance, installation
obsolete handling, runtime sandbox flags, OS TCP listener ownership and shutdown.
The offline model origin is scripted and tools are explicitly local-trusted;
these are genuine development checks, not native isolation or benchmark grades.

Forge's actual prePackage hook refused absent F28 frozen resources. The installed
read-only inspection path and verifier are defined, but cannot be accepted before
that assembly exists. Windows 11/Ubuntu, X11/Wayland, both active-close native
dialog choices, signed/hardened installation and paid provider experiments remain
independently blocked. No installation, administrator setup or model expenditure
was performed.
