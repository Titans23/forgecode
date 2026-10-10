"""Private tool distribution tests; these do not claim OS sandbox enforcement."""
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import shutil
import zipfile

import pytest

from forge.release import runtime
from scripts import materialize_release


def test_repository_tool_inventories_match_locked_checkout_bytes():
    root = Path(__file__).resolve().parents[3]
    lock = json.loads((root / 'release-lock.json').read_bytes())
    for bundle in lock['tool_bundles']:
        assert runtime.tool_inventory(root, bundle)


def bundle_fixture(root, files=None):
    files = files or {'pwsh.exe': b'shell fixture', 'runtime.dll': b'required dependency', 'LICENSE.txt': b'license'}
    inventory = {'schema_version': 'forge.tool.inventory.v1', 'files': [
        {'path': path, 'sha256': sha256(content).hexdigest(), 'size_bytes': len(content)}
        for path, content in sorted(files.items())]}
    raw = (json.dumps(inventory, indent=2) + '\n').encode()
    inventory_path = root / 'packaging/tool-inventories/powershell.json'
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_bytes(raw)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    data = stream.getvalue()
    bundle = {'name': 'powershell', 'platform': 'win32-x64', 'version': '1.2.3',
              'path': '.local/release-runtime/powershell-1.2.3-win32-x64', 'entry': 'pwsh.exe',
              'inventory': inventory_path.relative_to(root).as_posix(), 'inventory_sha256': sha256(raw).hexdigest(),
              'source': 'https://example.invalid/pinned.zip', 'archive_sha256': sha256(data).hexdigest(),
              'archive_size': len(data), 'strip_prefix': '', 'license': 'MIT'}
    return bundle, data


def materialize(root, bundle, data, monkeypatch):
    monkeypatch.setattr(materialize_release.urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(data))
    return materialize_release.materialize_tool_bundle(root, bundle)


def test_bundle_restores_all_dependencies_and_refuses_changed_dll(tmp_path, monkeypatch):
    bundle, data = bundle_fixture(tmp_path)
    assert materialize(tmp_path, bundle, data, monkeypatch) is True
    directory = tmp_path / bundle['path']
    assert (directory / 'runtime.dll').read_bytes() == b'required dependency'
    assert runtime.verify_tool_bundle(tmp_path, bundle) == directory / 'pwsh.exe'
    assert materialize(tmp_path, bundle, data, monkeypatch) is False
    (directory / 'runtime.dll').write_bytes(b'local modification')
    with pytest.raises(ValueError, match='integrity'):
        materialize(tmp_path, bundle, data, monkeypatch)
    assert (directory / 'runtime.dll').read_bytes() == b'local modification'


def test_bundle_checks_inventory_and_archive_before_publication(tmp_path, monkeypatch):
    bundle, data = bundle_fixture(tmp_path)
    bad = {**bundle, 'archive_sha256': '0' * 64}
    with pytest.raises(ValueError, match='archive'):
        materialize(tmp_path, bad, data, monkeypatch)
    assert not (tmp_path / bundle['path']).exists()
    (tmp_path / bundle['inventory']).write_text('{}')
    with pytest.raises(ValueError, match='inventory'):
        materialize(tmp_path, bundle, data, monkeypatch)


@pytest.mark.parametrize('name', ['../outside', 'C:/outside', 'folder\\outside', 'pwsh.exe:extra', 'PWSH.EXE'])
def test_bundle_rejects_unsafe_or_case_colliding_archive_members(tmp_path, monkeypatch, name):
    bundle, _ = bundle_fixture(tmp_path)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('pwsh.exe', b'shell fixture')
        archive.writestr(name, b'unexpected')
    data = stream.getvalue()
    bundle.update(archive_sha256=sha256(data).hexdigest(), archive_size=len(data))
    with pytest.raises(ValueError):
        materialize(tmp_path, bundle, data, monkeypatch)
    assert not (tmp_path / bundle['path']).exists()
    assert not (tmp_path / 'outside').exists()


def test_bundle_rejects_extra_dll_and_missing_dependency(tmp_path, monkeypatch):
    bundle, data = bundle_fixture(tmp_path)
    materialize(tmp_path, bundle, data, monkeypatch)
    directory = tmp_path / bundle['path']
    extra = directory / 'unexpected.dll'
    extra.write_bytes(b'unexpected loader input')
    with pytest.raises(ValueError, match='inventory'):
        runtime.verify_tool_bundle(tmp_path, bundle)
    extra.unlink()
    (directory / 'runtime.dll').unlink()
    with pytest.raises(ValueError):
        runtime.verify_tool_bundle(tmp_path, bundle)


def test_node_and_python_accept_and_reject_the_same_tool_directory(tmp_path, monkeypatch):
    bundle, data = bundle_fixture(tmp_path)
    materialize(tmp_path, bundle, data, monkeypatch)
    module = (Path(__file__).resolve().parents[3] / 'packaging/verify-installed.mjs').as_uri()
    script = 'import {verifyToolBundle} from ' + json.dumps(module) + ';console.log(await verifyToolBundle(process.argv[1],JSON.parse(process.argv[2])));'
    argv = [shutil.which('node'), '--input-type=module', '-e', script, str(tmp_path), json.dumps(bundle)]
    result = subprocess.run(argv, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == runtime.verify_tool_bundle(tmp_path, bundle)
    (tmp_path / bundle['path'] / 'runtime.dll').write_bytes(b'changed')
    failed = subprocess.run(argv, capture_output=True, text=True)
    assert failed.returncode != 0 and 'integrity' in failed.stderr


def test_release_assembly_copies_complete_tool_and_license(tmp_path, monkeypatch):
    from scripts.assemble_release import copy_tool_bundles
    bundle, data = bundle_fixture(tmp_path)
    materialize(tmp_path, bundle, data, monkeypatch)
    # This fixture exercises a declared bundle, not a complete Windows release.
    stage = tmp_path / 'stage'
    with pytest.raises(ValueError, match='requires bundled'):
        copy_tool_bundles(tmp_path, stage, {'tool_bundles': [bundle]}, 'win32-x64')
    assert (stage / 'runtimes/powershell/1.2.3/runtime.dll').read_bytes() == b'required dependency'
    assert (stage / 'licenses/native-tools/powershell/LICENSE.txt').read_bytes() == b'license'


def test_installed_tools_reject_uninventoried_dependency(tmp_path):
    from test_release_runtime import release_tree
    manifest = release_tree(tmp_path)
    tool_root = 'runtimes/powershell/1.2.3'
    path = tmp_path / tool_root / 'pwsh.exe'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'shell fixture')
    entry = {'path': path.relative_to(tmp_path).as_posix(), 'sha256': sha256(path.read_bytes()).hexdigest(), 'size_bytes': path.stat().st_size}
    manifest['files'].append(entry)
    dependency = path.parent / 'runtime.dll'
    dependency.write_bytes(b'required dependency')
    manifest['files'].append({'path': dependency.relative_to(tmp_path).as_posix(),
        'sha256': sha256(dependency.read_bytes()).hexdigest(), 'size_bytes': dependency.stat().st_size})
    manifest['tools'] = {'powershell': {'root': tool_root, 'entry': entry}}
    (tmp_path / 'release-manifest.json').write_text(json.dumps(manifest))
    assert runtime.verify_manifest(tmp_path)['tools']['powershell']['entry'] == entry
    assert runtime.verify_installed_tool(tmp_path, manifest, 'powershell') == path
    dependency.write_bytes(b'changed dependency')
    with pytest.raises(ValueError, match='integrity'):
        runtime.verify_manifest(tmp_path)
    with pytest.raises(ValueError, match='integrity'):
        runtime.verify_installed_tool(tmp_path, manifest, 'powershell')
    dependency.write_bytes(b'required dependency')
    (path.parent / 'unexpected.dll').write_bytes(b'untrusted loader input')
    with pytest.raises(ValueError, match='inventory'):
        runtime.verify_manifest(tmp_path)
    with pytest.raises(ValueError, match='inventory'):
        runtime.verify_installed_tool(tmp_path, manifest, 'powershell')
