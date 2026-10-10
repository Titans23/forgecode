"""OS detection/report semantics, not simulated native boundary acceptance."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from forge.sandbox.doctor import linux_release, system_diagnosis


@pytest.mark.parametrize(('text', 'supported'), [
    ('ID=ubuntu\nVERSION_ID="22.04"\n', True), ('ID=ubuntu\nVERSION_ID="24.04"\n', True),
    ('ID=debian\nVERSION_ID="24.04"\n', False), ('ID=ubuntu\nVERSION_ID="20.04"\n', False),
    ('ID=ubuntu\nVERSION_ID="24.04"\nID=debian\n', False), ('', False),
    ('ID=ubuntu\nVERSION_ID="24.04\n', False), ('ID=ubuntu\nVERSION_ID=24.04"\n', False)])
def test_release_detection_does_not_guess_from_similar_distribution(text, supported):
    assert linux_release(text)['supported'] is supported


def test_relative_evidence_path_has_the_same_volume_as_absolute_path(tmp_path, monkeypatch):
    from forge.sandbox.doctor import windows_volume
    monkeypatch.chdir(tmp_path)
    assert windows_volume(Path('.')) == windows_volume(tmp_path)


def test_windows_fixture_only_adds_inherited_controller_access_to_a_fresh_owned_directory(tmp_path):
    from forge.sandbox.doctor import prepare_windows_fixture
    if sys.platform != 'win32':
        with pytest.raises(OSError, match='requires Windows'):
            prepare_windows_fixture(tmp_path)
        return
    import win32api
    import win32con
    import win32security
    from ntsecuritycon import FILE_ALL_ACCESS
    handle = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        controller = win32security.GetTokenInformation(handle, win32security.TokenUser)[0]
    finally:
        handle.Close()
    before = win32security.GetFileSecurity(str(tmp_path), win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
    original = [before.GetSecurityDescriptorDacl().GetAce(i) for i in range(before.GetSecurityDescriptorDacl().GetAceCount())]
    prepare_windows_fixture(tmp_path)
    child = tmp_path/'child'
    child.mkdir()
    acl = win32security.GetFileSecurity(str(child), win32security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
    assert any(acl.GetAce(i)[-1] == controller and
               acl.GetAce(i)[0][1] & win32security.INHERITED_ACE and
               acl.GetAce(i)[1] == FILE_ALL_ACCESS for i in range(acl.GetAceCount()))
    after = win32security.GetFileSecurity(str(tmp_path), win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
    assert after.GetSecurityDescriptorOwner() == before.GetSecurityDescriptorOwner()
    parent = after.GetSecurityDescriptorDacl()
    actual = [parent.GetAce(i) for i in range(parent.GetAceCount())]
    assert len(actual) == len(original) + 1
    assert all(ace in actual for ace in original)
    with pytest.raises(ValueError, match='fresh and empty'):
        prepare_windows_fixture(tmp_path)


def test_actual_read_only_system_diagnosis_never_installs_or_claims_verified():
    report = system_diagnosis()
    assert report['read_only'] is True
    assert report['runtime']['status'] == 'pass'
    assert report['host']['build']
    assert report['cleanup']['state'] == 'not_observed'
    assert not report['workspace_tools_executed']
    assert report['automatic_repair'] is False
    assert all(value['status'] != 'verified' for value in report['capabilities']['verification'].values())
    if report['windows_status']['status'] == 'observed':
        assert isinstance(report['windows_status']['user']['provisioned'], bool)
        assert isinstance(report['windows_status']['user']['credPresent'], bool)


def test_linux_native_entry_on_unsupported_host_is_blocked_not_empty_pass(tmp_path):
    if sys.platform == 'linux':
        # Linux CI exercises the real native entry separately; this branch checks metadata only.
        assert system_diagnosis()['read_only']
        return
    output = tmp_path / 'report.json'
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run([sys.executable, '-m', 'forge.sandbox.doctor', '--native-linux', '--output', str(output)],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 2, result.stderr
    report = json.loads(output.read_text())
    assert report['status'] == 'blocked' and report['checks'] == []
    assert report['reason'] == 'Supported Ubuntu native runner is unavailable'
    assert report['eligible_for_native_pass'] is False


def test_windows_native_entry_on_unsupported_host_is_blocked_and_never_elevates(tmp_path):
    if system_diagnosis()['host']['supported_windows']:
        assert system_diagnosis()['read_only']
        return
    output = tmp_path / 'windows-report.json'
    result = subprocess.run([sys.executable, '-m', 'forge.sandbox.doctor', '--native-windows', '--output', str(output)],
        cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 2, result.stderr
    report = json.loads(output.read_text())
    assert report['status'] == 'blocked' and report['checks'] == []
    assert report['eligible_for_native_pass'] is False
    assert report['diagnosis']['automatic_repair'] is False


def test_retired_status_does_not_launch_or_inspect_shared_srt(tmp_path, monkeypatch):
    from forge.sandbox import doctor
    def unexpected(*args, **kwargs):
        pytest.fail('Retired status must not launch helpers')
    monkeypatch.setattr(doctor.subprocess, 'run', unexpected)
    report = doctor.windows_status_diagnosis(SimpleNamespace(root=tmp_path, installed=True))
    assert report['status'] == 'not_applicable'
    assert report['read_only'] is True
    assert report['shared_activity'] == 'not_observed'


def test_controller_grant_rejects_foreign_owner_before_changing_acl(tmp_path, monkeypatch):
    if sys.platform != 'win32':
        return
    import win32security
    from forge.sandbox.windows_worker import grant_controller_access
    foreign = win32security.ConvertStringSidToSid('S-1-5-21-123456789-123456789-123456789-1001')
    monkeypatch.setattr(win32security, 'GetFileSecurity', lambda *args: SimpleNamespace(GetSecurityDescriptorOwner=lambda: foreign))
    changed=[]
    monkeypatch.setattr(win32security, 'SetNamedSecurityInfo', lambda *args: changed.append(args))
    with pytest.raises(ValueError, match='another controller'):
        grant_controller_access(tmp_path)
    assert changed == []
