import asyncio
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from benchmark.harbor import platform
from benchmark.harbor.snapshot import freeze_source
from benchmark.adapters.protocol import content_hash
from forge.config import ForgeConfig
from forge.runtime.dependencies import RuntimeBindings
from forge.permissions.policy import PermissionManager
from forge.agents import AgentDefinition, PluginSpec
from forge.delivery import export_agent

ROOT = Path(__file__).resolve().parents[2]


def fixture_config(tmp_path, benchmark='terminal-bench-2'):
    from benchmark.catalog import get_benchmark
    taskset = tmp_path / 'taskset'
    task = taskset / 'task-a'
    task.mkdir(parents=True)
    files = {'instruction.md': b'Offline structural fixture, never executed.',
             'task.toml': b'version = "1.0"\n[environment]\ndocker_image="registry.invalid/offline-fixture:never-executed"\n',
             'tests/test.sh': b'exit 0\n', 'environment/Dockerfile': b'FROM scratch\n'}
    for name, raw in files.items():
        (task / name).parent.mkdir(parents=True, exist_ok=True)
        (task / name).write_bytes(raw)
    from harbor.models.task.task import Task
    manifest = {'schema_version': 'forge.harbor.taskset.v1', 'benchmark': benchmark,
        'dataset': get_benchmark(benchmark).dataset, 'version': 'sha256:' + 'a' * 64, 'usage': 'evaluation-only',
        'dataset_files': {}, 'tasks': {'task-a': {'directory': 'task-a', 'content_sha256': content_hash(files),
             'files': {k: sha256(v).hexdigest() for k, v in files.items()}, 'harbor_checksum': Task(task).checksum, 'os': 'linux'}}}
    (taskset / 'taskset.json').write_text(json.dumps(manifest))
    source = tmp_path / 'source-fixture'
    for name, raw in {'pyproject.toml': '[project]\nname="offline-source-fixture"\nversion="0.0.0"\n',
        'README.md': 'Structural source fixture; never installed or executed.', 'forge/__init__.py': '',
        'benchmark/__init__.py': '', 'benchmark/harbor/__init__.py': '', 'benchmark/core/__init__.py': ''}.items():
        file = source / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(raw)
    config = {'benchmark': benchmark, 'tasks': ['task-a'], 'taskset_directory': str(taskset),
              'source_directory': str(source), 'model': {'provider': 'openai_chat', 'model': 'fixture', 'api_key_env': 'FIXTURE_KEY'}}
    path = tmp_path / 'configuration.json'
    path.write_text(json.dumps(config))
    return path


@pytest.mark.parametrize('benchmark', ['terminal-bench-2', 'swe-bench-verified', 'aider-polyglot'])
def test_plan_freezes_all_three_without_credentials_or_model_calls(tmp_path, benchmark):
    path = fixture_config(tmp_path, benchmark)
    plan = platform.make_plan(path, tmp_path / 'plan')
    assert plan['model_api_calls'] == 0 and plan['protocol']['official_task_images']
    assert platform.run_plan(tmp_path / 'plan')['status'] == 'planned'
    bundle = tmp_path / 'plan/agent.zip'
    bundle.write_bytes(bundle.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='bundle changed'):
        platform.load_plan(tmp_path / 'plan')


def test_frozen_plan_cli_delegates_without_changing_conditions(tmp_path, monkeypatch, capsys):
    from benchmark.cli import main
    from benchmark.harbor import run_dataset
    path = fixture_config(tmp_path)
    assert main(['plan', str(path), '--output', str(tmp_path / 'plan')]) == 0
    capsys.readouterr()
    assert main(['run', '--plan', str(tmp_path / 'plan')]) == 0
    assert json.loads(capsys.readouterr().out)['model_api_calls'] == 0
    received = []
    monkeypatch.setattr(run_dataset, 'main', lambda args: received.extend(args) or 0)
    assert main(['run', '--plan', str(tmp_path / 'plan'), '--execute']) == 0
    assert '--launch-plan' in received and '--taskset-dir' in received
    assert not {'--force-build', '--prepare-only', '--prepared-images'} & set(received)


def test_plan_cannot_silently_override_model_or_source(tmp_path, monkeypatch):
    from benchmark.harbor import run_dataset
    path = fixture_config(tmp_path)
    plan = platform.make_plan(path, tmp_path / 'plan')
    args = run_dataset.build_parser().parse_args(['--dataset', plan['dataset'], '--task', 'task-a',
        '--dataset-ref', plan['taskset']['version'], '--source-dir', str(tmp_path / 'plan' / plan['source']),
        '--taskset-dir', str(tmp_path / 'plan/tasks'), '--launch-plan', str(tmp_path / 'plan/plan.json')])
    monkeypatch.setenv('FIXTURE_KEY', 'synthetic-planning-credential')
    config, manifest = run_dataset.effective_configuration(args)
    assert config.model_id == 'fixture' and 'synthetic-planning-credential' not in json.dumps(manifest)
    args.model = 'different'
    with pytest.raises(ValueError, match='overridden'):
        run_dataset.effective_configuration(args)
    args.model = None
    args.source_dir = ROOT
    with pytest.raises(ValueError, match='overridden'):
        run_dataset.effective_configuration(args)


def test_runtime_composition_uses_normal_host_and_provider_registry(tmp_path):
    import base64
    root = ROOT / 'examples/agents/expenses'
    agent = AgentDefinition(plugins=(PluginSpec(str(root)),), tools=('summarize_expenses',))
    bundle = tmp_path / 'agent.zip'
    export_agent(agent, {'cases': []}, bundle)
    composition = {'archive_base64': base64.b64encode(bundle.read_bytes()).decode(),
                   'archive_sha256': sha256(bundle.read_bytes()).hexdigest(), 'models': {}}
    project = tmp_path / 'project'
    project.mkdir()
    config = ForgeConfig(api_key='fixture', model_id='fixture')
    async def run():
        definition, bindings = await platform.prepare_runtime(composition, RuntimeBindings(config=config,
            data_root=tmp_path / 'data', permission_manager=PermissionManager(project)), project)
        assert definition.plugins[0].trusted
        result = await bindings.plugin_host.tools['summarize_expenses'].run({'csv': 'category,amount\nmeal,25\n'})
        assert json.loads(result.content)['total'] == '25.00'
        assert (await bindings.plugin_host.close())['state'] == 'clean'
    asyncio.run(run())


def test_unknown_request_usage_and_frozen_costs(tmp_path):
    from benchmark.harbor.summarize import request_accounting
    path = tmp_path / 'job/trial/session-fixture.jsonl'
    path.parent.mkdir(parents=True)
    def event(kind, value):
        return json.dumps({'type': 'observation', 'payload': {'event': {'event_type': kind, 'attributes': value}}}) + '\n'
    known = {'model_request_id': 'one', 'provider': 'openai_chat', 'requested_model': 'fixture', 'usage_quality': 'actual',
             'raw_usage': {'prompt_tokens': 100, 'completion_tokens': 10}}
    path.write_text(event('model.request.finished', known) + event('model.request.started', {'model_request_id': 'missing'}))
    pricing = {'revision': 'fixture-v1', 'source': 'synthetic fixture, not current prices', 'currency': 'USD',
               'rates': [{'provider': 'openai_chat', 'model': 'fixture', 'per_million': {'input_tokens': '1', 'output_tokens': '2'}}]}
    result = request_accounting(tmp_path, pricing)
    assert result['input_tokens'] is None and result['known_input_subtotal'] == 100
    assert result['unknown_usage_requests'] == 1 and result['cost']['quality'] == 'unknown'
    path.write_text(event('model.request.finished', known))
    assert request_accounting(tmp_path, pricing)['cost']['value'] == '0.00012'


def test_frozen_composition_flows_through_existing_benchmark_loop(tmp_path):
    import base64
    from uuid import uuid4
    from benchmark.harbor.run_forge import run_turn
    from forge.observability.events import Scope
    from forge.runtime.state import ModelToolCallCompleted, ToolCall, ModelTextDelta, ModelResponseCompleted
    agent = AgentDefinition(plugins=(PluginSpec(str(ROOT / 'examples/agents/expenses')),))
    bundle = tmp_path / 'agent.zip'
    bundle_manifest = export_agent(agent, {'cases': []}, bundle)
    configuration = ForgeConfig(api_key='unused', model_id='scripted-benchmark')
    budget = {'max_model_calls': 5, 'max_tool_calls': 3, 'wall_seconds': 60}
    scope = Scope()
    composition = {'archive_base64': base64.b64encode(bundle.read_bytes()).decode(),
        'archive_sha256': sha256(bundle.read_bytes()).hexdigest(), 'models': {},
        'definition': agent.as_dict(), 'files': bundle_manifest['files'], 'plan_hash': 'fixture'}
    frozen = {'parameters': {'temperature': None, 'top_p': None, 'max_output_tokens': configuration.max_tokens, 'reasoning_effort': None},
        'harness': {'max_context_tokens': configuration.context_window, 'compaction_enabled': True, 'max_delivery_repairs': 2,
            'parent_budget': budget, 'explore_enabled': True, 'trusted_extensions_enabled': False},
        'model': {k: v for k, v in asdict(configuration).items() if k != 'api_key'}, 'composition': composition,
        'scope': {'trace_id': scope.trace_id, 'span_id': scope.span_id, 'run_id': 'run-' + str(uuid4()), 'trial_id': 'trial-' + str(uuid4())},
        'attempt_id': 'attempt-' + str(uuid4())}
    class Model:
        model = 'scripted-benchmark'
        calls = 0
        closed = False
        async def stream(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield ModelToolCallCompleted(ToolCall(0, 'expense', 'summarize_expenses', {'csv': 'category,amount\nmeal,25\n'}))
                yield ModelResponseCompleted('tool_use')
            else:
                yield ModelTextDelta('Fixture ends without claiming official verifier success.')
                yield ModelResponseCompleted('end_turn')
        async def aclose(self):
            self.closed = True
    model = Model()
    project = tmp_path / 'project'
    project.mkdir()
    result = asyncio.run(run_turn(project, 'Apply the fixture tool', resume=False, max_model_calls=5, max_tool_calls=3,
        max_turn_seconds=60, frozen_configuration=frozen, result_path=tmp_path / 'result.json',
        runtime_bindings=RuntimeBindings(config=configuration, model_client_factory=lambda *a, **k: model,
            data_root=tmp_path / 'data', permission_manager=PermissionManager(project), intent_routing=False, trusted_extensions=False)))
    assert result.tool_calls[0].name == 'summarize_expenses' and model.closed
    assert json.loads((tmp_path / 'result.json').read_text())['cleanup']['state'] == 'clean'
