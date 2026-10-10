"""Policy compilation and actual local path identities; no OS sandbox simulated."""
from copy import deepcopy
import os
from pathlib import Path
import sys

import pytest

from forge.application.models import ContractError, canonical_hash
from forge.engine.persistence import new_id
from forge.sandbox.capabilities import CapabilityReport, unavailable_report
from forge.sandbox.path_policy import PathPolicy, validate_path_syntax
from forge.sandbox.policy import compile_policy


def policy_input(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'value.txt').write_text('initial', encoding='utf-8')
    identity = root.stat()
    workspace = {'id': new_id('ws'), 'canonical_path': str(root), 'file_identity': f'{identity.st_dev}:{identity.st_ino}'}
    policy = {'schema_version': 'forge.sandbox.policy.v1', 'policy_id': new_id('policy'), 'workspace_id': workspace['id'],
        'filesystem': {'read_mode': 'backend_default_with_protected_paths', 'read_roots': [str(root)],
            'write_roots': [str(root)], 'protected_paths': [], 'deny_overrides_allow': True, 'reject_unsafe_links': True},
        'network': {'mode': 'deny_direct', 'allowed_domains': [], 'dns_isolation_required': False},
        'limits': {'memory_bytes': None, 'disk_bytes': None, 'pids': None, 'wall_time_seconds': 30,
            'command_output_bytes': 1048576, 'session_artifact_bytes': 104857600},
        'environment_keys': [], 'fallback': 'deny', 'session_mutation': 'replace_session'}
    return root, workspace, policy


def declared_capabilities():
    # Pure compiler input, not a native capability measurement or FakeSandbox.
    value = unavailable_report(platform='linux-native', backend_version='0.0.78').value
    value.update(read_isolation='protected_paths', write_isolation=True, direct_network_isolation=True,
        socket_isolation=True, process_cleanup=True, readiness='ready')
    for name in ('read_isolation', 'write_isolation', 'direct_network_isolation', 'socket_isolation', 'process_cleanup'):
        value['verification'][name] = {'status': 'verified', 'evidence_refs': ['unit-policy-input-only']}
    return CapabilityReport(value)


def test_write_limit_does_not_become_read_allowlist_and_hash_is_frozen(tmp_path):
    root, workspace, policy = policy_input(tmp_path)
    snapshot = compile_policy(policy, workspace)
    declared_capabilities().require(snapshot.value)
    assert snapshot.value['filesystem']['write_roots'] == [str(root)]
    assert snapshot.value['filesystem']['read_mode'] == 'backend_default_with_protected_paths'
    assert snapshot.sha256 == canonical_hash(snapshot.value)
    policy['filesystem']['write_roots'].clear()
    copy = snapshot.value
    copy['filesystem']['write_roots'].clear()
    assert snapshot.value['filesystem']['write_roots'] == [str(root)]


@pytest.mark.parametrize('requirement', ['dns', 'strict_read', 'memory', 'disk', 'pids'])
def test_stronger_requirements_fail_before_backend_start(tmp_path, requirement):
    _, workspace, policy = policy_input(tmp_path)
    if requirement == 'dns':
        policy['network']['dns_isolation_required'] = True
    elif requirement == 'strict_read':
        policy['filesystem']['read_mode'] = 'strict_allowlist_required'
    else:
        key = 'pids' if requirement == 'pids' else requirement + '_bytes'
        policy['limits'][key] = {'value': 1024, 'enforcement': 'hard_required'}
    snapshot = compile_policy(policy, workspace)
    with pytest.raises(ContractError) as error:
        declared_capabilities().require(snapshot.value)
    assert error.value.kind == 'CAPABILITY_UNSATISFIED'


def test_workspace_backend_refuses_strict_read(tmp_path):
    from forge.sandbox.workspace_backend import require_workspace_policy
    _, _, policy = policy_input(tmp_path)
    policy['filesystem']['read_mode'] = 'strict_allowlist_required'
    with pytest.raises(ContractError, match='read/network/hard-resource'):
        require_workspace_policy(policy)


def test_unverified_capability_booleans_cannot_authorize_execution(tmp_path):
    _, workspace, policy = policy_input(tmp_path)
    caps = declared_capabilities().value
    caps.pop('verification')
    with pytest.raises(ContractError, match='verified'):
        CapabilityReport(caps).require(compile_policy(policy, workspace).value)
    caps = declared_capabilities().value
    caps['verification']['write_isolation'] = {'status': 'partial', 'evidence_refs': ['partial-input']}
    with pytest.raises(ContractError):
        CapabilityReport(caps).require(compile_policy(policy, workspace).value)


def test_unavailable_report_truthfully_keeps_all_native_features_unsupported():
    report = unavailable_report()
    assert report.value['readiness'] == 'unavailable'
    assert all(item['status'] == 'unsupported' and item['evidence_refs'] == [] for item in report.value['verification'].values())


def test_workspace_backend_cannot_discard_a_hard_resource_requirement(tmp_path):
    from forge.sandbox.workspace_backend import require_workspace_policy
    _, _, policy = policy_input(tmp_path)
    policy['filesystem']['read_mode'] = 'host_default'
    policy['network']['mode'] = 'inherit'
    policy['limits']['memory_bytes'] = {'value': 1024, 'enforcement': 'hard_required'}
    with pytest.raises(ContractError, match='hard-resource'):
        require_workspace_policy(policy)


def test_best_effort_does_not_claim_hard_resource_enforcement(tmp_path):
    _, workspace, policy = policy_input(tmp_path)
    policy['limits']['memory_bytes'] = {'value': 1024, 'enforcement': 'best_effort'}
    assert declared_capabilities().require(compile_policy(policy, workspace).value)['memory'] == 'unavailable'


@pytest.mark.parametrize('name', ['NODE_OPTIONS', 'PYTHONPATH', 'LD_PRELOAD', 'ANTHROPIC_API_KEY', 'MY_SECRET', 'ELECTRON_RUN_AS_NODE'])
def test_control_and_credential_environment_cannot_be_allowlisted(tmp_path, name):
    _, workspace, policy = policy_input(tmp_path)
    policy['environment_keys'] = [name]
    with pytest.raises(ContractError, match='environment'):
        compile_policy(policy, workspace)


def test_domain_normalization_and_budget_change_affect_hash(tmp_path):
    _, workspace, policy = policy_input(tmp_path)
    policy['network'].update(mode='allowlist', allowed_domains=['EXAMPLE.COM', 'xn--fiqs8s.cn'])
    first = compile_policy(policy, workspace)
    assert first.value['network']['allowed_domains'] == ['example.com', 'xn--fiqs8s.cn']
    changed = deepcopy(policy)
    changed['limits']['wall_time_seconds'] += 1
    assert compile_policy(changed, workspace).sha256 != first.sha256


@pytest.mark.parametrize('domain', ['https://example.com', 'user@example.com', '127.0.0.1', 'localhost', '*.example.com', 'example.com:443', 'example.com/path'])
def test_ambiguous_private_or_wildcard_domain_rules_are_refused(tmp_path, domain):
    _, workspace, policy = policy_input(tmp_path)
    policy['network'].update(mode='allowlist', allowed_domains=[domain])
    with pytest.raises(ContractError):
        compile_policy(policy, workspace)


@pytest.mark.parametrize('raw', [r'\\server\share\file', r'\\?\C:\file', r'\\.\NUL', r'C:relative', r'C:\file:stream', r'C:\NUL.txt', r'C:\name. ', r'C:\dir\CON', 'C:\\file\x00'])
def test_windows_unsupported_path_forms_are_denied(raw):
    with pytest.raises(ContractError):
        validate_path_syntax(raw, platform='windows')


def test_windows_drive_case_normalization_is_explicit():
    assert validate_path_syntax(r'c:\工作区\a & b.txt', platform='windows', absolute=True) == 'C:/工作区/a & b.txt'


def test_path_boundaries_deny_protected_and_outside_writes(tmp_path):
    root, workspace, policy = policy_input(tmp_path)
    secret = root / 'private'
    secret.mkdir()
    policy['filesystem']['protected_paths'] = [str(secret)]
    paths = compile_policy(policy, workspace).paths
    assert paths.authorize(root / 'new.txt', write=True).path == root / 'new.txt'
    for target in (secret / 'future.txt', root / '.git' / 'config', root / '.env', tmp_path / 'project-other' / 'file'):
        with pytest.raises(ContractError):
            paths.authorize(target, write=True)
    for target in (secret / 'future.txt', root / '.env.new', root / 'nested' / 'credentials.json'):
        with pytest.raises(ContractError):
            paths.authorize(target)


def test_unsafe_write_roots_and_changed_workspace_identity_refused(tmp_path):
    root, workspace, policy = policy_input(tmp_path)
    policy['filesystem']['write_roots'] = [str(tmp_path)]
    with pytest.raises(ContractError):
        compile_policy(policy, workspace)
    policy['filesystem']['write_roots'] = [str(root)]
    workspace['file_identity'] = '0:0'
    with pytest.raises(ContractError):
        compile_policy(policy, workspace)


def test_hardlinks_are_rejected_using_actual_file_identity(tmp_path):
    root, workspace, policy = policy_input(tmp_path)
    os.link(root / 'value.txt', tmp_path / 'alias.txt')
    with pytest.raises(ContractError, match='hard'):
        compile_policy(policy, workspace)


def test_parent_replacement_is_detected_before_path_use(tmp_path):
    root, workspace, policy = policy_input(tmp_path)
    directory = root / 'folder'
    directory.mkdir()
    paths = compile_policy(policy, workspace).paths
    authorized = paths.authorize(directory / 'new.txt', write=True)
    directory.rename(root / 'old-folder')
    directory.mkdir()
    with pytest.raises(ContractError) as error:
        authorized.assert_current()
    assert error.value.kind == 'STALE_FILE'


def test_missing_target_created_after_authorization_is_stale(tmp_path):
    root, workspace, policy = policy_input(tmp_path)
    target = root / 'new.txt'
    authorized = compile_policy(policy, workspace).paths.authorize(target, write=True)
    target.write_text('external edit')
    with pytest.raises(ContractError):
        authorized.assert_current()


def test_worktree_gitdir_and_common_directory_are_protected(tmp_path):
    root, workspace, policy = policy_input(tmp_path)
    common = tmp_path / 'repository-metadata'
    common.mkdir()
    gitdir = common / 'worktrees' / 'one'
    gitdir.mkdir(parents=True)
    (gitdir / 'commondir').write_text('../..\n')
    (root / '.git').write_text('gitdir: ' + str(gitdir) + '\n')
    snapshot = compile_policy(policy, workspace)
    assert str(common) in snapshot.value['filesystem']['protected_paths']
    for target in (gitdir / 'config', common / 'config'):
        with pytest.raises(ContractError):
            snapshot.paths.authorize(target)


def test_actual_junction_or_symlink_cannot_bypass_path_policy(tmp_path):
    root, workspace, policy = policy_input(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = root / 'alias'
    if sys.platform == 'win32':
        import subprocess
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ContractError, match='link'):
            compile_policy(policy, workspace)
    finally:
        link.rmdir() if sys.platform == 'win32' else link.unlink()
