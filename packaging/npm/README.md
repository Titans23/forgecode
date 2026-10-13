# ForgeCode

To build and install from source, install Git and Node.js 22.13+, then run:

```sh
git clone --branch main https://github.com/Titans23/forgecode.git
cd forgecode
npm run setup
```

This prepares Python and uv, builds the locked sandbox and wheel, and installs the resulting package globally. To update, run `git pull --ff-only` and `npm run setup` again in the source directory. User configuration and sessions are retained. Builds target the current platform.

Install the development package on Windows x64/NTFS:

```sh
npm install -g ./titans23-forgecode-0.1.2.tgz
forge
```

Node.js 22.13+ is required by the native launcher. The installer reuses the Node running this launcher and prepares Python, uv, Git, ripgrep and PowerShell 7. The package includes the prebuilt Python wheel and sandbox assets; no source checkout or manual build is needed. Installation creates `~/.forge/.env` if absent, preserving any existing file. Run `forge config setup` once, then run `forge` in your target project. Windows does not need WSL or Docker.

Use `forge config setup --from-env .env` to import only model settings, or `forge config set model MODEL`, `forge config set base-url URL` and `forge config set api-key` to update them individually. API keys are entered at a hidden prompt, never displayed by the check command. `forge config path` shows the user file and `forge config` checks effective settings without contacting a model. Process variables and the target project's `.env` override user settings.

Windows 10 and WSL Ubuntu 26.04 have native installation coverage; Ubuntu 22.04/24.04 native acceptance is pending. Linux needs system Git and bubblewrap with working user namespaces. Sandbox failure refuses tasks instead of silently granting full host access.

This development package has not been published to the npm registry. The local archive command above works without a registry account.
