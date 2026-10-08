"""Controlled report fixtures test the target policy, never attest to OS isolation."""
from hashlib import sha256
import pytest

from scripts.acceptance_audit import audit_mapping
from tests.implementation.unit.test_acceptance_audit import cases
from tests.implementation.unit.test_gates import gate_repo, write


@pytest.mark.parametrize('platform,os_build,eligible,expected', [
    ('win32', 'Windows-10-10.0.19045-SP0', True, True),
    ('win32', 'Windows-10-10.0.19045-SP0', False, False),
    ('win32', 'Windows-10-10.0.19044-SP0', True, False),
    ('win32', 'Windows-11-10.0.26100-SP0', True, False),
    ('win32', 'Windows-2025Server-10.0.26100-SP0', True, False),
    ('linux', 'Windows-10-10.0.19045-SP0', True, False),
])
def test_audit_and_release_require_windows10_native_evidence(tmp_path, monkeypatch, platform, os_build, eligible, expected):
    impl, record, _, case = gate_repo(tmp_path, monkeypatch)
    report = tmp_path / '.local/implementation/first/desktop.json'
    write(report, {'status': 'pass', 'eligible_for_native_pass': eligible,
        'checks': [{'id': 'unit-report-fixture-not-native-proof', 'status': 'pass'}]})
    registry = cases()
    record.update(suite='desktop', platform=platform, os_build=os_build,
        case_ids=[c['id'] for c in registry], report_ref=report.relative_to(tmp_path).as_posix(),
        report_hash=sha256(report.read_bytes()).hexdigest())
    write(impl.DOCS / 'evidence/first.json', record)
    proof = {'windows': {'status': 'pass', 'evidence_ids': ['first']}}
    for item in registry:
        item.update(implementation_test_refs=['tests/test_example.py'], platform_verification=proof)
    audit = audit_mapping(tmp_path, registry)
    assert all((item['platforms']['windows']['status'] == 'pass') is expected for item in audit)
    case.update(required_platforms=['windows'], platform_verification=proof)
    write(impl.DOCS / 'acceptance-registry.json', {'cases': [case]})
    gate = impl.gate('release')
    assert (not any(reason.startswith('N23/windows:') for reason in gate['blocked_dependencies'])) is expected
