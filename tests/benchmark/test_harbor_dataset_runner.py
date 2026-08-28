from __future__ import annotations

import json
from pathlib import Path

from benchmark.harbor.run_dataset import build_command, container_base_url
from benchmark.harbor.summarize import summarize_run


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
