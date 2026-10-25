from dataclasses import replace

import pytest

from forge.protocol import ContractError
from forge.sandbox.container_inputs import capture_inputs, writable_directories
from forge.sandbox.path_policy import PathPolicy, inspect_path
from forge.sandbox.workspace_transfer import TransferLimits


def policy(root, references=(), writes=()):
    return PathPolicy(inspect_path(root, absolute=True), 'strict_allowlist_required',
        (root, *references), writes, ())


def test_reference_parent_excludes_project_and_protected_inputs(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'code').write_bytes(b'code')
    (tmp_path / 'example').write_bytes(b'reference')
    (tmp_path / '.env').write_bytes(b'synthetic secret')
    state, references = capture_inputs(policy(project, (tmp_path, project)))
    assert set(state.files) == {'code'}
    assert len(references) == 1
    assert set(references[0].files) == {'example'}


@pytest.mark.parametrize('limits', [TransferLimits(bytes=5), TransferLimits(entries=2)])
def test_reference_quotas_are_shared_with_project(tmp_path, limits):
    project, reference = tmp_path / 'project', tmp_path / 'reference'
    project.mkdir()
    reference.mkdir()
    (project / 'code').write_bytes(b'code')
    (reference / 'example').write_bytes(b'data')
    with pytest.raises(ContractError, match='quota'):
        capture_inputs(policy(project, (reference,)), limits)


def test_write_scopes_reduce_nested_roots_and_reject_missing_or_external(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'src/nested').mkdir(parents=True)
    frozen = policy(project, writes=(project / 'src', project / 'src/nested'))
    state, _ = capture_inputs(frozen)
    assert writable_directories(state) == ('src',)
    for root in (project / 'missing', tmp_path):
        state, _ = capture_inputs(replace(frozen, write_roots=(root,)))
        with pytest.raises(ContractError):
            writable_directories(state)


def test_reference_changes_invalidate_original_snapshot(tmp_path):
    project, reference = tmp_path / 'project', tmp_path / 'reference'
    project.mkdir()
    reference.mkdir()
    (reference / 'example').write_bytes(b'original')
    _, references = capture_inputs(policy(project, (reference,)))
    (reference / 'example').write_bytes(b'user edit')
    with pytest.raises(ContractError, match='changed while'):
        references[0].assert_unchanged()
