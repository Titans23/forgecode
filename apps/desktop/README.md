# ForgeCode desktop development

Windows users can double-click `Start-ForgeCode.cmd` at the repository root.
See the [Chinese desktop quickstart](../../docs/install/desktop-quickstart.md)
for first launch, model connections, project authorization and troubleshooting.
The interface follows the system light/dark preference.

Run `npm run start --workspace @forgecode/desktop` from the repository after
the locked Python/npm dependencies are installed. The command builds the fixed
Main, sandboxed Preload and React assets, then launches Electron. It starts no
development HTTP server. The normal desktop profile uses the real provider
adapter and strict readiness; missing verified native sandbox capability blocks
execution before any model request.

For the genuine offline repair demonstration and window checks, run
`.venv/Scripts/python.exe scripts/impl.py verify --suite desktop`
(Linux: `.venv/bin/python`). The explicit development smoke creates a private
fixture copy, uses scripted model responses with real Harness/tools/unittest,
captures the actual window and confirms owned shutdown. Its report is
`development-desktop`, not native acceptance or a model benchmark result.

`scripts/build_desktop.py --check` compiles and compares inventories without
repairing them. An intentional development rebuild omits `--check`.
The Linux development venv's fixed Python alias may resolve to its trusted
runtime outside the repository, but actual executable bytes must match the
inventory. Installed loading never uses this alias or falls back to Python.

`npm run package --workspace @forgecode/desktop` requires F28's fixed frozen
assets at `.local/desktop-resources/engine` and `release-manifest.json`.
`verify --suite desktop-packaged` records missing assembly as blocked; after
assembly it invokes actual Forge packaging and the installed client's read-only
inspection path. Windows 10/Ubuntu installation, hardened fuses and signing
remain separate release acceptance.
