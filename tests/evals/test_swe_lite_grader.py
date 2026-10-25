"""Exercise independent grading with real pytest collection and execution."""

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from evals.swe_lite_grader import assessment


GRADER = Path(__file__).resolve().parents[2] / 'evals' / 'swe_lite_grader.py'


def grade(tmp_path, source, suite, *, expect_base=False):
    suite.setdefault('expected_mapping', {label: [label] for label in suite['FAIL_TO_PASS'] + suite['PASS_TO_PASS']})
    (tmp_path / 'test_task.py').write_text(source, encoding='utf-8')
    suite_path = tmp_path / 'suite.json'
    suite_path.write_text(json.dumps(suite), encoding='utf-8')
    report_path = tmp_path / 'report.json'
    args = [sys.executable, str(GRADER), '--suite', str(suite_path), '--report', str(report_path)]
    if expect_base:
        args.append('--expect-base')
    process = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=30,
                             env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTEST_ADDOPTS': ''})
    return process, json.loads(report_path.read_text(encoding='utf-8'))


@pytest.mark.parametrize('fixed', [False, True])
def test_all_legacy_label_matches_run_and_required_regressions_survive(tmp_path, fixed):
    source = f'''import pytest
def test_bug():
    assert {fixed!r}
@pytest.mark.parametrize('value', [1, 2], ids=['same first', 'same second'])
def test_regression(value):
    assert value > 0
def test_unrelated():
    assert False
'''
    suite = {'FAIL_TO_PASS': ['test_task.py::test_bug'],
             'PASS_TO_PASS': ['test_task.py::test_regression[same'],
             'expected_mapping': {'test_task.py::test_bug': ['test_task.py::test_bug'],
                                  'test_task.py::test_regression[same': [
                                      'test_task.py::test_regression[same first]',
                                      'test_task.py::test_regression[same second]']}}
    process, report = grade(tmp_path, source, suite, expect_base=not fixed)
    assert process.returncode == 0, process.stdout + process.stderr
    assert report['mapping'][suite['PASS_TO_PASS'][0]] == [
        'test_task.py::test_regression[same first]', 'test_task.py::test_regression[same second]']
    assert report['assessment']['executed_tests'] == 3
    if not fixed:
        assert not assessment(suite, report)['accepted']


@pytest.mark.parametrize('decorator', ["@pytest.mark.skip(reason='platform')", "@pytest.mark.xfail(reason='known')"])
def test_skip_or_xfail_never_counts_as_success(tmp_path, decorator):
    suite = {'FAIL_TO_PASS': ['test_task.py::test_bug'], 'PASS_TO_PASS': []}
    process, report = grade(tmp_path, f'import pytest\n{decorator}\ndef test_bug():\n    assert False\n', suite)
    assert process.returncode == 1
    assert not report['assessment']['accepted']


def test_one_failing_parameter_cannot_hide_behind_last_passing_parameter(tmp_path):
    suite = {'FAIL_TO_PASS': ['test_task.py::test_bug[same'], 'PASS_TO_PASS': [],
             'expected_mapping': {'test_task.py::test_bug[same': [
                 'test_task.py::test_bug[same first]', 'test_task.py::test_bug[same second]']}}
    process, report = grade(tmp_path, '''import pytest
@pytest.mark.parametrize('value', [0, 1], ids=['same first', 'same second'])
def test_bug(value):
    assert value
''', suite)
    assert process.returncode == 1
    assert report['assessment']['executed_tests'] == 2
    assert 'unexpected_call:test_task.py::test_bug[same first]' in report['assessment']['reasons']


def test_missing_parameter_from_frozen_expansion_is_rejected(tmp_path):
    suite = {'FAIL_TO_PASS': ['test_task.py::test_bug[same'], 'PASS_TO_PASS': [],
             'expected_mapping': {'test_task.py::test_bug[same': [
                 'test_task.py::test_bug[same first]', 'test_task.py::test_bug[same second]']}}
    process, report = grade(tmp_path, '''import pytest
@pytest.mark.parametrize('value', [1], ids=['same first'])
def test_bug(value):
    assert value
''', suite)
    assert process.returncode == 1
    assert 'frozen_mapping_mismatch' in report['assessment']['reasons']


@pytest.mark.parametrize('source', ['def test_other(): pass\n', 'import unavailable_task_dependency\n'])
def test_missing_or_collection_error_is_rejected(tmp_path, source):
    process, report = grade(tmp_path, source, {'FAIL_TO_PASS': ['test_task.py::test_bug'], 'PASS_TO_PASS': []})
    assert process.returncode == 1
    assert not report['assessment']['accepted']
    assert 'missing:test_task.py::test_bug' in report['assessment']['reasons']


def test_frozen_subset_references_and_budgets_are_consistent():
    root = GRADER.parents[1]
    manifest_bytes = (root / 'experiments/native-swe-lite.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    plan = json.loads((root / 'experiments/native-swe-lite-plan.json').read_bytes())
    assert plan['task_manifest_sha256'] == hashlib.sha256(manifest_bytes).hexdigest()
    assert len(manifest['cases']) == 6
    for case in manifest['cases']:
        suite_bytes = (root / case['suite']).read_bytes()
        assert case['suite_sha256'] == hashlib.sha256(suite_bytes).hexdigest()
        suite = json.loads(suite_bytes)
        labels = suite['FAIL_TO_PASS'] + suite['PASS_TO_PASS']
        assert set(suite['expected_mapping']) == set(labels)
        nodes = [node for values in suite['expected_mapping'].values() for node in values]
        assert len(set(nodes)) == case['required']['actual_nodes']
        assert all(suite['expected_mapping'].values())
    for environment in manifest['environments'].values():
        assert environment['sha256'] == hashlib.sha256((root / environment['requirements']).read_bytes()).hexdigest()
    assert len(plan['schedule']) == plan['runs'] == 12
    assert plan['total_model_request_cap'] == plan['runs'] * plan['per_run']['max_model_calls']
    assert plan['automatic_retries'] == plan['failed_reruns'] == 0
