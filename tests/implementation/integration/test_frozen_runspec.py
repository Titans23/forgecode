"""Freeze real package bytes and reject internally inconsistent exported plans."""
from copy import deepcopy
from dataclasses import replace
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from benchmark.adapters.harbor import export_runspec
from benchmark.core.spec import freeze_spec
from benchmark.harbor.forgecode_agent import ForgeCodeHarborAgent
from benchmark.harbor.snapshot import freeze_source
from forge.application.evaluation_client import REFERENCES
from forge.application.models import ContractError, canonical_hash
from tests.implementation.integration.test_evaluations import make_evaluation


def test_repair_plan_freezes_two_exports_and_keeps_the_input_unchanged(tmp_path):
    from scripts.delivery_experiment import freeze_plans
    methods, spec = make_evaluation(tmp_path)
    try:
        spec, values, _ = freeze_spec(methods.store, spec)
        before = deepcopy((spec, values))
        plans = freeze_plans(export_runspec(spec, values))
        assert (spec, values) == before
        assert set(plans) == {'A', 'B'}
        for group, repairs in (('A', 0), ('B', 2)):
            plan = plans[group]
            assert plan == export_runspec(plan['spec'], plan['resolved_snapshots'])
            assert plan['spec']['harness']['max_delivery_repairs'] == repairs
            assert plan['resolved_snapshots']['harness']['max_delivery_repairs'] == repairs
            assert plan['spec']['budget'] == spec['budget']
            assert plan['read_only_plan'] is True
        from benchmark.core.repair_comparison import compare_repairs
        assert compare_repairs(plans['A']['spec'], plans['B']['spec'],
            plans['A']['resolved_snapshots'], plans['B']['resolved_snapshots'])['comparable']
        assert methods.store.connection.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0] == 0
    finally:
        methods.store.close()


def test_repair_plan_cli_is_offline_and_refuses_to_overwrite(tmp_path):
    methods, spec = make_evaluation(tmp_path)
    try:
        spec, values, _ = freeze_spec(methods.store, spec)
        path = tmp_path / 'base.json'
        path.write_text(json.dumps(export_runspec(spec, values)), encoding='utf-8')
        destination = tmp_path / 'frozen-plans'
        argv = [sys.executable, 'scripts/delivery_experiment.py', 'freeze',
            '--from-plan', str(path), '--output', str(destination)]
        first = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        assert first.returncode == 0, first.stdout + first.stderr
        assert {p.name for p in destination.iterdir()} == {'A.json', 'B.json', 'manifest.json'}
        original = {p.name: p.read_bytes() for p in destination.iterdir()}
        second = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        assert second.returncode != 0
        assert {p.name: p.read_bytes() for p in destination.iterdir()} == original
        manifest = json.loads(original['manifest.json'])
        assert manifest['actual_model_calls'] == 0
        assert manifest['execution_authorized'] is False
        invalid = json.loads(path.read_text(encoding='utf-8'))
        invalid['spec_hash'] = '0' * 64
        path.write_text(json.dumps(invalid), encoding='utf-8')
        argv[-1] = str(tmp_path / 'invalid-plans')
        assert subprocess.run(argv, capture_output=True, timeout=20).returncode != 0
        assert not Path(argv[-1]).exists()
    finally:
        methods.store.close()


def test_frozen_source_binding_rejects_changed_bytes_and_changed_inventory(tmp_path):
    from benchmark.harbor.snapshot import verify_frozen_source
    source = freeze_source(Path(__file__).resolve().parents[3], tmp_path / 'snapshots')
    manifest = json.loads((source.parent / 'manifest.json').read_text(encoding='utf-8'))
    verify_frozen_source(source, manifest)
    changed = source / 'benchmark/catalog.py'
    original = changed.read_bytes()
    changed.write_bytes(original + b'\n# changed after planning\n')
    with pytest.raises(ValueError, match='Frozen source'):
        verify_frozen_source(source, manifest)
    changed.write_bytes(original)
    added = source / 'forge/unplanned.py'
    added.write_text('raise RuntimeError("unplanned code")', encoding='utf-8')
    with pytest.raises(ValueError, match='Frozen source'):
        verify_frozen_source(source, manifest)
    added.unlink()
    forged = deepcopy(manifest)
    forged['content_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='Frozen source'):
        verify_frozen_source(source, forged)


def test_harbor_materialize_refuses_inconsistent_plan_before_writing_job(tmp_path):
    from benchmark.adapters.harbor import HarborAdapter
    methods, spec = make_evaluation(tmp_path)
    try:
        spec, values, _ = freeze_spec(methods.store, spec)
        values['harness']['parent_budget']['max_model_calls'] += 1
        spec['harness']['configuration']['sha256'] = canonical_hash(values['harness'])
        output = tmp_path / 'job'
        with pytest.raises(ContractError, match='budget'):
            HarborAdapter('aider-polyglot').materialize(spec, values, {}, output, endpoint='https://example.test')
        assert not output.exists()
    finally:
        methods.store.close()


@pytest.mark.parametrize('drift', ['model', 'parameters', 'prompt', 'tools', 'launch', 'plan_hash'])
def test_bound_runner_rejects_drift_before_any_model_request(tmp_path, drift):
    from benchmark.harbor.run_forge import run_turn
    from forge.application.harness_adapter import LocalTrustedBackend
    from forge.config import ForgeConfig
    from forge.engine.persistence import new_id
    from forge.engine.test_profile import ScriptedModelClient
    from forge.runtime.agent_loop import load_system_prompt
    from forge.runtime.dependencies import RuntimeBindings

    methods, spec = make_evaluation(tmp_path)
    clients = []
    def factory(config, **kwargs):
        client = ScriptedModelClient(config, [], **kwargs)
        clients.append(client)
        return client
    try:
        spec, values, _ = freeze_spec(methods.store, spec)
        spec['model_mode'] = 'live'
        spec['model']['provider'] = 'anthropic'
        config = ForgeConfig(api_key='explicit-unused-scripted-key', model_id=spec['model']['requested_model'],
            max_tokens=1024, context_window=4096)
        spec['harness']['prompt_sha256'] = sha256(load_system_prompt().encode('utf-8')).hexdigest()
        frozen = {'parameters': values['model_parameters'], 'harness': deepcopy(values['harness']),
            'scope': {'trace_id': '1' * 32, 'span_id': '2' * 16,
                'run_id': new_id('run'), 'trial_id': new_id('trial')}, 'attempt_id': new_id('attempt')}
        if drift == 'model':
            config = replace(config, model_id='different-model')
        elif drift == 'parameters':
            config = replace(config, max_tokens=2048)
        elif drift == 'prompt':
            spec['harness']['prompt_sha256'] = '0' * 64
        elif drift == 'tools':
            spec['harness']['tool_schema_sha256'] = '0' * 64
        elif drift == 'launch':
            frozen['harness']['max_delivery_repairs'] = 2
        frozen['plan'] = export_runspec(spec, values)
        if drift == 'plan_hash':
            frozen['plan']['spec_hash'] = '0' * 64
        project = tmp_path / 'project'
        project.mkdir()
        bindings = RuntimeBindings(config=config, model_client_factory=factory,
            data_root=tmp_path / 'harness', backend=LocalTrustedBackend(), trusted_extensions=False)
        with pytest.raises(ValueError, match='differ(?:s)? from'):
            asyncio.run(run_turn(project, 'No request may start.', resume=False,
                max_model_calls=10, max_tool_calls=20, max_turn_seconds=30,
                frozen_configuration=frozen, runtime_bindings=bindings))
        assert sum(client.calls for client in clients) == 0
        assert not list(project.iterdir())
    finally:
        methods.store.close()


def test_frozen_and_staged_official_package_imports_its_own_adapter(tmp_path):
    source = freeze_source(Path(__file__).resolve().parents[3], tmp_path / 'snapshots')
    methods, spec = make_evaluation(tmp_path / 'metadata')
    try:
        spec, values, _ = freeze_spec(methods.store, spec)
        values['source'] = {'schema_version': 'forge.harbor.source.v1',
            'manifest': json.loads((source.parent / 'manifest.json').read_text(encoding='utf-8'))}
        spec['source']['source_snapshot']['sha256'] = canonical_hash(values['source'])
        configuration = json.dumps({'plan': export_runspec(spec, values)})
    finally:
        methods.store.close()
    with pytest.raises(ValueError, match='package override'):
        ForgeCodeHarborAgent(logs_dir=tmp_path / 'package-override', source_dir=source,
            frozen_configuration=configuration, package='unbound-package')
    staged = ForgeCodeHarborAgent(logs_dir=tmp_path / 'logs', source_dir=source,
        frozen_configuration=configuration)._stage_local_source()
    for package in (source, staged):
        program = """import pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import benchmark.catalog
import benchmark.adapters.harbor
import benchmark.harbor.run_forge
import forge.application.models
for module in (benchmark.catalog, benchmark.adapters.harbor,
               benchmark.harbor.run_forge, forge.application.models):
    assert pathlib.Path(module.__file__).resolve().is_relative_to(root)
print('isolated frozen imports passed')
"""
        result = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(package)],
            cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == 'isolated frozen imports passed'
    assert not (source / '.env').exists()
    assert not (staged / '.env').exists()
    (source / 'benchmark/catalog.py').write_text('changed after validation', encoding='utf-8')
    with pytest.raises(ValueError, match='Frozen source'):
        ForgeCodeHarborAgent(logs_dir=tmp_path / 'changed-logs', source_dir=source,
            frozen_configuration=configuration)._stage_local_source()
    assert not (tmp_path / 'changed-logs/forgecode-source').exists()


@pytest.mark.parametrize('field', ['harness', 'environment', 'grader', 'pricing', 'network_cache', 'model_parameters'])
def test_export_rejects_rehashed_but_inconsistent_snapshots(tmp_path, field):
    methods, spec = make_evaluation(tmp_path)
    try:
        spec, values, _ = freeze_spec(methods.store, spec)
        if field == 'harness':
            values[field]['parent_budget']['max_model_calls'] += 1
        elif field == 'environment':
            values[field]['platform'] = 'windows-native'
        elif field == 'grader':
            values[field]['revision'] = 'another-grader'
        elif field == 'pricing':
            values[field]['currency'] = 'EUR'
        elif field == 'network_cache':
            values[field]['allowed_domains'] = ['example.test']
        else:
            values[field]['temperature'] = '3'
        group, key, _ = REFERENCES[field]
        spec[group][key]['sha256'] = canonical_hash(values[field])
        with pytest.raises(ContractError):
            export_runspec(spec, values)
    finally:
        methods.store.close()


def test_export_validates_snapshot_schema_and_does_not_mutate_the_plan(tmp_path):
    methods, spec = make_evaluation(tmp_path)
    try:
        spec, values, _ = freeze_spec(methods.store, spec)
        original = deepcopy((spec, values))
        exported = export_runspec(spec, values)
        assert (spec, values) == original
        assert exported['spec_hash'] == canonical_hash(spec)
        values['environment']['unrecognized_authority'] = 'trusted'
        spec['execution']['environment_snapshot']['sha256'] = canonical_hash(values['environment'])
        with pytest.raises(ContractError):
            export_runspec(spec, values)
        assert methods.store.connection.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0] == 0
    finally:
        methods.store.close()
