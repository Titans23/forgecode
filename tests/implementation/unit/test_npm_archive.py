"""A distributable package must carry its runtime and exclude private configuration."""
import io
import tarfile
import json
import zipfile
from hashlib import sha256

import pytest

from scripts.package_cli import check_archive


def archive(tmp_path, additional=()):
    output = tmp_path / 'package.tgz'
    names = ['package/bin/forge.mjs', 'package/lib/install.mjs', 'package/assets/runtime.json',
             'package/assets/constraints.txt', 'package/assets/runtime.whl', 'package/LICENSE',
             'package/THIRD_PARTY_NOTICES.md', 'package/licenses/pi.txt', 'package/licenses/deepseek-harness.txt', *additional]
    with tarfile.open(output, 'w:gz') as package:
        for name in names:
            value = b'archive-layout fixture'
            entry = tarfile.TarInfo(name)
            entry.size = len(value)
            package.addfile(entry, io.BytesIO(value))
    return output


@pytest.mark.parametrize('name', ['package/assets/.env', 'package/lib/.env.production',
                                  'package/lib/.npmrc', 'package/lib/credentials.json'])
def test_private_files_refuse_distribution(tmp_path, name):
    with pytest.raises(ValueError, match='Private'):
        check_archive(archive(tmp_path, [name]), 'runtime.whl')


def test_runtime_payload_is_required(tmp_path):
    output = archive(tmp_path)
    assert check_archive(output, 'runtime.whl') == 9
    with pytest.raises(ValueError, match='incomplete'):
        check_archive(output, 'missing.whl')


@pytest.mark.parametrize(('extra', 'reason'), [
    (None, None),
    ('forge/_sandbox/sandbox_bridge/dist/workspace-runner.js', 'outside its verified inventory'),
    ('extensions/qwen_tool_distillation.py', 'Retired'),
    ('forge/training.py', 'Retired'),
    ('forge/runtime/model_budget.py', 'Retired'),
    ('forge/sandbox/windows_python_compat.py', 'Retired'),
    ('benchmark/analysis/probe_journal_cost.py', 'Retired'),
])
def test_wheel_rejects_retired_source_and_uninventoried_assets(tmp_path, extra, reason):
    from scripts.check_wheel import check
    output = tmp_path / 'fixture.whl'
    prefix = 'forge/_sandbox/'
    payload = b'export {}'
    manifest = {'schema_version': 'forge.bridge.runtime.v1', 'package_lock_sha256': sha256(b'{}').hexdigest(),
                'files': [{'path': 'sandbox_bridge/dist/native-session.js', 'sha256': sha256(payload).hexdigest()}]}
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr(prefix + 'package-lock.json', '{}')
        archive.writestr(prefix + 'sandbox_bridge/runtime-manifest.json', json.dumps(manifest))
        archive.writestr(prefix + 'sandbox_bridge/dist/native-session.js', payload)
        if extra:
            archive.writestr(extra, b'retired code')
    if extra:
        with pytest.raises(ValueError, match=reason):
            check(output)
    else:
        assert check(output)['status'] == 'pass'


def test_bundle_discards_retired_assets_and_preserves_sources(tmp_path, monkeypatch):
    from scripts import build_bridge
    monkeypatch.setattr(build_bridge, 'ROOT', tmp_path)
    (tmp_path / 'package-lock.json').write_text('{}')
    asset = tmp_path / 'sandbox_bridge/dist/current.js'
    asset.parent.mkdir(parents=True)
    asset.write_text('export {}')
    destination = tmp_path / 'forge/_sandbox'
    destination.mkdir(parents=True)
    retired = destination / 'retired.js'
    retired.write_text('obsolete')
    build_bridge.bundle_python({'files': [{'path': 'sandbox_bridge/dist/current.js', 'sha256': build_bridge.digest(asset)}]})
    assert not retired.exists()
    assert asset.read_text() == (destination / 'sandbox_bridge/dist/current.js').read_text() == 'export {}'
    assert (destination / 'node_modules/@forgecode/contracts/index.js').is_file()
