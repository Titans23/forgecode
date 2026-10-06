"""OS detection/report semantics, not simulated native boundary acceptance."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from forge.sandbox.doctor import linux_release, system_diagnosis, normalize_windows_status


@pytest.mark.parametrize(('text', 'supported'), [
    ('ID=ubuntu\nVERSION_ID="22.04"\n', True), ('ID=ubuntu\nVERSION_ID="24.04"\n', True),
    ('ID=debian\nVERSION_ID="24.04"\n', False), ('ID=ubuntu\nVERSION_ID="20.04"\n', False),
    ('ID=ubuntu\nVERSION_ID="24.04"\nID=debian\n', False), ('', False),
    ('ID=ubuntu\nVERSION_ID="24.04\n', False), ('ID=ubuntu\nVERSION_ID=24.04"\n', False)])
def test_release_detection_does_not_guess_from_similar_distribution(text, supported):
    assert linux_release(text)['supported'] is supported


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


def test_locked_native_status_shape_is_normalized_without_credentials():
    user, wfp = normalize_windows_status({'user': {'user': {'exists': False, 'group_exists': False},
        'cred_present': False, 'real_user_sid': 'host-fixture', 'ca_cert_pem': 'PRIVATE', 'password': 'PRIVATE'},
        'wfp': {'state': 'cannot-read', 'filters': 0, 'port_range': [60080, 60089]}})
    assert user == {'provisioned': False, 'groupExists': False, 'credPresent': False, 'realUserSid': 'host-fixture'}
    assert wfp == {'state': 'cannot-read', 'filters': 0, 'portRange': [60080, 60089]}
    assert 'PRIVATE' not in json.dumps([user, wfp])
    with pytest.raises((KeyError, ValueError)):
        normalize_windows_status({'user': {}, 'wfp': {}})
