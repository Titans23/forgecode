'''Build and run a reproducible ForgeCode Aider Polyglot Harbor job.'''

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MIRROR_MARKER = '# ForgeCode local Ubuntu mirror override'


def container_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.hostname not in {'localhost', '127.0.0.1'}:
        return value.rstrip('/')
    port = f':{parsed.port}' if parsed.port is not None else ''
    netloc = f'host.docker.internal{port}'
    return urlunsplit(
        (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
    ).rstrip('/')


def apply_ubuntu_mirror(
    task_names: tuple[str, ...],
    mirror: str,
    *,
    tasks_cache: Path | None = None,
) -> int:
    parsed = urlsplit(mirror)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.query:
        raise ValueError('Ubuntu mirror must be an absolute HTTPS URL')
    base = mirror.rstrip('/')
    cache = tasks_cache or Path.home() / '.cache' / 'harbor' / 'tasks'
    selected = set(task_names)
    changed = 0
    for dockerfile in cache.glob('*/polyglot_*/environment/Dockerfile'):
        if selected and dockerfile.parents[1].name not in selected:
            continue
        content = dockerfile.read_text(encoding='utf-8')
        if _MIRROR_MARKER in content:
            continue
        lines = content.splitlines(keepends=True)
        insertion = next(
            (index + 1 for index, line in enumerate(lines) if line.startswith('FROM ')),
            None,
        )
        if insertion is None:
            continue
        newline = '\r\n' if '\r\n' in content else '\n'
        override = (
            f'{_MIRROR_MARKER}{newline}'
            'RUN sed -i '
            f"'s|http://archive.ubuntu.com/ubuntu|{base}|g; "
            f"s|http://security.ubuntu.com/ubuntu|{base}|g' "
            f'/etc/apt/sources.list{newline}'
        )
        lines.insert(insertion, override)
        dockerfile.write_text(''.join(lines), encoding='utf-8', newline='')
        changed += 1
    return changed


def build_command(
    *,
    harbor: str,
    env_file: Path,
    output_dir: Path,
    cache_dir: Path,
    model: str,
    base_url: str,
    tasks: tuple[str, ...] = (),
    n_tasks: int | None = None,
    concurrency: int = 1,
    feedback: bool = True,
    max_model_calls: int = 120,
    max_tool_calls: int = 240,
    max_retries: int = 1,
    force_build: bool = False,
    agent_setup_timeout_multiplier: int = 4,
    install_retries: int = 5,
    install_retry_delay_seconds: int = 20,
) -> list[str]:
    mounts = json.dumps(
        [
            {
                'type': 'bind',
                'source': str(cache_dir.resolve()),
                'target': '/opt/forgecode-cache',
            }
        ],
        separators=(',', ':'),
    )
    command = [
        harbor,
        'run',
        '-d',
        'aider-polyglot',
        '-a',
        'benchmark.harbor.forgecode_agent:ForgeCodeHarborAgent',
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
        'max_turn_seconds=1800',
        '--ak',
        f'install_retries={install_retries}',
        '--ak',
        f'install_retry_delay_seconds={install_retry_delay_seconds}',
        '--agent-setup-timeout-multiplier',
        str(agent_setup_timeout_multiplier),
        '--env-file',
        str(env_file.resolve()),
        '--ae',
        'FORGECODE_API_KEY=${ANTHROPIC_API_KEY}',
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
    if feedback:
        command.extend(
            [
                '--plugin',
                'benchmark.harbor.aider_feedback_plugin:AiderFeedbackPlugin',
            ]
        )
    for task in tasks:
        command.extend(['-i', task])
    if n_tasks is not None:
        command.extend(['-l', str(n_tasks)])
    if force_build:
        command.append('--force-build')
    return command


def _configured_values(env_file: Path) -> dict[str, str]:
    values = {
        key: value
        for key, value in dotenv_values(env_file).items()
        if value is not None
    }
    for key in ('MODEL_ID', 'ANTHROPIC_BASE_URL'):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def _harbor_executable() -> str:
    found = shutil.which('harbor')
    if found:
        return found
    candidate = Path(sys.executable).with_name('harbor.exe')
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError('Harbor executable is not available in this environment.')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', action='append', default=[])
    parser.add_argument('--n-tasks', type=int)
    parser.add_argument('--concurrency', type=int, default=1)
    parser.add_argument('--max-retries', type=int, default=1)
    parser.add_argument(
        '--agent-setup-timeout-multiplier',
        type=int,
        default=4,
        help='Multiplier for ForgeCode installation/setup timeout.',
    )
    parser.add_argument('--install-retries', type=int, default=5)
    parser.add_argument('--install-retry-delay-seconds', type=int, default=20)
    parser.add_argument('--model')
    parser.add_argument('--base-url')
    parser.add_argument('--env-file', type=Path, default=PROJECT_ROOT / '.env')
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=PROJECT_ROOT / 'benchmark' / 'runs' / 'harbor' / 'aider',
    )
    parser.add_argument(
        '--cache-dir',
        type=Path,
        default=Path.home() / '.cache' / 'forgecode-harbor',
    )
    parser.add_argument('--no-feedback', action='store_true')
    parser.add_argument(
        '--force-build',
        action='store_true',
        help='Force a fresh Docker build for each selected task environment.',
    )
    parser.add_argument(
        '--ubuntu-mirror',
        help=(
            'Optional HTTPS Ubuntu mirror used only for task image package '
            'downloads; task sources and verifiers are unchanged.'
        ),
    )
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if args.concurrency < 1:
        parser.error('--concurrency must be positive')
    if args.n_tasks is not None and args.n_tasks < 1:
        parser.error('--n-tasks must be positive')
    if args.max_retries < 0:
        parser.error('--max-retries must not be negative')
    if args.agent_setup_timeout_multiplier < 1:
        parser.error('--agent-setup-timeout-multiplier must be positive')
    if args.install_retries < 1:
        parser.error('--install-retries must be positive')
    if args.install_retry_delay_seconds < 1:
        parser.error('--install-retry-delay-seconds must be positive')

    values = _configured_values(args.env_file)
    model = args.model or values.get('MODEL_ID')
    base_url = args.base_url or values.get('ANTHROPIC_BASE_URL')
    if not model or not base_url:
        parser.error('MODEL_ID and ANTHROPIC_BASE_URL must be configured')
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    if args.ubuntu_mirror:
        changed = apply_ubuntu_mirror(
            tuple(args.task),
            args.ubuntu_mirror,
        )
        print(f'Applied Ubuntu mirror override to {changed} task Dockerfile(s).')
    command = build_command(
        harbor=_harbor_executable(),
        env_file=args.env_file,
        output_dir=args.output_dir,
        cache_dir=args.cache_dir,
        model=model,
        base_url=container_base_url(base_url),
        tasks=tuple(args.task),
        n_tasks=args.n_tasks,
        concurrency=args.concurrency,
        feedback=not args.no_feedback,
        max_retries=args.max_retries,
        force_build=args.force_build,
        agent_setup_timeout_multiplier=args.agent_setup_timeout_multiplier,
        install_retries=args.install_retries,
        install_retry_delay_seconds=args.install_retry_delay_seconds,
    )
    if args.dry_run:
        print(json.dumps(command, ensure_ascii=False, indent=2))
        return 0

    env = dict(os.environ)
    docker_bin = (
        Path(os.environ.get('LOCALAPPDATA', ''))
        / 'Programs'
        / 'DockerDesktop'
        / 'resources'
        / 'bin'
    )
    env['PATH'] = os.pathsep.join(
        item for item in (str(docker_bin), env.get('PATH', '')) if item
    )
    env['PYTHONPATH'] = os.pathsep.join(
        item
        for item in (str(PROJECT_ROOT), env.get('PYTHONPATH', ''))
        if item
    )
    return subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
