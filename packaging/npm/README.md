# ForgeCode 1.0.0

ForgeCode is a lightweight extensible Python Agent Harness. This package supplies
the default coding CLI, Python runtime and optional native execution assets.

Install Git and Node.js 22.13+, then build for the current Windows x64 or Linux x64 host:

```sh
git clone --branch v1.0.0 https://github.com/Titans23/forgecode.git
cd forgecode
npm run setup
forge --version
forge config setup
```

An already built archive can be installed without a source checkout:

```sh
npm install -g ./titans23-forgecode-1.0.0.tgz
forge
```

The installer prepares Python, uv and required command tools, verifies downloaded
assets, and preserves existing configuration and sessions. Model settings live
in `~/.forge/.env`; process variables and project `.env` values take precedence.
API keys are entered at hidden prompts. `forge config` checks settings without
contacting a model. Installation does not require Docker or WSL.

Ordinary coding defaults to host access (`danger-full-access` + `never`). Select
`forge --sandbox workspace-write` or `/permission` for a confined mode. Native
commands require the selected platform mechanism; failure does not silently grant
host access. Windows DSH grants can modify directory ACLs when explicitly using
native confinement. Complete OS isolation is not claimed.

Use `Ctrl+O` for reasoning/tool details, `/resume` for recent sessions and `/jobs`
for background commands. See the [usage guide](https://github.com/Titans23/forgecode/blob/v1.0.0/docs/install/terminal-quickstart.md)
for SDK, plugins, benchmark and trajectory exports. Registry publication is
separate from source tags and local archive installation.
