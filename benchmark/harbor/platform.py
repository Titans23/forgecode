"""Freeze a CLI launch plan around the existing official-task runner.

This does not change the separately gated V4 scheduler/RunSpec execution path.
"""
import asyncio
import base64
from dataclasses import asdict, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import zipfile

from benchmark.catalog import get_benchmark
from benchmark.harbor.snapshot import freeze_source, verify_frozen_source
from benchmark.adapters.materialize import materialize_dataset, reuse_taskset, resolve_task
from forge.agents import AgentDefinition
from forge.api import RunProfile
from forge.delivery import export_agent, import_agent, load_definition, portable
from forge.runtime.model_registry import ModelSelection, ProviderRegistry
from forge.validation import digest


def credential_name(selection):
    return selection.get('api_key_env') or {'anthropic': 'ANTHROPIC_API_KEY', 'openai_responses': 'OPENAI_API_KEY',
        'openai_chat': 'OPENAI_API_KEY', 'deepseek': 'DEEPSEEK_API_KEY', 'gemini': 'GEMINI_API_KEY'}.get(selection['provider'], 'FORGE_API_KEY')


def credential_references(value):
    names = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith('_env') and item is not None:
                if not isinstance(item, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', item):
                    raise ValueError('Credential references must be environment variable names')
                names.add(item)
            elif isinstance(item, (dict, list, tuple)):
                names.update(credential_references(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            names.update(credential_references(item))
    return names


def make_plan(config_path, destination):
    config_path, destination = Path(config_path).resolve(), Path(destination).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    allowed = {'benchmark', 'model', 'models', 'agent', 'budget', 'tasks', 'dataset_ref', 'taskset_directory',
               'source_directory', 'harness', 'pricing'}
    if set(config) - allowed:
        raise ValueError('Unknown plan configuration fields: ' + ', '.join(sorted(set(config) - allowed)))
    portable(config)
    spec = get_benchmark(config['benchmark'])
    if spec.status != 'ready' or not spec.dataset:
        raise ValueError('Choose a registered ready official benchmark')
    definition = load_definition(config_path.parent / config['agent']) if config.get('agent') else AgentDefinition()
    if definition.base != 'coding':
        raise ValueError('These official code-task benchmarks require a coding composition')
    from forge.context.compactor import CompactionConfig
    CompactionConfig(**definition.context_options)
    models = config.get('models', {})
    if 'default' in models:
        raise ValueError('default is reserved for the primary model')
    for selection in models.values():
        ModelSelection(**selection).resolve({credential_name(selection): 'unused-planning'})
    for alias in definition.role_models.values():
        if alias != 'default' and alias not in models:
            raise ValueError(f'Unconfigured role model: {alias}')
    selected = models[definition.role_models['main']] if definition.role_models.get('main') not in (None, 'default') else config['model']
    model = asdict(ModelSelection(**selected))
    ModelSelection(**model).resolve({credential_name(model): 'unused-planning'})
    budget = {'max_model_calls': 120, 'max_tool_calls': 240, 'wall_seconds': 1800, **config.get('budget', {})}
    RunProfile(**budget)
    harness = {'max_delivery_repairs': 2, **config.get('harness', {})}
    if set(harness) != {'max_delivery_repairs'} or type(harness['max_delivery_repairs']) is not int or not 0 <= harness['max_delivery_repairs'] <= 2:
        raise ValueError('Supported Harness override: max_delivery_repairs in 0..2; use Agent context_options/prompt/tools for composition changes')
    if config.get('pricing'):
        from forge.observability.usage_ledger import PriceBook
        PriceBook(config['pricing'])
    tasks = config.get('tasks')
    if not isinstance(tasks, list) or not tasks or not all(isinstance(x, str) for x in tasks) or len(set(tasks)) != len(tasks):
        raise ValueError('Freeze an explicit nonempty list of unique task IDs')
    destination.mkdir(parents=True, exist_ok=False)
    source_root = config_path.parent / config.get('source_directory', str(Path(__file__).resolve().parents[2]))
    source = freeze_source(source_root, destination / 'source')
    selection = dict(dataset=spec.dataset, ref=config.get('dataset_ref', 'latest'), task_names=tasks,
                     n_tasks=None, destination=destination / 'tasks')
    taskset = (reuse_taskset(config_path.parent / config['taskset_directory'], **selection) if config.get('taskset_directory') else
               asyncio.run(materialize_dataset(**selection)))
    bundle = destination / 'agent.zip'
    bundle_manifest = export_agent(definition, {'cases': []}, bundle, models=models)
    with zipfile.ZipFile(bundle) as archive:
        delivered_definition = json.loads(archive.read('agent.json'))
        dependencies = archive.read('requirements.lock').decode()
    references = credential_references([delivered_definition, models, model])
    references.update(credential_name(m) for m in [model, *models.values()])
    value = {'schema_version': 'forge.evaluation.launch-plan.v1', 'benchmark': spec.key, 'dataset': spec.dataset,
        'taskset': taskset, 'source': source.relative_to(destination).as_posix(),
        'source_manifest': json.loads((source.parent / 'manifest.json').read_text()),
        'model': model, 'models': models, 'budget': budget, 'harness': harness, 'pricing': config.get('pricing'),
        'composition': {'definition': delivered_definition, 'files': bundle_manifest['files'], 'dependencies': dependencies},
        'bundle': 'agent.zip', 'bundle_sha256': sha256(bundle.read_bytes()).hexdigest(),
        'credential_references': sorted(references), 'protocol': {'feedback': 'none', 'attempts': 1, 'task_retries': 0,
            'official_task_images': True}, 'model_api_calls': 0}
    value['plan_hash'] = digest(value)
    (destination / 'plan.json').write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    return value


def load_plan(path):
    path = Path(path).resolve()
    if path.is_dir():
        path = path / 'plan.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('schema_version') != 'forge.evaluation.launch-plan.v1' or value.get('plan_hash') != digest({k: v for k, v in value.items() if k != 'plan_hash'}):
        raise ValueError('Launch plan bytes changed')
    from forge.validation import relative_file
    for key in ('source', 'bundle'):
        relative_file(value[key])
        if not (path.parent / value[key]).resolve().is_relative_to(path.parent):
            raise ValueError('Plan artifact escaped its directory')
    verify_frozen_source(path.parent / value['source'], value['source_manifest'])
    if json.loads((path.parent / 'tasks/taskset.json').read_text(encoding='utf-8')) != value['taskset']:
        raise ValueError('Taskset metadata changed after planning')
    if sha256((path.parent / value['bundle']).read_bytes()).hexdigest() != value['bundle_sha256']:
        raise ValueError('Agent bundle changed after planning')
    for task in value['taskset']['tasks']:
        resolve_task(path.parent / 'tasks', value['taskset'], task)
    return path, value


def run_plan(path, *, output=None, execute=False, trust_plugins=False):
    path, plan = load_plan(path)
    if not execute:
        return {'status': 'planned', 'plan_hash': plan['plan_hash'], 'model_api_calls': 0,
                'next': 'Use --execute to run this frozen scope; plugin compositions also require --trust-plugins'}
    if plan['composition']['definition']['plugins'] and not trust_plugins:
        raise ValueError('Running this composition requires --trust-plugins; Python plugins run on the host of each task')
    from benchmark.harbor.run_dataset import main
    args = ['--dataset', plan['dataset'], '--dataset-ref', plan['taskset']['version'], '--taskset-dir', str(path.parent / 'tasks'),
            '--source-dir', str(path.parent / plan['source']), '--launch-plan', str(path)]
    for task in plan['taskset']['tasks']:
        args += ['--task', task]
    from harbor.models.task.task import Task
    for task in plan['taskset']['tasks']:
        resolved = resolve_task(path.parent / 'tasks', plan['taskset'], task)
        if not Task(resolved).config.environment.docker_image:
            raise ValueError('This task has no official prebuilt image; direct-image execution remains unverified and no image is built automatically')
    for key, flag in [('max_model_calls', '--max-model-calls'), ('max_tool_calls', '--max-tool-calls'), ('wall_seconds', '--max-turn-seconds')]:
        args += [flag, str(plan['budget'][key])]
    if output:
        args += ['--output-dir', str(output)]
    if trust_plugins:
        args += ['--trust-plugins']
    return {'status': 'finished', 'exit_code': main(args), 'plan_hash': plan['plan_hash']}


def runtime_bundle(plan_path):
    path, plan = load_plan(plan_path)
    return {**plan['composition'], 'models': plan['models'], 'pricing': plan['pricing'],
            'credential_references': plan['credential_references'], 'plan_hash': plan['plan_hash'],
            'archive_base64': base64.b64encode((path.parent / plan['bundle']).read_bytes()).decode(),
            'archive_sha256': plan['bundle_sha256']}


async def prepare_runtime(composition, bindings, project):
    """Install is owned by Harbor; loading here uses the normal PluginHost/registry."""
    from forge.plugins import PluginHost
    from uuid import uuid4
    root = bindings.data_root / ('composition-' + uuid4().hex)
    root.mkdir(parents=True)
    raw = base64.b64decode(composition['archive_base64'], validate=True)
    if sha256(raw).hexdigest() != composition['archive_sha256']:
        raise ValueError('Runtime composition bytes differ from plan')
    bundle = root / 'agent.zip'
    bundle.write_bytes(raw)
    import_agent(bundle, root / 'imported')
    agent = load_definition(root / 'imported/agent.json', trust=True)
    host = await PluginHost(workspace=project, data_root=bindings.data_root).load(agent.plugins)
    registry = ProviderRegistry()
    try:
        for name, factory in host.providers.items():
            registry.register(name, factory)
    except BaseException:
        await host.close()
        raise
    startup = {'active': True, 'clients': []}
    host.model_startup = startup
    factory = bindings.model_client_factory or registry.create
    def create(*args, **kwargs):
        client = factory(*args, **kwargs)
        if startup['active']:
            startup['clients'].append(client)
        return client
    async def close_startup():
        failures = []
        for client in {id(c): c for c in startup['clients']}.values():
            close = getattr(client, 'aclose', None)
            if close:
                try:
                    await close()
                except (Exception, asyncio.CancelledError) as error:
                    failures.append(type(error).__name__)
        if failures:
            raise RuntimeError('Startup client cleanup: ' + ', '.join(failures))
    host.cleanups.append(close_startup)
    return agent, replace(bindings, plugin_host=host, model_aliases=composition['models'],
        model_environment=dict(os.environ), model_client_factory=create)
