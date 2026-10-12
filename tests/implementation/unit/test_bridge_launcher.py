"""Trusted paths and environments; these tests do not stand in for OS isolation."""
from hashlib import sha256
import json
from pathlib import Path

import pytest

from forge.application.models import ContractError
from forge.sandbox.launcher import bridge_environment, verify_asset, verify_code_inventory, verify_runtime


def test_control_environment_drops_project_loading_and_credentials(tmp_path):
    environment = bridge_environment(tmp_path, source={
        'PATH': str(tmp_path / 'evil'), 'NODE_OPTIONS': '--require evil.js',
        'PYTHONPATH': 'evil', 'LD_PRELOAD': 'evil', 'API_KEY': 'secret',
        'LOCALAPPDATA': str(tmp_path / 'evil'),
        'HTTP_PROXY': 'http://secret@evil.invalid', 'SRT_DEBUG': '1'})
    assert environment['HOME'] == str(tmp_path)
    assert str(tmp_path / 'evil') not in environment['PATH']
    assert not set(environment) & {'NODE_OPTIONS', 'PYTHONPATH', 'LD_PRELOAD', 'API_KEY', 'HTTP_PROXY', 'SRT_DEBUG'}
    if __import__('sys').platform == 'win32':
        from forge.sandbox.windows_worker import windows_local_app_data
        assert environment['LOCALAPPDATA'] == str(windows_local_app_data())
        assert environment['PATHEXT'] == '.COM;.EXE;.BAT;.CMD'


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


def test_code_inventory_checks_all_bytes_without_host_tool_admission(monkeypatch):
    import shutil
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setattr(shutil, 'which', lambda name: pytest.fail('Code verification must not discover host tools'))
    entry, manifest_hash = verify_code_inventory(root)
    assert entry.is_absolute() and entry.name == 'workspace-runner.js'
    assert manifest_hash


def test_runtime_still_refuses_missing_host_tool_after_code_verification(monkeypatch):
    import forge.release.toolchains as tools
    root = Path(__file__).resolve().parents[3]
    def missing(name):
        raise ContractError('required host tool unavailable', kind='SANDBOX_UNAVAILABLE')
    monkeypatch.setattr(tools, 'core_tool', missing)
    with pytest.raises(ContractError) as rejected:
        verify_runtime(root)
    assert rejected.value.kind == 'SANDBOX_UNAVAILABLE'
