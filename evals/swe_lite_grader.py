"""Evaluator-side pytest grading for the explicitly adapted SWE-bench Lite subset.

Runs on the task's Python (including 3.9). No model client, assertion rewrites,
skip overrides, or reference fixes are part of this entry point.
"""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory


def resolve_node_ids(labels, node_ids, aliases):
    """Expand legacy whitespace-truncated labels; never choose one arbitrary match."""
    mapping = {}
    for label in labels:
        target = aliases.get(label, label)
        if target in node_ids:
            mapping[label] = [target]
        elif '[' in target and not target.endswith(']'):
            mapping[label] = [node for node in node_ids if node.split()[0] == target]
        else:
            mapping[label] = []
    return mapping


class TestRecorder:
    """Keep all selected node IDs and each setup/call/teardown result."""

    def __init__(self, suite):
        self.suite = suite
        self.mapping = {}
        self.tests = {}
        self.collection_errors = []

    def pytest_collection_modifyitems(self, config, items):
        import pytest

        labels = self.suite['FAIL_TO_PASS'] + self.suite['PASS_TO_PASS']
        self.mapping = resolve_node_ids(labels, [item.nodeid for item in items], self.suite.get('aliases', {}))
        missing = [label for label, nodes in self.mapping.items() if not nodes]
        if missing:
            raise pytest.UsageError('Required tests not collected: ' + repr(missing))
        if self.mapping != self.suite['expected_mapping']:
            raise pytest.UsageError('Collected test mapping differs from frozen preflight mapping')
        wanted = {node for nodes in self.mapping.values() for node in nodes}
        selected = [item for item in items if item.nodeid in wanted]
        deselected = [item for item in items if item.nodeid not in wanted]
        items[:] = selected
        config.hook.pytest_deselected(items=deselected)

    def pytest_runtest_logreport(self, report):
        self.tests.setdefault(report.nodeid, []).append({
            'when': report.when, 'outcome': report.outcome, 'xfail': getattr(report, 'wasxfail', None),
        })

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(str(report.longrepr))


def assessment(suite, report, *, expect_base=False):
    """Missing, skipped, xfailed, setup/teardown failures and duplicates never pass."""
    reasons = []
    if report['exit_code'] != (1 if expect_base else 0):
        reasons.append('unexpected_pytest_exit')
    if report['collection_errors']:
        reasons.append('collection_error')
    mapping = report['mapping']
    if mapping != suite.get('expected_mapping'):
        reasons.append('frozen_mapping_mismatch')
    expected_nodes = set()
    for group in ('FAIL_TO_PASS', 'PASS_TO_PASS'):
        for label in suite[group]:
            nodes = mapping.get(label, [])
            if not nodes:
                reasons.append('missing:' + label)
            expected_nodes.update(nodes)
            for node in nodes:
                expected = 'failed' if expect_base and group == 'FAIL_TO_PASS' else 'passed'
                entries = report['tests'].get(node, [])
                phases = {phase: [r for r in entries if r['when'] == phase]
                          for phase in ('setup', 'call', 'teardown')}
                if any(len(values) != 1 for values in phases.values()) or len(entries) != 3:
                    reasons.append('incomplete_or_duplicate:' + node)
                    continue
                for phase, values in phases.items():
                    result = values[0]
                    if result.get('xfail') or result['outcome'] != (expected if phase == 'call' else 'passed'):
                        reasons.append('unexpected_' + phase + ':' + node)
    if set(report['tests']) != expected_nodes:
        reasons.append('executed_set_mismatch')
    return {'accepted': not reasons, 'reasons': reasons, 'required_labels': len(mapping),
            'executed_tests': len(report['tests'])}


def main():
    import pytest

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--expect-base', action='store_true')
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding='utf-8'))
    labels = suite['FAIL_TO_PASS'] + suite['PASS_TO_PASS']
    if not suite['FAIL_TO_PASS'] or len(labels) != len(set(labels)):
        raise ValueError('Required labels must be nonempty for FAIL_TO_PASS and disjoint/unique')
    if set(suite.get('expected_mapping', {})) != set(labels):
        raise ValueError('A frozen preflight mapping is required for every label')
    recorder = TestRecorder(suite)
    files = sorted({label.split('::')[0] for label in labels})
    # Frozen node IDs are relative to the evaluated workspace, even when a
    # temporary workspace sits below another repository's pytest configuration.
    # Do not override -c: the task's own test options must remain in force.
    # Pytest's cache staging uses mkdir(0700), which can discard the restricted
    # token's inherited write capability in a project directory on Windows.
    # Keep cache artifacts in the already-owned execution scratch instead.
    with TemporaryDirectory(prefix='forge-pytest-cache-') as scratch:
        code = pytest.main(['--rootdir', str(Path.cwd()), '-o',
                            'cache_dir=' + str(Path(scratch) / 'cache'),
                            '-rA', '--tb=short', '--color=no', *files], plugins=[recorder])
    report = {'exit_code': int(code), 'mapping': recorder.mapping, 'tests': recorder.tests,
              'collection_errors': recorder.collection_errors}
    report['assessment'] = assessment(suite, report, expect_base=args.expect_base)
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['assessment']['accepted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
