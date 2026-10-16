"""Prepare immutable installed-agent images before starting paid trials."""
from __future__ import annotations

from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import tomllib
from urllib.request import urlopen
import zipfile

from benchmark.harbor.snapshot import verify_frozen_source


UV_VERSION = '0.12.5'
PYTHON_VERSION = '3.12.13'
SOURCE_LABEL = 'org.forgecode.source-sha256'
BASE_LABEL = 'org.forgecode.base-image'
CACHE_LABEL = 'org.forgecode.verifier-cache'


def installed_check(digest: str) -> str:
    return (
        'set -eu; test "$(cat /opt/forgecode-agent/source-sha256)" = ' + shlex.quote(digest) + '; '
        'git --version; curl --version >/dev/null; '
        '/opt/forgecode-agent/bin/uv --version; '
        '/opt/forgecode-agent/.venv/bin/python -I -c '
        + shlex.quote('import forge.launch, benchmark.harbor.run_forge, cryptography.hazmat.bindings._rust; '
                     'import sys; assert sys.version_info[:3] == (3, 12, 13)')
    )


def image_info(name: str) -> dict:
    # Do not read container environment variables: they can contain credentials.
    value = subprocess.run(['docker', 'image', 'inspect', name, '--format',
        '{"id":{{json .Id}},"config":{{json .Config}}}'],
        check=True, capture_output=True, text=True, timeout=30)
    return json.loads(value.stdout)


def task_images(tasks: Path, taskset: dict) -> set[str]:
    from benchmark.adapters.materialize import resolve_task
    images = set()
    for name in taskset['tasks']:
        task = resolve_task(tasks, taskset, name)
        config = tomllib.loads((task / 'task.toml').read_text())
        image = config.get('environment', {}).get('docker_image')
        if not image:
            raise ValueError(f'Prepared installation requires an official prebuilt image: {name}')
        images.add(image)
    return images


def validate_prepared(manifest: dict, digest: str, images: set[str]) -> dict:
    if manifest.get('schema_version') != 1 or manifest.get('source_sha256') != digest:
        raise ValueError('Prepared images do not match frozen source')
    entries = manifest.get('images', {})
    if not images.issubset(entries):
        raise ValueError('Prepared image inventory does not cover all selected tasks')
    for original in sorted(images):
        entry = entries[original]
        prepared = image_info(entry['image_id'])
        labels = prepared['config'].get('Labels') or {}
        if (image_info(original)['id'] != entry['base_id'] or prepared['id'] != entry['image_id']
                or labels.get(SOURCE_LABEL) != digest or labels.get(BASE_LABEL) != entry['base_id']
                or (manifest.get('verifier_cache_image_id')
                    and labels.get(CACHE_LABEL) != manifest['verifier_cache_image_id'])):
            raise ValueError(f'Prepared image provenance mismatch: {original}')
    return {original: entries[original]['image_id'] for original in sorted(images)}


def _uv_binary(cache: Path) -> Path:
    """Fetch once from the official uv PyPI release, verify the wheel hash."""
    target = cache / f'uv-{UV_VERSION}-linux-x86_64'
    receipt = target.with_suffix('.json')
    if target.is_file() and receipt.is_file():
        if sha256(target.read_bytes()).hexdigest() == json.loads(receipt.read_text())['binary_sha256']:
            return target
    with urlopen(f'https://pypi.org/pypi/uv/{UV_VERSION}/json', timeout=120) as response:
        metadata = json.load(response)
    release = next(item for item in metadata['urls']
                   if 'manylinux_2_17_x86_64' in item['filename'] and item['filename'].endswith('.whl'))
    with urlopen(release['url'], timeout=120) as response:
        wheel = response.read()
    if sha256(wheel).hexdigest() != release['digests']['sha256']:
        raise ValueError('Official uv wheel digest mismatch')
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        binary = next(name for name in archive.namelist() if name.endswith('/scripts/uv'))
        target.write_bytes(archive.read(binary))
    target.chmod(0o755)
    receipt.write_text(json.dumps({'url': release['url'], 'wheel_sha256': release['digests']['sha256'],
                                  'binary_sha256': sha256(target.read_bytes()).hexdigest()}))
    return target


def _prerequisites(os_release: str = '/etc/os-release') -> str:
    # Official TB2 images are Debian/Ubuntu. HTTPS avoids proxy failures seen
    # with their HTTP sources; the separate archive cache survives docker-clean.
    return (
        'set -eu; if ! command -v git >/dev/null || ! command -v curl >/dev/null; then '
        "find /etc/apt -type f \\( -name '*.list' -o -name '*.sources' \\) "
        "-exec sed -i 's|http://|https://|g' {} +; "
        # Bullseye's signed security index is still served by deb.debian.org,
        # but its package files moved to Debian's archive after LTS ended.
        f'if [ -r {shlex.quote(os_release)} ]; then . {shlex.quote(os_release)}; '
        'if [ "${ID:-}" = debian ] && [ "${VERSION_CODENAME:-}" = bullseye ]; then '
        "find /etc/apt -type f \\( -name '*.list' -o -name '*.sources' \\) "
        "-exec sed -i 's|https://deb.debian.org/debian-security|https://archive.debian.org/debian-security|g;"
        "s|https://security.debian.org/debian-security|https://archive.debian.org/debian-security|g' {} +; "
        'fi; fi; '
        'apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=60 '
        '-o Acquire::https::CaInfo=/opt/forgecode-preparation-ca.pem '
        '-o Acquire::PDiffs=false -o APT::Update::Error-Mode=any update; '
        'mkdir -p /opt/forgecode-apt/archives/partial; '
        'DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=3 '
        '-o Acquire::https::Timeout=60 -o Acquire::https::CaInfo=/opt/forgecode-preparation-ca.pem '
        '-o Dir::Cache::archives=/opt/forgecode-apt/archives '
        'install -y --no-install-recommends ca-certificates curl git; '
        'rm -rf /var/lib/apt/lists/*; fi'
    )


def prepare_images(source: Path, source_manifest: dict, images: set[str], cache: Path, output: Path,
                   *, verifier_dependencies: list[list[str]] | None = None) -> dict:
    verify_frozen_source(source, source_manifest)
    digest = source_manifest['content_sha256']
    cache.mkdir(parents=True, exist_ok=True)
    uv = _uv_binary(cache)
    from benchmark.harbor.verifier_cache import prepare_bootstrap, warm_dependencies, freeze_cache_layer
    bootstrap, bootstrap_receipt = prepare_bootstrap(cache)
    bases = {name: image_info(name) for name in sorted(images)}
    if not bases:
        raise ValueError('No task images selected')
    for base in bases.values():
        base['local_tag'] = 'forgecode-evaluation-base:' + base['id'].removeprefix('sha256:')
        subprocess.run(['docker', 'tag', base['id'], base['local_tag']], check=True)
    manifest = {'schema_version': 1, 'source_sha256': digest, 'uv_version': UV_VERSION,
                'python_version': PYTHON_VERSION, 'wheel_platform': 'x86_64-manylinux_2_28',
                'uv_binary_sha256': sha256(uv.read_bytes()).hexdigest(),
                'images': {}, 'verifier_bootstrap': bootstrap_receipt,
                'verifier_dependencies': verifier_dependencies or []}
    runtime = f'forgecode-evaluation-runtime:{digest[:24]}'
    with tempfile.TemporaryDirectory(prefix='prepare-', dir=cache) as temporary:
        context = Path(temporary)
        shutil.copytree(source, context / 'source')
        shutil.copy2(uv, context / 'uv')
        shutil.copytree(bootstrap, context / 'verifier')
        (context / 'source-sha256').write_text(digest + '\n')
        dockerfile = context / 'Dockerfile'
        dockerfile.write_text('\n'.join([
            'FROM ' + next(iter(bases.values()))['local_tag'], 'USER root',
            'COPY --chmod=755 uv /opt/forgecode-agent/bin/uv',
            'COPY verifier /opt/forgecode-verifier',
            'COPY --chmod=755 verifier/curl /opt/forgecode-verifier/bin/curl',
            'COPY source /installed-agent/forgecode-src',
            'COPY source-sha256 /opt/forgecode-agent/source-sha256',
            'RUN --mount=type=cache,id=forgecode-uv,target=/opt/forgecode-cache '
            'export UV_HTTP_TIMEOUT=120 UV_HTTP_RETRIES=5 UV_CACHE_DIR=/opt/forgecode-cache '
            'UV_PYTHON_INSTALL_DIR=/opt/forgecode-python UV_PROJECT_ENVIRONMENT=/opt/forgecode-agent/.venv; '
            f'UV_PYTHON_INSTALL_DIR=/opt/forgecode-cache/python /opt/forgecode-agent/bin/uv python install {PYTHON_VERSION} && '
            'mkdir -p /opt/forgecode-python && '
            f'cp -a /opt/forgecode-cache/python/cpython-{PYTHON_VERSION}-* /opt/forgecode-python/ && '
            '/opt/forgecode-agent/bin/uv sync --frozen --no-dev --no-editable '
            '--python-platform x86_64-manylinux_2_28 '
            f'--python {PYTHON_VERSION} --project /installed-agent/forgecode-src',
        ]) + '\n')
        subprocess.run(['docker', 'build', '--progress=plain', '-t', runtime, str(context)], check=True)
        runtime_id = image_info(runtime)['id']
        manifest['runtime_image_id'] = runtime_id
        warm_dependencies(runtime, cache, verifier_dependencies or [])
        cache_image = freeze_cache_layer(runtime, cache)
        manifest['verifier_cache_image_id'] = image_info(cache_image)['id']
        # Small final context: task images copy the already-installed runtime.
        shutil.rmtree(context / 'source')
        logs = output.parent / 'image-builds'
        logs.mkdir(exist_ok=True)
        def build_image(item):
            original, base = item
            key = sha256((base['id'] + digest + runtime_id + manifest['verifier_cache_image_id']).encode()).hexdigest()[:24]
            tag = 'forgecode-evaluation-prepared:' + key
            recipe = context / (key + '.Dockerfile')
            recipe.write_text('\n'.join([
                'FROM ' + base['local_tag'], 'USER root',
                f'COPY --from={runtime} /opt/forgecode-agent/.venv/lib/python3.12/site-packages/certifi/cacert.pem /opt/forgecode-preparation-ca.pem',
                f'RUN --mount=type=cache,id=forgecode-apt-{base["id"][7:23]},target=/opt/forgecode-apt,sharing=locked '
                + _prerequisites(),
                f'COPY --link --from={runtime} /opt/forgecode-agent /opt/forgecode-agent',
                f'COPY --link --from={runtime} /opt/forgecode-python /opt/forgecode-python',
                f'COPY --link --from={runtime} /opt/forgecode-verifier /opt/forgecode-verifier',
                f'COPY --link --from={cache_image} /opt/forgecode-verifier/cache /opt/forgecode-cache',
                'USER ' + (base['config'].get('User') or 'root'),
                'RUN --network=none ' + installed_check(digest),
                f'LABEL {SOURCE_LABEL}="{digest}" {BASE_LABEL}="{base["id"]}" '
                f'{CACHE_LABEL}="{manifest["verifier_cache_image_id"]}"',
            ]) + '\n')
            log = logs / (key + '.log')
            with log.open('w') as stream:
                result = subprocess.run(['docker', 'build', '--progress=plain', '-f', str(recipe),
                                         '-t', tag, str(context)], stdout=stream, stderr=subprocess.STDOUT)
            if result.returncode:
                raise RuntimeError(f'Image preparation failed for {original}; see {log}')
            return original, {'base_id': base['id'], 'image_id': image_info(tag)['id']}
        # Keep four builds busy; stop queued builds on failure or interruption.
        pool = ThreadPoolExecutor(max_workers=4)
        try:
            for future in as_completed([pool.submit(build_image, item) for item in bases.items()]):
                original, entry = future.result()
                manifest['images'][original] = entry
                output.write_text(json.dumps(manifest, indent=2) + '\n')
                print(f'Prepared {len(manifest["images"])}/{len(bases)}: {original}', flush=True)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
    validate_prepared(manifest, digest, images)
    return manifest
