"""Run the frozen driver's Linux bootstrap with local release bytes only."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest


pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Official task installer runs in Linux')
SCRIPT = Path(__file__).resolve().parents[2] / 'benchmark/harbor/install_uv.sh'


@pytest.fixture
def bootstrap(tmp_path):
    cache = tmp_path / 'cache'
    cache.mkdir()
    tools = tmp_path / 'tools'
    tools.mkdir()
    log = tmp_path / 'downloads'
    binary = b'#!/bin/sh\necho "uv 0.12.5"\n'
    digest = sha256(binary).hexdigest()
    # Exercise the arm64 branch independently of the CI host's CPU.
    (tools / 'uname').write_text('#!/bin/sh\necho aarch64\n')
    (tools / 'curl').write_text('''#!/bin/bash
echo "${!#}" >> "$DOWNLOADS"
case "$FAIL" in
    tls) if [[ $(wc -l < "$DOWNLOADS") == 1 ]]; then echo 000; exit 35; fi ;;
    forbidden) echo 403; exit 22 ;;
esac
while [[ "$1" != -o ]]; do shift; done
cp "$ARCHIVE" "$2" && echo 200
''')
    for tool in tools.iterdir():
        tool.chmod(0o755)
    archive = tmp_path / 'release.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        member = tarfile.TarInfo('uv-aarch64-unknown-linux-musl/uv')
        member.size = len(binary)
        tar.addfile(member, io.BytesIO(binary))

    def run(task, *, fail='', expected=digest):
        target = tmp_path / task / 'uv'
        target.parent.mkdir()
        env = dict(os.environ, PATH=f'{tools}:{os.environ["PATH"]}', DOWNLOADS=str(log), ARCHIVE=str(archive), FAIL=fail)
        return subprocess.run(['bash', str(SCRIPT), '0.12.5', str(cache), str(target), 'unused-x86-digest', expected],
                              env=env, capture_output=True, text=True, timeout=20)
    return run, cache, log


def test_concurrent_cold_cache_then_offline_and_repair(bootstrap, tmp_path):
    run, cache, log = bootstrap
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, ['first', 'second', 'third', 'fourth']))
    assert all(r.returncode == 0 for r in results), results
    assert log.read_text().splitlines() == [
        'https://github.com/astral-sh/uv/releases/download/0.12.5/uv-aarch64-unknown-linux-musl.tar.gz']
    assert run('offline', fail='forbidden').returncode == 0
    assert subprocess.check_output([tmp_path / 'offline/uv', '--version'], text=True).strip() == 'uv 0.12.5'
    cached, = cache.iterdir()
    cached.write_bytes(b'corrupt')
    assert run('repair').returncode == 0
    assert cached.read_bytes() == (tmp_path / 'first/uv').read_bytes()
    assert len(log.read_text().splitlines()) == 2


def test_retry_tls_and_reject_unverified_or_unavailable_release(bootstrap, tmp_path):
    run, cache, log = bootstrap
    bad = run('bad', fail='tls', expected='0' * 64)
    assert bad.returncode != 0 and 'SHA-256 mismatch' in bad.stderr
    assert len(log.read_text().splitlines()) == 2
    assert not list(cache.iterdir()) and not (tmp_path / 'bad/uv').exists()
    assert run('denied', fail='forbidden').returncode == 22
    assert len(log.read_text().splitlines()) == 3
