"""Trusted paths and environments; these tests do not stand in for OS isolation."""
from hashlib import sha256
import json
from pathlib import Path

import pytest

from forge.application.models import ContractError
from forge.sandbox.launcher import bridge_environment, verify_asset, verify_runtime


def test_control_environment_drops_project_loading_and_credentials(tmp_path):
    environment = bridge_environment(tmp_path, source={
        'PATH': str(tmp_path / 'evil'), 'NODE_OPTIONS': '--require evil.js',
        'PYTHONPATH': 'evil', 'LD_PRELOAD': 'evil', 'API_KEY': 'secret',
        'HTTP_PROXY': 'http://secret@evil.invalid', 'SRT_DEBUG': '1'})
    assert environment['HOME'] == str(tmp_path)
    assert str(tmp_path / 'evil') not in environment['PATH']
    assert not set(environment) & {'NODE_OPTIONS', 'PYTHONPATH', 'LD_PRELOAD', 'API_KEY', 'HTTP_PROXY', 'SRT_DEBUG'}


def test_asset_integrity_and_boundary_are_checked_before_execution(tmp_path):
    resource = tmp_path / 'entry.js'
    resource.write_bytes(b'export {}')
    asset = {'path': 'entry.js', 'sha256': sha256(resource.read_bytes()).hexdigest()}
    assert verify_asset(tmp_path, asset) == resource
    resource.write_bytes(b'changed')
    with pytest.raises(ContractError, match='integrity'):
        verify_asset(tmp_path, asset)
    for path in ('../outside.js', str(resource), 'sub\\entry.js'):
        with pytest.raises(ContractError):
            verify_asset(tmp_path, {**asset, 'path': path})


def test_unresolved_or_incomplete_release_never_searches_project_path(tmp_path):
    (tmp_path / 'release-lock.json').write_text(json.dumps({'resolution_status': 'unresolved'}))
    with pytest.raises(ContractError):
        verify_runtime(tmp_path)


def test_development_runtime_has_pinned_node_and_complete_code_inventory():
    root = Path(__file__).resolve().parents[3]
    runtime = verify_runtime(root)
    assert runtime.node.is_absolute() and runtime.entry.is_absolute()
    assert '24.21.0' in str(runtime.node)
    assert runtime.root == root
    assert runtime.manifest_hash
