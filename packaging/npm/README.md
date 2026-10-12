# ForgeCode

To build and install from source, install Git and Node.js 22.13+, then run:

```sh
git clone --branch main https://github.com/Titans23/forgecode.git
cd forgecode
npm run setup
```

This prepares Python and uv, builds the locked sandbox and wheel, and installs the resulting package globally. To update, run `git pull --ff-only` and `npm run setup` again in the source directory. User configuration and sessions are retained. Builds target the current platform; Windows is verified and Ubuntu native validation remains paused.

Install the development package on Windows x64/NTFS:

```sh
npm install -g ./titans23-forgecode-0.1.2.tgz
forge
```

Node.js 22.13+ is required by the native launcher. The installer reuses the Node running this launcher and prepares Python, uv, Git, ripgrep and PowerShell 7. The package includes the prebuilt Python wheel and sandbox assets; no source checkout or manual build is needed. Configure a model in `~/.forge/.env` or the target project `.env`, then run `forge` in that project. Windows does not need WSL or Docker.

The Ubuntu 22.04/24.04 implementation remains unverified; native Linux validation is paused at the user's request. Linux needs system Git and bubblewrap with working user namespaces. Sandbox failure refuses tasks instead of silently granting full host access.

This development package has not been published to the npm registry. The local archive command above works without a registry account.
