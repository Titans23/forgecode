import asyncio
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from forge.testing.demo import FIXTURES, fixture_manifests, run_demo
from test_rpc import initialize, launch, stop


def test_six_public_fixture_structures_have_actual_assets():
    manifests = fixture_manifests()
    assert len(manifests) == 6
    for fixture in manifests.values():
        assert fixture['assertions'] and fixture['acceptance_tasks']
        assert all((FIXTURES / fixture['fixture_id'] / name).is_file() for name in fixture['inputs'])
    mini = json.loads((FIXTURES / 'eval-mini-bundle' / 'input.json').read_text())
    passed = {attempt['case_id'] for attempt in mini['attempts'] if attempt['raw_grade'] == 'pass'}
    assert len(set(mini['case_ids'])) == mini['expected']['denominator'] == 4
    assert len(passed) / len(mini['case_ids']) == float(mini['expected']['pass_rate'])


@pytest.mark.parametrize('disconnect', [False, True])
def test_real_rpc_demo_reads_repairs_and_verifies_without_duplicate_turn(tmp_path, disconnect):
    path = tmp_path / 'demo'
    original = FIXTURES / 'fix-python-add' / 'project' / 'calculator.py'
    original_hash = sha256(original.read_bytes()).hexdigest()
    report = run_demo(path, disconnect_before_response=disconnect)
    assert report['status'] == 'pass', report['checks']
    assert all(report['checks'].values())
    assert report['origin'] == 'scripted' and report['mode'] == 'local-trusted'
    assert report['eligible_for_benchmark'] is False and report['sandbox_acceptance'] == 'blocked'
    assert report['turn']['native_outcome']['model_calls'] == 5
    assert report['verification_history'][0]['exit_code'] == 1
    assert report['verification_history'][1]['exit_code'] == 0
    assert report['verification_history'][1]['freshness'] == 'unknown'  # Reload never upgrades historical evidence.
    assert sha256(original.read_bytes()).hexdigest() == original_hash
    assert len(list((path / 'data' / 'harness').rglob('session-*.jsonl'))) == 1
    for relative, expected in report['artifacts'].items():
        assert sha256((path / relative).read_bytes()).hexdigest() == expected
    # Existing files are not reset on a second demo invocation.
    with pytest.raises(FileExistsError):
        run_demo(path)


def test_one_command_demo_runs_and_persists_report(tmp_path):
    directory = tmp_path / 'command-demo'
    result = subprocess.run([sys.executable, '-m', 'forge.testing.demo', '--output-dir', str(directory)],
        capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['status'] == 'pass'
    assert json.loads((directory / 'demo-report.json').read_text()) == report


def test_production_default_uses_provider_model_and_strict_readiness(tmp_path):
    async def run():
        process = await launch(tmp_path, profile='desktop')
        try:
            hello = (await initialize(process, profile='desktop'))['result']
            assert 'provider-model' in hello['capabilities']['features']
            assert 'scripted-model' not in hello['capabilities']['features']
            assert hello['readiness']['status'] == 'blocked'
        finally:
            await stop(process)
    asyncio.run(run())
