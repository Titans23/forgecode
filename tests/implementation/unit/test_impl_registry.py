import json
from pathlib import Path
import subprocess
import sys

from test_impl_audit import load_impl


def test_case_registry_references_real_tests():
    impl = load_impl()
    for references in impl.CASE_TESTS.values():
        assert references
        assert all((impl.ROOT / ref).is_file() for ref in references)


def test_unknown_and_unimplemented_task_commands_cannot_pass():
    impl = load_impl()
    for arguments, code in [(['verify', '--task', 'F05'], 1), (['verify', '--task', 'F99'], 3)]:
        result = subprocess.run([sys.executable, str(impl.ROOT / 'scripts/impl.py'), *arguments],
                                cwd=impl.ROOT, capture_output=True, text=True)
        assert result.returncode == code


def test_implementation_gate_requires_all_tasks_and_untampered_evidence(tmp_path, monkeypatch):
    impl = load_impl()
    docs = tmp_path / 'docs'
    (docs / 'evidence').mkdir(parents=True)
    state = {'tasks': {'F00': {'implementation_status': 'implemented', 'evidence_ids': ['test'], 'blocked_reasons': []},
                       'F01': {'implementation_status': 'todo', 'evidence_ids': [], 'blocked_reasons': []}}}
    (docs / 'progress.json').write_text(json.dumps(state))
    (docs / 'evidence/test.json').write_text(json.dumps({'report_ref': 'missing.xml', 'report_hash': 'a' * 64}))
    monkeypatch.setattr(impl, 'DOCS', docs)
    monkeypatch.setattr(impl, 'ROOT', tmp_path)
    result = impl.gate('implementation')
    assert result['status'] == 'fail'
    assert result['incomplete_tasks'] == ['F01']
    assert result['missing_or_changed_evidence'] == ['test']
