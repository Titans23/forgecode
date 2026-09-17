'''Run a ForgeCode Harbor agent against a registered Harbor dataset.'''

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from typing import Sequence
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def build_command(
    *,
    harbor: str,
    dataset: str,
    env_file: Path,
    output_dir: Path,
    cache_dir: Path,
    model: str,
    base_url: str,
    agent: str = 'benchmark.harbor.forgecode_agent:ForgeCodeHarborAgent',
    tasks: tuple[str, ...] = (),
    n_tasks: int | None = None,
    concurrency: int = 1,
    max_retries: int = 1,
    max_model_calls: int = 120,
    max_tool_calls: int = 240,
    max_turn_seconds: int = 1800,
    agent_setup_timeout_multiplier: int = 12,
    install_retries: int = 8,
    install_retry_delay_seconds: int = 30,
    timeout_multiplier: float | None = None,
    environment: str | None = None,
    force_build: bool = False,
    source_dir: Path | None = None,
    provider: str = 'anthropic',
    api_key_env: str | None = None,
    reasoning_effort: str | None = None,
) -> list[str]:
    '''Build a Harbor command without exposing credentials in argv.'''
    credential_names = {'anthropic': 'ANTHROPIC_API_KEY', 'openai_responses': 'OPENAI_API_KEY',
                        'deepseek': 'DEEPSEEK_API_KEY'}
    if provider not in credential_names:
        raise ValueError('Unsupported model provider')
    credential = api_key_env or credential_names[provider]
    if credential not in {*credential_names.values(), 'FORGE_API_KEY'}:
        raise ValueError('Unsupported credential environment variable')
    mounts = json.dumps(
        [{
            'type': 'bind',
            'source': str(cache_dir.resolve()),
            'target': '/opt/forgecode-cache',
        }],
        separators=(',', ':'),
    )
    command = [
        harbor,
        'run',
        '-d',
        dataset,
        '-a',
        agent,
        '-m',
        model,
        '-n',
        str(concurrency),
        '-k',
        '1',
        '-r',
        str(max_retries),
        '--ak',
        f'max_model_calls={max_model_calls}',
        '--ak',
        f'max_tool_calls={max_tool_calls}',
        '--ak',
        f'max_turn_seconds={max_turn_seconds}',
        '--ak',
        f'install_retries={install_retries}',
        '--ak',
        f'install_retry_delay_seconds={install_retry_delay_seconds}',
        '--agent-setup-timeout-multiplier',
        str(agent_setup_timeout_multiplier),
        '--env-file',
        str(env_file.resolve()),
        '--ae',
        'FORGECODE_API_KEY=${' + credential + '}',
        '--ae',
        f'FORGECODE_MODEL={model}',
        '--ae',
        f'FORGECODE_BASE_URL={base_url}',
        '--ae',
        'FORGECODE_MODEL_MAX_TOKENS=16384',
        '--ae',
        'FORGECODE_CONTEXT_WINDOW=128000',
        '--mounts',
        mounts,
        '-o',
        str(output_dir.resolve()),
        '-y',
    ]
    command.extend(['--ae', f'FORGECODE_PROVIDER={provider}'])
    if reasoning_effort:
        command.extend(['--ae', f'FORGECODE_REASONING_EFFORT={reasoning_effort}'])
    if timeout_multiplier is not None:
        command.extend(['--timeout-multiplier', str(timeout_multiplier)])
    if agent == 'benchmark.harbor.forgecode_agent:ForgeCodeHarborAgent':
        command.extend(['--plugin', 'benchmark.harbor.deadline_plugin:ForgeCodeDeadlinePlugin'])
    if environment is not None:
        command.extend(['--env', environment])
    else:
        command.extend(['--env', 'benchmark.harbor.bounded_docker:BoundedDockerEnvironment'])
    for task in tasks:
        command.extend(['-i', task])
    if n_tasks is not None:
        command.extend(['-l', str(n_tasks)])
    if force_build:
        command.append('--force-build')
    if source_dir is not None:
        command.extend(['--ak', f'source_dir={source_dir.resolve()}'])
    return command


def configured_values(env_file: Path) -> dict[str, str]:
    '''Read model settings while allowing process env overrides.'''
    values = {
        key: value
        for key, value in dotenv_values(env_file).items()
        if value is not None
    }
    for key in ('FORGE_REASONING_EFFORT', 'MODEL_ID', 'ANTHROPIC_BASE_URL', 'FORGE_MODEL', 'FORGE_BASE_URL',
                'FORGE_PROVIDER', 'OPENAI_BASE_URL', 'DEEPSEEK_BASE_URL',
                'FORGE_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'DEEPSEEK_API_KEY'):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def harbor_executable() -> str:
    found = shutil.which('harbor')
    if found:
        return found
    candidate = Path(sys.executable).with_name('harbor.exe')
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
    parser.add_argument('--dataset', default=default_dataset, required=default_dataset is None)
    parser.add_argument('--task', action='append', default=[])
    parser.add_argument('--n-tasks', type=int)
    parser.add_argument('--concurrency', type=int, default=1)
    parser.add_argument('--max-retries', type=int, default=1)
    parser.add_argument('--model')
    parser.add_argument('--reasoning-effort')
    parser.add_argument('--provider', choices=['anthropic', 'openai_responses', 'deepseek'])
    parser.add_argument('--base-url')
    parser.add_argument('--env-file', type=Path, default=PROJECT_ROOT / '.env')
    parser.add_argument(
        '--output-dir', type=Path,
        default=default_output_dir or PROJECT_ROOT / 'benchmark' / 'runs' / 'harbor' / 'dataset',
    )
    parser.add_argument(
        '--cache-dir',
        type=Path,
        default=PROJECT_ROOT / 'benchmark' / '.cache' / 'harbor',
        help='Harbor/uv cache directory (defaults to an ignored project path).',
    )
    parser.add_argument('--agent', default='benchmark.harbor.forgecode_agent:ForgeCodeHarborAgent')
    parser.add_argument('--source-dir', type=Path, default=PROJECT_ROOT,
                        help='Source checkout to freeze for a controlled baseline/candidate comparison.')
    parser.add_argument('--max-model-calls', type=int, default=120)
    parser.add_argument('--max-tool-calls', type=int, default=240)
    parser.add_argument('--max-turn-seconds', type=int, default=1800)
    parser.add_argument('--agent-setup-timeout-multiplier', type=int, default=12)
    parser.add_argument('--install-retries', type=int, default=8)
    parser.add_argument('--install-retry-delay-seconds', type=int, default=30)
    parser.add_argument('--timeout-multiplier', type=float)
    parser.add_argument('--environment')
    parser.add_argument('--force-build', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    default_dataset: str | None = None,
    default_output_dir: Path | None = None,
) -> int:
    parser = build_parser(
        default_dataset=default_dataset,
        default_output_dir=default_output_dir,
    )
    args = parser.parse_args(argv)
    if args.concurrency < 1:
        parser.error('--concurrency must be positive')
    if args.n_tasks is not None and args.n_tasks < 1:
        parser.error('--n-tasks must be positive')
    if args.max_retries < 0:
        parser.error('--max-retries must not be negative')
    for name in (
        'max_model_calls', 'max_tool_calls', 'max_turn_seconds',
        'agent_setup_timeout_multiplier', 'install_retries',
        'install_retry_delay_seconds',
    ):
        if getattr(args, name) < 1:
            parser.error(f'--{name.replace("_", "-")} must be positive')
    if args.timeout_multiplier is not None and args.timeout_multiplier <= 0:
        parser.error('--timeout-multiplier must be positive')

    values = configured_values(args.env_file)
    provider = args.provider or values.get('FORGE_PROVIDER', 'anthropic')
    model = args.model or values.get('FORGE_MODEL') or values.get('MODEL_ID')
    endpoint_key = {'anthropic': 'ANTHROPIC_BASE_URL', 'openai_responses': 'OPENAI_BASE_URL',
                    'deepseek': 'DEEPSEEK_BASE_URL'}.get(provider)
    base_url = args.base_url or values.get('FORGE_BASE_URL') or values.get(endpoint_key or '')
    if base_url is None:
        base_url = {'openai_responses': 'https://api.openai.com/v1',
                    'deepseek': 'https://api.deepseek.com'}.get(provider)
    if not model or not base_url:
        parser.error('MODEL_ID and ANTHROPIC_BASE_URL must be configured')
    if not args.dry_run and not local_provider_available(base_url):
        parser.error(
            'The local model provider is not listening at '
            f'{base_url}. Start it before launching Docker benchmark tasks.'
        )
    command = build_command(
        harbor=harbor_executable(),
        dataset=args.dataset,
        env_file=args.env_file,
        output_dir=args.output_dir,
        cache_dir=args.cache_dir,
        model=model,
        base_url=container_base_url(base_url),
        provider=provider,
        reasoning_effort=args.reasoning_effort or values.get('FORGE_REASONING_EFFORT'),
        api_key_env='FORGE_API_KEY' if values.get('FORGE_API_KEY') else None,
        agent=args.agent,
        tasks=tuple(args.task),
        n_tasks=args.n_tasks,
        concurrency=args.concurrency,
        max_retries=args.max_retries,
        max_model_calls=args.max_model_calls,
        max_tool_calls=args.max_tool_calls,
        max_turn_seconds=args.max_turn_seconds,
        agent_setup_timeout_multiplier=args.agent_setup_timeout_multiplier,
        install_retries=args.install_retries,
        install_retry_delay_seconds=args.install_retry_delay_seconds,
        timeout_multiplier=args.timeout_multiplier,
        environment=args.environment,
        force_build=args.force_build,
    )
    if args.dry_run:
        print(json.dumps(command, ensure_ascii=False, indent=2))
        return 0
    from benchmark.harbor.snapshot import freeze_source

    # All trials, including Harbor retries, must install these same bytes.
    source_snapshot = freeze_source(args.source_dir, args.output_dir / '.source-snapshots')
    driver_snapshot = source_snapshot if args.source_dir.resolve() == PROJECT_ROOT.resolve() else freeze_source(
        PROJECT_ROOT, args.output_dir / '.driver-snapshots',
    )
    command.extend(['--ak', f'source_dir={source_snapshot}'])
    print(f'Frozen ForgeCode source: {source_snapshot}', flush=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    docker_bin = (
        Path(os.environ.get('LOCALAPPDATA', ''))
        / 'Programs' / 'DockerDesktop' / 'resources' / 'bin'
    )
    env['PATH'] = os.pathsep.join(
        item for item in (str(docker_bin), env.get('PATH', '')) if item
    )
    env['PYTHONPATH'] = os.pathsep.join(
        item for item in (str(driver_snapshot), env.get('PYTHONPATH', '')) if item
    )
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    from benchmark.harbor.controller import run_controller
    return run_controller(command, cwd=PROJECT_ROOT, env=env, output_dir=args.output_dir,
                          metadata={'source_snapshot': str(source_snapshot), 'concurrency': args.concurrency,
                                    'provider': provider, 'model': model,
                                    'reasoning_effort': args.reasoning_effort or values.get('FORGE_REASONING_EFFORT'),
                                    'reasoning_configuration': 'explicit' if args.reasoning_effort or values.get('FORGE_REASONING_EFFORT') else 'provider_default_unknown',
                                    'max_model_calls': args.max_model_calls, 'max_tool_calls': args.max_tool_calls,
                                    'kernel_max_seconds': args.max_turn_seconds,
                                    'outer_timeout': 'dataset agent timeout scaled by Harbor timeout_multiplier',
                                    'timeout_multiplier': args.timeout_multiplier or 1.0})


if __name__ == '__main__':
    raise SystemExit(main())
