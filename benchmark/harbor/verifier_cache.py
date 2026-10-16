"""Cache official TB2 bootstrap bytes without rewriting verifier scripts."""
from hashlib import sha256
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess

VERSION = '0.9.5'
INSTALLER_URL = f'https://astral.sh/uv/{VERSION}/install.sh'
ARCHIVE = 'uv-x86_64-unknown-linux-gnu.tar.gz'
ROOT = '/opt/forgecode-verifier'


def verifier_specs(tasks: Path, taskset: dict) -> list[list[str]]:
    from benchmark.adapters.materialize import resolve_task
    specs = set()
    for name in taskset['tasks']:
        script = resolve_task(tasks, taskset, name) / 'tests/test.sh'
        text = script.read_text() if script.exists() else ''
        if 'astral.sh/uv/' not in text:
            continue
        if f'curl -LsSf {INSTALLER_URL} | sh' not in text:
            raise ValueError(f'Unsupported verifier bootstrap: {name}')
        commands = re.findall(r'^uvx\s+([^\n]+)', text.replace('\\\n', ' '), re.M)
        if len(commands) != 1:
            raise ValueError(f'Verifier preparation requires one uvx invocation: {name}')
        tokens = shlex.split(commands[0])
        if 'pytest' not in tokens:
            raise ValueError(f'Unsupported verifier runner: {name}')
        options = tokens[:tokens.index('pytest')]
        if len(options) % 2 or any(options[i] not in {'-p', '--python', '-w', '--with'}
                                   for i in range(0, len(options), 2)) or any('$' in s for s in options):
            raise ValueError(f'Unresolved verifier dependency options: {name}')
        specs.add(tuple(options))
    return [list(s) for s in sorted(specs)]


def prepare_bootstrap(cache: Path) -> tuple[Path, dict]:
    directory = cache / f'verifier-uv-{VERSION}'
    directory.mkdir(parents=True, exist_ok=True)
    receipt = directory / 'receipt.json'
    urls = {'install.sh': INSTALLER_URL, ARCHIVE:
            f'https://releases.astral.sh/github/uv/releases/download/{VERSION}/{ARCHIVE}'}
    previous = json.loads(receipt.read_text()) if receipt.exists() else {}
    hashes = {}
    for name, url in urls.items():
        path = directory / name
        if not path.exists() or sha256(path.read_bytes()).hexdigest() != previous.get('sha256', {}).get(name):
            pending = path.with_suffix(path.suffix + '.download')
            subprocess.run([shutil.which('curl.exe') or 'curl', '-fLsS', '--retry', '2',
                            '--max-time', '180', url, '-o', str(pending)], check=True)
            pending.replace(path)
        hashes[name] = sha256(path.read_bytes()).hexdigest()
    (directory / 'curl').write_text(
        '#!/bin/sh\nif [ "$#" = 2 ] && [ "$1" = -LsSf ] && [ "$2" = '
        + shlex.quote(INSTALLER_URL) + ' ]; then\n'
        f'  exec cat {ROOT}/install.sh\nfi\nexec /usr/bin/curl "$@"\n', newline='\n')
    metadata = {'version': VERSION, 'urls': urls, 'sha256': hashes}
    receipt.write_text(json.dumps(metadata, indent=2) + '\n')
    return directory, metadata


def warm_dependencies(image: str, cache: Path, specs: list[list[str]]) -> None:
    for options in specs:
        command = (f'mkdir -p /tmp/verifier-uv; tar -xzf {ROOT}/{ARCHIVE} -C /tmp/verifier-uv; '
                   '/tmp/verifier-uv/uv-x86_64-unknown-linux-gnu/uvx '
                   + shlex.join(options + ['pytest', '--version']))
        base = ['docker', 'run', '--rm', '--entrypoint', '/bin/sh',
                '--mount', f'type=bind,source={cache.resolve()},target=/opt/forgecode-cache',
                '-e', 'UV_CACHE_DIR=/opt/forgecode-cache',
                '-e', 'UV_PYTHON_INSTALL_DIR=/opt/forgecode-cache/python',
                '-e', 'UV_HTTP_TIMEOUT=120']
        subprocess.run(base + [image, '-ec', command], check=True)
        subprocess.run(base + ['--network', 'none', '-e', 'UV_OFFLINE=1', image, '-ec', command], check=True)
