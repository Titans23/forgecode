from __future__ import annotations

import json
from pathlib import Path

from benchmark.harbor.run_dataset import build_command, container_base_url
import benchmark.harbor.run_terminal as terminal_runner
from benchmark.harbor.summarize import summarize_run
from benchmark.harbor.snapshot import freeze_source


def test_generic_harbor_command_uses_dataset_and_safe_env_mapping(
    tmp_path: Path,
) -> None:
    command = build_command(
        harbor='harbor',
        dataset='swe-bench/swe-bench-verified',
        env_file=tmp_path / '.env',
        output_dir=tmp_path / 'out',
        cache_dir=tmp_path / 'cache',
        model='gpt-test',
        base_url='http://host.docker.internal:54982',
        tasks=('repo__issue-1',),
        n_tasks=2,
        concurrency=3,
        timeout_multiplier=2.5,
        force_build=True,
    )

    assert command[command.index('-d') + 1] == 'swe-bench/swe-bench-verified'
    assert command[command.index('-a') + 1].endswith(
        'benchmark.harbor.forgecode_agent:ForgeCodeHarborAgent'
    )
    assert command[command.index('-i') + 1] == 'repo__issue-1'
    assert command[command.index('-l') + 1] == '2'
    assert command[command.index('-n') + 1] == '3'
    assert '--timeout-multiplier' in command
    assert '--force-build' in command
    assert 'FORGECODE_API_KEY=${ANTHROPIC_API_KEY}' in command
    assert not any('sk-' in value for value in command)


def test_loopback_provider_is_rewritten_only_for_containers() -> None:
    assert container_base_url('http://localhost:1234/v1') == (
        'http://host.docker.internal:1234/v1'
    )


def test_terminal_entrypoint_uses_full_fixed_task_ids_and_concurrency(
    monkeypatch,
) -> None:
    received: list[str] = []

    def fake_run_dataset(argv, **kwargs) -> int:
        received.extend(argv)
        assert kwargs['default_dataset'] == 'terminal-bench/terminal-bench-2'
        return 0

    monkeypatch.setattr(terminal_runner, 'run_dataset', fake_run_dataset)

    assert terminal_runner.main(()) == 0

    task_values = [
        received[index + 1]
        for index, value in enumerate(received[:-1])
        if value == '--task'
    ]
    assert task_values == list(terminal_runner.FIXED_TERMINAL_TASKS)
    assert '--concurrency' in received
    assert received[received.index('--concurrency') + 1] == '6'
    assert received[received.index('--max-retries') + 1] == '3'
    assert received[received.index('--model') + 1] == 'gpt-5.6-luna'
    assert container_base_url('https://api.example/v1') == (
        'https://api.example/v1'
    )


def test_summary_counts_single_attempt_benchmark_results(tmp_path: Path) -> None:
    trial = tmp_path / 'trial'
    trial.mkdir()
    (trial / 'result.json').write_text(
        json.dumps({
            'exception_info': None,
            'verifier_result': {'rewards': {'reward': 1.0}},
        }),
        encoding='utf-8',
    )

    summary = summarize_run(tmp_path)

    assert summary.scored_trials == 1
    assert summary.first_attempt_known == 1
    assert summary.pass_at_1 == 1
    assert summary.pass_at_2 == 1
    assert summary.to_dict()['final_pass_rate'] == 1.0


def test_unfinished_job_does_not_label_pending_trial_an_agent_failure(tmp_path: Path) -> None:
    (tmp_path / 'result.json').write_text(json.dumps({'finished_at': None}), encoding='utf-8')
    trial = tmp_path / 'pending-trial'
    trial.mkdir()
    (trial / 'config.json').write_text('{}', encoding='utf-8')

    summary = summarize_run(tmp_path)

    assert summary.missing_results == ('pending-trial',)
    assert summary.unfinished_trials == ('pending-trial',)
    assert summary.job_finished is False
    assert summary.agent_failures == summary.infrastructure_failures == 0


def test_unfinished_job_still_reports_observed_agent_timeout(tmp_path: Path) -> None:
    (tmp_path / 'result.json').write_text(json.dumps({'finished_at': None}), encoding='utf-8')
    trial = tmp_path / 'timeout-trial'
    (trial / 'agent').mkdir(parents=True)
    (trial / 'config.json').write_text('{}', encoding='utf-8')
    (trial / 'agent' / 'forgecode-status.json').write_text(
        json.dumps({'exit_code': 124, 'timed_out': True}), encoding='utf-8',
    )
    summary = summarize_run(tmp_path)
    assert summary.unfinished_trials == ()
    assert summary.agent_timeouts == 1
    assert summary.job_finished is False


def test_snapshot_pins_package_bytes_and_excludes_environment_secrets(tmp_path: Path) -> None:
    source = tmp_path / 'repo'
    for relative, content in {
        'pyproject.toml': '[project]\nname="probe"',
        'README.md': 'probe',
        'forge/runner.py': 'VERSION = 1',
        'benchmark/__init__.py': '',
        'benchmark/harbor/entry.py': 'VERSION = 1',
        '.env': 'DO_NOT_COPY_SECRET=value',
        'benchmark/runs/private.txt': 'excluded',
        'forge/__pycache__/probe.pyc': 'excluded',
    }.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    frozen = freeze_source(source, tmp_path / 'snapshots')
    (source / 'forge/runner.py').write_text('VERSION = 2', encoding='utf-8')

    assert (frozen / 'forge/runner.py').read_text() == 'VERSION = 1'
    assert not (frozen / '.env').exists()
    assert not (frozen / 'benchmark/runs').exists()
    assert not (frozen / 'forge/__pycache__').exists()
    manifest = json.loads((frozen.parent / 'manifest.json').read_text())
    assert 'forge/runner.py' in manifest['files']
    assert len(manifest['content_sha256']) == 64


def test_command_passes_frozen_source_to_every_harbor_trial(tmp_path: Path) -> None:
    command = build_command(
        harbor='harbor', dataset='probe', env_file=tmp_path / '.env',
        output_dir=tmp_path / 'runs', cache_dir=tmp_path / 'cache',
        model='probe', base_url='http://example.test', source_dir=tmp_path / 'frozen',
    )
    assert f'source_dir={(tmp_path / "frozen").resolve()}' in command


def test_summary_distinguishes_kernel_deadline_and_verifier_timeout(tmp_path: Path):
    for name, exception in [('kernel', None), ('verifier', {'exception_type': 'VerifierTimeoutError'})]:
        trial = tmp_path / name
        (trial / 'agent').mkdir(parents=True)
        (trial / 'result.json').write_text(json.dumps({
            'exception_info': exception, 'verifier_result': {'rewards': {'reward': 0}},
        }), encoding='utf-8')
    (tmp_path / 'kernel/agent/forgecode.txt').write_text(
        'FORGECODE_BENCHMARK_RESULT=' + json.dumps({'status': 'failed', 'stop_reason': 'time_budget_exhausted'}),
        encoding='utf-8',
    )
    summary = summarize_run(tmp_path)
    assert summary.agent_timeouts == 1
    assert summary.verifier_environment_failures == 1
    assert summary.agent_failures == 0
    assert summary.raw_rewards == {'kernel': 0.0, 'verifier': 0.0}
