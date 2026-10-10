"""Archive fixtures test ownership/hash validation, never native sandbox capability."""
from hashlib import sha256
import io
import json
from pathlib import Path
import stat
import tarfile
import zipfile

import pytest
from scripts.materialize_release import locked_asset,materialize,runtime_member


@pytest.mark.parametrize('target',['win32-x64','linux-x64'])
def test_pinned_runtime_member_checks_both_archive_and_binary(target):
    content=b'explicit archive-parser fixture';stream=io.BytesIO();version='1.2.3'
    member='node-v1.2.3'+('-win-x64/node.exe' if target=='win32-x64' else '-linux-x64/bin/node')
    if target=='win32-x64':
        with zipfile.ZipFile(stream,'w') as bundle:bundle.writestr(member,content)
    else:
        with tarfile.open(fileobj=stream,mode='w:xz') as bundle:
            info=tarfile.TarInfo(member);info.size=len(content);bundle.addfile(info,io.BytesIO(content))
    archive=stream.getvalue();archive_hash=sha256(archive).hexdigest();binary_hash=sha256(content).hexdigest()
    assert runtime_member(archive,version,target,archive_hash,binary_hash)==content
    with pytest.raises(ValueError,match='archive hash'):runtime_member(archive,version,target,'0'*64,binary_hash)
    with pytest.raises(ValueError,match='member hash'):runtime_member(archive,version,target,archive_hash,'0'*64)


def test_zip_runtime_rejects_link_even_with_a_matching_content_hash():
    stream=io.BytesIO();info=zipfile.ZipInfo('node-v1.2.3-win-x64/node.exe')
    info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16;content=b'another/file'
    with zipfile.ZipFile(stream,'w') as bundle:bundle.writestr(info,content)
    archive=stream.getvalue()
    with pytest.raises(ValueError,match='regular member'):
        runtime_member(archive,'1.2.3','win32-x64',sha256(archive).hexdigest(),sha256(content).hexdigest())


def test_tampered_existing_asset_is_never_replaced(tmp_path):
    path=tmp_path/'owned';path.write_bytes(b'original')
    asset={'path':'owned','sha256':sha256(b'original').hexdigest(),'name':'node','platform':'win32-x64'}
    assert locked_asset(tmp_path,asset)==path
    path.write_bytes(b'user modification')
    with pytest.raises(ValueError):locked_asset(tmp_path,asset)
    assert path.read_bytes()==b'user modification'


def test_asset_cannot_reference_a_parent_outside_the_owned_root(tmp_path):
    root=tmp_path/'root';root.mkdir();outside=tmp_path/'outside';outside.write_bytes(b'fixture')
    with pytest.raises(ValueError,match='ownership'):
        locked_asset(root,{'path':'../outside','sha256':sha256(b'fixture').hexdigest()})


def test_actual_current_platform_assets_and_node_leave_release_lock_unchanged():
    root=Path(__file__).resolve().parents[3];before=(root/'release-lock.json').read_bytes()
    report=materialize(root)
    assert report['status']=='pass' and report['public_model_calls']==0
    assert report['eligible_for_native_pass'] is False
    assert report['node']=='v'+json.loads(before)['node']['version']
    assert (root/'release-lock.json').read_bytes()==before
