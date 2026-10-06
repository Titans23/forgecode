"""F00 uses real subprocesses and an isolated repository, without model calls."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[3] / 'scripts' / 'impl.py'


def load_impl():
    spec = importlib.util.spec_from_file_location('forge_impl', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_discovers_root_from_another_cwd_and_preserves_user_files(tmp_path):
    before = subprocess.check_output(['git', 'status', '--porcelain=v1', '-z'], cwd=SCRIPT.parent.parent)
    result = subprocess.run([sys.executable, str(SCRIPT), 'audit'], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    audit = json.loads(result.stdout)
    assert Path(audit['repository_root']) == SCRIPT.parent.parent
    assert len(audit['git_commit']) == 40
    assert audit['code_map']['conversation']['symbols']['Conversation.stream']['signature'].startswith('async def stream(')
    after = subprocess.check_output(['git', 'status', '--porcelain=v1', '-z'], cwd=SCRIPT.parent.parent)
    assert after == before


def test_audit_reads_actual_signatures_and_leaves_dirty_file_bytes_unchanged(tmp_path):
    impl = load_impl()
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    source = tmp_path / 'source.py'
    source.write_text('class Example:\n    async def run(self, value: str, *, count=3):\n        return value\n', encoding='utf-8')
    dirty = tmp_path / 'user.bin'
    dirty.write_bytes(b'\x00\xffuser work\r\n')
    original = dirty.read_bytes()
    mapping = impl.symbol_map(tmp_path, {'example': ('source.py', ['Example.run'])})
    assert mapping['example']['symbols']['Example.run']['signature'] == 'async def run(self, value: str, *, count=3)'
    assert dirty.read_bytes() == original


def test_missing_node_is_blocked_without_a_fabricated_version(monkeypatch):
    impl = load_impl()
    monkeypatch.setattr(impl.shutil, 'which', lambda name: None)
    result = impl.probe_tool('node')
    assert result['status'] == 'blocked'
    assert result['version'] is None
    assert 'not found' in result['reason']


def test_network_failure_is_blocked(monkeypatch):
    impl = load_impl()
    def unavailable(*args, **kwargs):
        raise OSError('offline')
    monkeypatch.setattr(impl.urllib.request, 'urlopen', unavailable)
    result = impl.probe_network()
    assert result['status'] == 'blocked'
    assert result['reason'] == 'OSError'


def test_status_reports_only_dependency_ready_tasks():
    impl = load_impl()
    report = impl.task_status(
        {'tasks': [{'id': 'F00', 'depends_on': []}, {'id': 'F01', 'depends_on': ['F00']},
                   {'id': 'F02', 'depends_on': ['F01']}]},
        {'tasks': {'F00': {'implementation_status': 'implemented'},
                   'F01': {'implementation_status': 'todo'}, 'F02': {'implementation_status': 'todo'}}})
    assert report['ready_tasks'] == ['F01']
    assert report['waiting_tasks'] == {'F02': ['F01']}


def test_empty_or_skipped_required_tests_cannot_report_pass(tmp_path):
    impl = load_impl()
    report = tmp_path / 'junit.xml'
    report.write_text('<testsuites><testsuite tests="0"/></testsuites>')
    assert impl.pytest_outcome(0, report)[0] == 'fail'
    report.write_text('<testsuites><testsuite><testcase><skipped/></testcase></testsuite></testsuites>')
    assert impl.pytest_outcome(0, report)[0] == 'blocked'


def test_invalid_command_has_specified_usage_exit_code(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), 'unknown'], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 3
    assert json.loads(result.stderr)['status'] == 'invalid_configuration'
