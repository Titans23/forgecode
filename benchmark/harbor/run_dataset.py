'''Run a ForgeCode Harbor agent against a registered Harbor dataset.'''

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from typing import Sequence
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

from forge.config import ForgeConfig, model_environment


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT = 'benchmark.harbor.forgecode_agent:ForgeCodeHarborAgent'
DATASETS = ('terminal-bench/terminal-bench-2', 'swe-bench/swe-bench-verified')


def evaluation_directory() -> Path:
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'forgecode/evaluation'


def container_base_url(value: str) -> str:
    '''Make a host-loopback provider reachable from a Docker task.'''
    parsed = urlsplit(value)
    if parsed.hostname not in {'localhost', '127.0.0.1'}:
        return value.rstrip('/')
    port = f':{parsed.port}' if parsed.port is not None else ''
    return urlunsplit(
        (parsed.scheme, f'host.docker.internal{port}', parsed.path,
         parsed.query, parsed.fragment)
    ).rstrip('/')


def local_provider_available(value: str, timeout_seconds: float = 2) -> bool:
    '''Return whether a configured host-loopback provider is listening.'''
    parsed = urlsplit(value)
    if parsed.hostname not in {'localhost', '127.0.0.1'}:
        return True
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        with socket.create_connection(
            (parsed.hostname, port), timeout=timeout_seconds
        ):
            return True
    except OSError:
        return False




def configured_values(env_file: Path) -> dict[str, str]:
    return model_environment(env_file)


def budget_limit(value) -> int | None:
    if value is None or value == 'unlimited':
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError('Budget must be a positive integer or unlimited.') from error
    if parsed < 1:
        raise ValueError('Budget must be a positive integer or unlimited.')
    return parsed


def harbor_executable() -> str:
    candidate = Path(sys.executable).with_name('harbor.exe' if os.name == 'nt' else 'harbor')
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError('Harbor executable is not available in this environment.')


def build_parser(
    *,
    default_dataset: str | None = None,
    default_output_dir: Path | None = None,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Run ForgeCode on a Harbor-registered benchmark dataset.'
    )
    parser.add_argument('--dataset', choices=DATASETS, default=default_dataset, required=default_dataset is None)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--task', action='append', default=[])
    selection.add_argument('--n-tasks', type=int)
    selection.add_argument('--all', action='store_true')
    parser.add_argument('--dataset-ref', default='latest', help='Resolved to an immutable digest before launch.')
    parser.add_argument('--taskset-dir', type=Path, help='Reuse a previously frozen official taskset after verifying its bytes.')
    parser.add_argument('--concurrency', type=int, default=1)
    parser.add_argument('--max-retries', type=int, default=0)
    parser.add_argument('--model')
    parser.add_argument('--reasoning-effort')
    parser.add_argument('--provider', choices=['anthropic', 'openai_responses', 'deepseek'])
    parser.add_argument('--base-url')
    parser.add_argument('--max-output-tokens', type=int)
    parser.add_argument('--context-window', type=int)
    parser.add_argument('--env-file', type=Path, default=Path.cwd() / '.env')
    parser.add_argument(
        '--output-dir', type=Path,
        default=default_output_dir,
    )
    parser.add_argument(
        '--cache-dir',
        type=Path,
        default=evaluation_directory() / 'cache',
        help='Harbor/uv cache directory outside the checkout.',
    )
    parser.add_argument('--agent', choices=[AGENT, 'oracle'], default=AGENT)
    parser.add_argument('--source-dir', type=Path, default=PROJECT_ROOT,
                        help='Source checkout to freeze for a controlled baseline/candidate comparison.')
    parser.add_argument('--max-model-calls', type=budget_limit, default=120)
    parser.add_argument('--max-tool-calls', type=budget_limit, default=240)
    parser.add_argument('--max-turn-seconds', type=budget_limit, default=1800,
                        help='Agent limit or unlimited; official benchmark deadlines always apply.')
    parser.add_argument('--agent-setup-timeout-multiplier', type=int, default=12)
    parser.add_argument('--timeout-multiplier', type=float)
    parser.add_argument('--force-build', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    return parser


def preconditions(source: Path) -> dict:
    try:
        harbor_version = version('harbor')
    except PackageNotFoundError:
        harbor_version = None
    try:
        harbor_executable()
        command_ready = True
    except RuntimeError:
        command_ready = False
    docker_ready = False
    if shutil.which('docker'):
        try:
            docker_ready = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'],
                capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {'linux': sys.platform == 'linux', 'harbor_0_18_0': harbor_version == '0.18.0',
            'harbor_command': command_ready,
            'docker_access': docker_ready, 'source_lock': (source / 'uv.lock').is_file()}


def effective_configuration(args) -> tuple[ForgeConfig | None, dict]:
    overrides = {key: str(value) for key, value in {
        'FORGE_PROVIDER': args.provider, 'FORGE_MODEL': args.model, 'FORGE_BASE_URL': args.base_url,
        'FORGE_REASONING_EFFORT': args.reasoning_effort, 'MODEL_MAX_TOKENS': args.max_output_tokens,
        'MODEL_CONTEXT_WINDOW': args.context_window}.items() if value is not None}
    config = None if args.agent == 'oracle' else ForgeConfig.from_env(model_environment(args.env_file, overrides))
    model = {key: value for key, value in asdict(config).items() if key != 'api_key'} if config else None
    if model:
        # Keep the existing evaluation context limit explicit when the user has not set one.
        model['context_window'] = config.context_window or 128000
    budget = {'max_model_calls': args.max_model_calls, 'max_tool_calls': args.max_tool_calls,
              'wall_seconds': args.max_turn_seconds}
    return config, {'schema_version': 'forge.evaluation.v1', 'dataset': args.dataset,
        'dataset_ref': args.dataset_ref, 'selection': {'tasks': args.task, 'n_tasks': args.n_tasks, 'all': args.all},
        'harbor_version': '0.18.0', 'usage': 'evaluation-only', 'model': model,
        'agent': args.agent, 'concurrency': args.concurrency, 'attempts': 1, 'task_retries': args.max_retries,
        'budget': budget, 'timeout_multiplier': args.timeout_multiplier or 1,
        'agent_setup_timeout_multiplier': args.agent_setup_timeout_multiplier,
        'grader': {'harbor_version': '0.18.0', 'protocol': 'official-task-verifier',
                   'env': {'UV_HTTP_TIMEOUT': '120', 'UV_CACHE_DIR': '/opt/forgecode-cache',
                           'UV_PYTHON_INSTALL_DIR': '/opt/forgecode-cache/python'}},
        'parameters': {'temperature': None, 'top_p': None, 'max_output_tokens': config.max_tokens,
                       'reasoning_effort': config.reasoning_effort} if config else None,
        'harness': {'max_context_tokens': model['context_window'], 'compaction_enabled': True,
                    'max_delivery_repairs': 2, 'parent_budget': budget, 'explore_enabled': True,
                    'trusted_extensions_enabled': False} if config else None}


def frozen_job(args, manifest, taskset, source, output):
    """Use the pinned Harbor models, with no moving dataset resolution at launch."""
    from harbor.models.job.config import JobConfig, RetryConfig
    from harbor.models.metric.config import MetricConfig
    from harbor.models.trial.config import AgentConfig, EnvironmentConfig, TaskConfig, VerifierConfig
    from benchmark.adapters.materialize import resolve_task
    frozen = {key: manifest[key] for key in ('run_id', 'parameters', 'harness', 'source_manifest', 'model')}
    if frozen['model']:
        frozen['model'] = {**frozen['model'], 'base_url': container_base_url(frozen['model']['base_url'])}
    model = frozen['model']
    env = {'FORGECODE_API_KEY': '${FORGECODE_API_KEY}',
           'FORGECODE_PROVIDER': model['provider'], 'FORGECODE_MODEL': model['model_id'],
           'FORGECODE_BASE_URL': model['base_url'], 'FORGECODE_MODEL_MAX_TOKENS': str(model['max_tokens']),
           'FORGECODE_CONTEXT_WINDOW': str(model['context_window']),
           'FORGECODE_REASONING_EFFORT': model['reasoning_effort'] or '',
           'MODEL_REQUEST_TIMEOUT_SECONDS': str(model['request_timeout_seconds'])} if model else {}
    agent = AgentConfig(name='oracle', max_timeout_sec=args.max_turn_seconds) if args.agent == 'oracle' else AgentConfig(import_path=AGENT,
        model_name=model['model_id'], max_timeout_sec=args.max_turn_seconds, env=env,
        kwargs={'source_dir': str(source), 'frozen_configuration': json.dumps(frozen),
                'max_model_calls': args.max_model_calls, 'max_tool_calls': args.max_tool_calls,
                'max_turn_seconds': args.max_turn_seconds})
    return JobConfig(job_name='job', jobs_dir=output, n_attempts=1, n_concurrent_trials=args.concurrency,
        metrics=[MetricConfig()],
        verifier=VerifierConfig(env=manifest['grader']['env']),
        retry=RetryConfig(max_retries=args.max_retries), timeout_multiplier=args.timeout_multiplier or 1,
        agent_setup_timeout_multiplier=args.agent_setup_timeout_multiplier,
        environment=EnvironmentConfig(import_path='benchmark.adapters.docker:EvaluationDockerEnvironment',
            kwargs={'receipt_path': str(output / 'resources.jsonl')},
            force_build=args.force_build, mounts=[{'type': 'bind', 'source': str(args.cache_dir.resolve()),
                                                 'target': '/opt/forgecode-cache'}]),
        agents=[agent], tasks=[TaskConfig(path=resolve_task(output / 'tasks', taskset, name),
                                          source=args.dataset) for name in taskset['tasks']])


def main(argv: Sequence[str] | None = None, *, default_dataset: str | None = None,
         default_output_dir: Path | None = None) -> int:
    parser = build_parser(default_dataset=default_dataset, default_output_dir=default_output_dir)
    args = parser.parse_args(argv)
    for name in ('concurrency', 'agent_setup_timeout_multiplier'):
        if getattr(args, name) < 1:
            parser.error(f'--{name.replace("_", "-")} must be positive')
    if args.n_tasks is not None and args.n_tasks < 1 or args.max_retries < 0:
        parser.error('Task count must be positive and retries non-negative.')
    if args.timeout_multiplier is not None and not 0 < args.timeout_multiplier <= 1:
        parser.error('Timeout multiplier must be in (0, 1]; official benchmark deadlines cannot be extended.')
    try:
        config, manifest = effective_configuration(args)
    except ValueError as error:
        parser.error(str(error))
    checks = preconditions(args.source_dir)
    output = (args.output_dir or evaluation_directory() / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:8])).expanduser().resolve()
    if args.dry_run:
        print(json.dumps({**manifest, 'output_dir': str(output), 'preconditions': checks,
                          'ready': all(checks.values()), 'model_calls': 0}, indent=2))
        return 0
    if not all(checks.values()):
        parser.error('Unmet prerequisites: ' + ', '.join(key for key, ready in checks.items() if not ready))
    if config and not local_provider_available(config.base_url):
        parser.error('The configured local model provider is not listening.')
    from benchmark.adapters.materialize import materialize_dataset, reuse_taskset
    from benchmark.harbor.snapshot import freeze_source
    from benchmark.harbor.controller import run_controller
    output.mkdir(parents=True, exist_ok=False)
    manifest['run_id'] = 'run-' + str(uuid4())
    selection = dict(dataset=args.dataset, ref=args.dataset_ref, task_names=args.task,
                     n_tasks=args.n_tasks, destination=output / 'tasks')
    taskset = (reuse_taskset(args.taskset_dir, **selection) if args.taskset_dir else
               asyncio.run(materialize_dataset(**selection)))
    source = freeze_source(args.source_dir, output / '.source-snapshots')
    driver = source if args.source_dir.resolve() == PROJECT_ROOT else freeze_source(PROJECT_ROOT, output / '.driver-snapshots')
    manifest.update(source_manifest=json.loads((source.parent / 'manifest.json').read_text(encoding='utf-8')),
                    driver_manifest=json.loads((driver.parent / 'manifest.json').read_text(encoding='utf-8')),
                    taskset=taskset)
    job = frozen_job(args, manifest, taskset, source, output)
    (output / 'experiment.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    configuration = output / 'harbor.json'
    # All credentials here are templates. Preserve numeric *_TOKENS settings;
    # Harbor's default sensitive-key serializer otherwise redacts them too.
    configuration.write_text(job.model_dump_json(indent=2, context={'redact_sensitive_env': False}) + '\n', encoding='utf-8')
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    command = [harbor_executable(), 'run', '--config', str(configuration), '-y']
    command += ['--plugin', 'benchmark.harbor.deadline_plugin:ForgeCodeDeadlinePlugin']
    env = {**os.environ, 'PYTHONPATH': str(driver), 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    if config:
        env['FORGECODE_API_KEY'] = config.api_key
    print(f'Frozen evaluation: {output}', flush=True)
    return run_controller(command, cwd=output, env=env, output_dir=output,
                          metadata={'run_id': manifest['run_id'], 'experiment': 'experiment.json'})


if __name__ == '__main__':
    raise SystemExit(main())
