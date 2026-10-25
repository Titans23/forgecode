import base64
import copy
import os

import pytest

from forge.protocol import ContractError
from forge.sandbox.path_policy import PathPolicy, inspect_path
from forge.sandbox.workspace_transfer import TransferLimits, WorkspaceSnapshot


def snapshot(root, *, writes=None, limits=None):
    policy = PathPolicy(inspect_path(root, absolute=True), 'strict_allowlist_required',
        (root,), (root,) if writes is None else writes, ())
    return WorkspaceSnapshot(policy, limits=limits)


def record(content, executable=False):
    return {'content': base64.b64encode(content).decode(), 'executable': executable}


def test_authorized_binary_create_update_delete_and_empty_directory_roundtrip(tmp_path):
    (tmp_path / 'old').write_bytes(b'old')
    (tmp_path / 'delete').write_bytes(b'delete')
    state = snapshot(tmp_path)
    tree = state.encode()
    tree['files']['old'] = record(b'new\0binary')
    del tree['files']['delete']
    tree['directories'] = ['new', 'empty']
    tree['files']['new/script'] = record(b'#!/bin/sh\n', True)
    assert set(state.apply(tree)) == {'old', 'delete', 'new', 'empty', 'new/script'}
    assert (tmp_path / 'old').read_bytes() == b'new\0binary'
    assert not (tmp_path / 'delete').exists()
    assert (tmp_path / 'empty').is_dir()
    assert (tmp_path / 'new/script').read_bytes() == b'#!/bin/sh\n'


def test_import_excludes_control_credentials_and_private_configuration(tmp_path):
    for name in ('.git', '.forge', '.ssh', '.aws'):
        (tmp_path / name).mkdir()
        (tmp_path / name / 'private').write_text('synthetic secret')
    for name in ('.env', '.env.local', '.npmrc', '.netrc', 'credentials.json'):
        (tmp_path / name).write_text('synthetic secret')
    (tmp_path / 'code.py').write_text('pass')
    tree = snapshot(tmp_path).encode()
    assert set(tree['files']) == {'code.py'}
    assert tree['directories'] == []


@pytest.mark.parametrize('name', ['../escape', '/escape', 'a/../escape', 'a//b', './escape',
    'a\\b', 'C:/escape', 'NUL', 'file:stream', '.env', '.ssh/key', 'x/.forge/key', 'file.'])
def test_untrusted_manifest_paths_are_rejected_before_any_write(tmp_path, name):
    (tmp_path / 'a.txt').write_bytes(b'original')
    state = snapshot(tmp_path)
    tree = state.encode()
    tree['files']['a.txt'] = record(b'changed')
    tree['files'][name] = record(b'escape')
    with pytest.raises(ContractError):
        state.apply(tree)
    assert (tmp_path / 'a.txt').read_bytes() == b'original'


def test_case_aliases_and_missing_parent_are_rejected(tmp_path):
    state = snapshot(tmp_path)
    for tree in ({'files': {'A': record(b'a'), 'a': record(b'b')}, 'directories': []},
                 {'files': {'missing/a': record(b'a')}, 'directories': []}):
        with pytest.raises(ContractError):
            state.apply(tree)
    assert not list(tmp_path.iterdir())


def test_scope_denial_preflights_the_entire_diff(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/code').write_bytes(b'original')
    (tmp_path / 'outside').write_bytes(b'original')
    state = snapshot(tmp_path, writes=(tmp_path / 'src',))
    tree = state.encode()
    tree['files']['src/code'] = record(b'allowed')
    tree['files']['outside'] = record(b'not allowed')
    with pytest.raises(ContractError, match='authorized roots'):
        state.apply(tree)
    assert (tmp_path / 'src/code').read_bytes() == b'original'
    assert (tmp_path / 'outside').read_bytes() == b'original'


@pytest.mark.parametrize('change', ['edit', 'replace', 'create'])
def test_user_changes_are_preserved_as_stale_outputs(tmp_path, change):
    path = tmp_path / 'code'
    path.write_bytes(b'original')
    state = snapshot(tmp_path)
    tree = state.encode()
    tree['files']['code'] = record(b'agent output')
    if change == 'edit':
        path.write_bytes(b'user edit')
    elif change == 'replace':
        path.unlink()
        path.write_bytes(b'original')
    else:
        (tmp_path / 'new-user-file').write_bytes(b'user edit')
    expected = path.read_bytes()
    with pytest.raises(ContractError, match='changed while'):
        state.apply(tree)
    assert path.read_bytes() == expected


def test_removed_directory_cannot_delete_excluded_credentials(tmp_path):
    (tmp_path / 'folder').mkdir()
    (tmp_path / 'folder/.env').write_bytes(b'private')
    state = snapshot(tmp_path)
    assert state.encode()['directories'] == ['folder']
    with pytest.raises(ContractError, match='protected entries'):
        state.apply({'files': {}, 'directories': []})
    assert (tmp_path / 'folder/.env').read_bytes() == b'private'


def test_hardlinks_are_not_imported(tmp_path):
    (tmp_path / 'a').write_bytes(b'data')
    os.link(tmp_path / 'a', tmp_path / 'b')
    with pytest.raises(ContractError, match='hardlink'):
        snapshot(tmp_path)


def test_excluded_entries_still_count_toward_scan_quota(tmp_path):
    (tmp_path / '.env.one').write_bytes(b'private')
    (tmp_path / '.env.two').write_bytes(b'private')
    with pytest.raises(ContractError, match='entry quota'):
        snapshot(tmp_path, limits=TransferLimits(entries=1))


def test_import_and_export_quotas_reject_without_truncating(tmp_path):
    (tmp_path / 'a').write_bytes(b'12345')
    with pytest.raises(ContractError, match='byte quota'):
        snapshot(tmp_path, limits=TransferLimits(bytes=4))
    state = snapshot(tmp_path, limits=TransferLimits(bytes=5, entries=1))
    tree = state.encode()
    tree['files']['a'] = record(b'123456')
    with pytest.raises(ContractError, match='byte quota'):
        state.apply(tree)
    tree = state.encode()
    tree['files']['b'] = record(b'')
    with pytest.raises(ContractError, match='entry quota'):
        state.apply(tree)
    assert (tmp_path / 'a').read_bytes() == b'12345'


def test_file_directory_type_replacements(tmp_path):
    (tmp_path / 'file').write_bytes(b'file')
    (tmp_path / 'dir').mkdir()
    (tmp_path / 'dir/child').write_bytes(b'child')
    state = snapshot(tmp_path)
    state.apply({'directories': ['file'], 'files': {'dir': record(b'now file'), 'file/child': record(b'now child')}})
    assert (tmp_path / 'dir').read_bytes() == b'now file'
    assert (tmp_path / 'file/child').read_bytes() == b'now child'


def test_partial_io_failure_is_explicit_and_never_claims_atomic_tree(tmp_path, monkeypatch):
    for name in ('a', 'b'):
        (tmp_path / name).write_bytes(b'original')
    state = snapshot(tmp_path)
    tree = copy.deepcopy(state.encode())
    for name in tree['files']:
        tree['files'][name] = record(b'new')
    replace = os.replace
    def fail_second(source, target):
        if target.name == 'b':
            raise OSError('simulated disk failure during publication')
        replace(source, target)
    monkeypatch.setattr(os, 'replace', fail_second)
    with pytest.raises(ContractError, match='incomplete') as error:
        state.apply(tree)
    assert error.value.kind == 'INDETERMINATE'
    assert error.value.applied_paths == ('a',)
    assert (tmp_path / 'a').read_bytes() == b'new'
    assert (tmp_path / 'b').read_bytes() == b'original'
    assert not list(tmp_path.glob('.forge-write-*'))
