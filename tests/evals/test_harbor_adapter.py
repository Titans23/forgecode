from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from benchmark.harbor.aider_feedback_plugin import AiderFeedbackPlugin
from benchmark.harbor.aider_feedback_trial import (
    build_aider_feedback,
    create_aider_feedback_trial,
    should_request_feedback_after_missing_reward,
    should_request_feedback_round,
)
from benchmark.harbor.forgecode_agent import (
    ForgeCodeHarborAgent,
    _git_baseline_command,
    _install_command,
)
from benchmark.harbor.summarize import summarize_run
from benchmark.harbor.run_aider import (
    apply_ubuntu_mirror,
    build_command,
    container_base_url,
)
from harbor.trial.trial import Trial


def test_adapter_builds_quoted_resumable_command(tmp_path: Path) -> None:
    agent = ForgeCodeHarborAgent(
        logs_dir=tmp_path,
        max_model_calls='77',
        max_tool_calls='155',
    )

    command = agent._run_command('Fix it.\nRun tests.', resume=True)

    assert '-m benchmark.harbor.run_forge' in command
    assert '--resume' in command
    assert '--max-model-calls 77' in command
    assert '--max-tool-calls 155' in command
    assert 'FORGECODE_API_KEY' in command
    assert 'ANTHROPIC_API_KEY=' in command
    assert 'FORGECODE_BENCHMARK_RESULT' not in command
    assert '/logs/agent/forgecode-repair.txt' in command
    assert 'export FORGE_DATA_DIR=/tmp/forgecode-harbor' in command
    assert '-exec cp {} /logs/agent/' in command
    assert 'git commit --quiet -m "Harbor evaluation baseline"' not in command


def test_adapter_can_run_with_uploaded_message_file(tmp_path: Path) -> None:
    agent = ForgeCodeHarborAgent(logs_dir=tmp_path)

    command = agent._run_command(
        resume=False,
        message_file='/tmp/forgecode-benchmark-instruction.txt',
    )

    assert '--message-file /tmp/forgecode-benchmark-instruction.txt' in command
    assert '--message ' not in command


def test_adapter_rejects_ambiguous_message_sources(tmp_path: Path) -> None:
    agent = ForgeCodeHarborAgent(logs_dir=tmp_path)

    with pytest.raises(ValueError, match='exactly one'):
        agent._run_command()
    with pytest.raises(ValueError, match='exactly one'):
        agent._run_command('inline', message_file='/tmp/message')


def test_first_turn_creates_git_baseline_but_resume_does_not(
    tmp_path: Path,
) -> None:
    agent = ForgeCodeHarborAgent(logs_dir=tmp_path)

    first = agent._run_command('Fix it.')
    resumed = agent._run_command('Tests failed.', resume=True)

    baseline = _git_baseline_command()
    assert baseline in first
    assert baseline not in resumed
    assert '.forge/' in first
    assert 'git config core.whitespace cr-at-eol' in first
    assert "-exec sed -i 's/\\r$//'" in first
    assert '.gradle/' in first
    assert 'node_modules/' in first
    assert 'target/' in first


def test_adapter_stages_no_secrets_or_local_state(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    (source / 'forge').mkdir(parents=True)
    (source / 'benchmark' / 'harbor').mkdir(parents=True)
    (source / 'benchmark' / 'runs' / 'old').mkdir(parents=True)
    (source / 'pyproject.toml').write_text('[project]\nname="forge-code"\n')
    (source / 'README.md').write_text('# ForgeCode\n')
    (source / 'forge' / '__init__.py').write_text('')
    (source / 'benchmark' / '__init__.py').write_text('')
    (source / 'benchmark' / 'harbor' / '__init__.py').write_text('')
    (source / 'benchmark' / 'runs' / 'old' / 'result.json').write_text('{}')
    (source / '.env').write_text('SECRET=not-copied\n')

    agent = ForgeCodeHarborAgent(
        logs_dir=tmp_path / 'logs', source_dir=source
    )
    staged = agent._stage_local_source()

    assert (staged / 'forge' / '__init__.py').is_file()
    assert (staged / 'benchmark' / '__init__.py').is_file()
    assert (staged / 'benchmark' / 'harbor' / '__init__.py').is_file()
    assert not (staged / 'benchmark' / 'runs').exists()
    assert not (staged / '.env').exists()


def test_install_uses_python_312_and_shared_cache() -> None:
    command = _install_command('/installed-agent/forgecode-src')

    assert 'python install 3.12' in command
    assert 'python find 3.12' in command
    assert 'CACHE_DIR=/opt/forgecode-cache' in command
    assert command.count('--clear') == 1
    assert 'while true; do' in command
    assert 'attempt" -ge 3' in command


def test_feedback_requires_real_zero_reward() -> None:
    assert should_request_feedback_round({'reward': 0})
    assert not should_request_feedback_round({'reward': 1})
    assert not should_request_feedback_round(None)
    assert should_request_feedback_after_missing_reward(
        'error: compile failed\nCMake build failed'
    )
    assert not should_request_feedback_after_missing_reward('network failed')


def test_feedback_preserves_output_and_protects_tests() -> None:
    feedback = build_aider_feedback('FAIL expected 4 got 3')

    assert 'Do not modify the tests' in feedback
    assert 'FAIL expected 4 got 3' in feedback


def test_feedback_factory_ignores_multistep_tasks() -> None:
    class FakeTask:
        has_steps = True

    class FakeTrial:
        @classmethod
        async def _load_task(cls, config):
            return FakeTask(), 'download'

    assert asyncio.run(create_aider_feedback_trial(FakeTrial, object())) is None


def test_feedback_plugin_restores_trial_factory() -> None:
    original = Trial.__dict__['create']
    plugin = AiderFeedbackPlugin()

    async def exercise() -> None:
        await plugin.on_job_start(object())
        assert Trial.__dict__['create'] is not original
        await plugin.on_job_end(None)

    asyncio.run(exercise())
    assert Trial.__dict__['create'] is original


def test_adapter_rejects_invalid_limits(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match='max_model_calls'):
        ForgeCodeHarborAgent(logs_dir=tmp_path, max_model_calls=0)
    with pytest.raises(ValueError, match='max_turn_seconds'):
        ForgeCodeHarborAgent(logs_dir=tmp_path, max_turn_seconds='nan')


def test_summary_separates_first_attempt_repair_and_infrastructure(
    tmp_path: Path,
) -> None:
    passed = tmp_path / 'passed'
    (passed / 'agent').mkdir(parents=True)
    (passed / 'verifier').mkdir()
    (passed / 'result.json').write_text(
        json.dumps(
            {
                'exception_info': None,
                'verifier_result': {'rewards': {'reward': 1.0}},
            }
        )
    )
    (passed / 'verifier' / 'aider-attempts.json').write_text(
        json.dumps(
            {
                'first_reward': 0.0,
                'feedback_requested': True,
                'missing_reward_compile_failure': False,
            }
        )
    )
    payload = {
        'status': 'completed',
        'model_calls': 2,
        'tool_calls': 3,
        'usage': {'input_tokens': 10, 'output_tokens': 4},
    }
    (passed / 'agent' / 'forgecode-repair.txt').write_text(
        'FORGECODE_BENCHMARK_RESULT=' + json.dumps(payload) + '\n'
    )
    failed = tmp_path / 'infra'
    failed.mkdir()
    (failed / 'result.json').write_text(
        json.dumps(
            {
                'exception_info': {'exception_type': 'RuntimeError'},
                'verifier_result': None,
            }
        )
    )

    summary = summarize_run(tmp_path)

    assert summary.total_trials == 2
    assert summary.infrastructure_failures == 1
    assert summary.infrastructure_failure_types == {'RuntimeError': 1}
    assert summary.pass_at_1 == 0
    assert summary.pass_at_2 == 1
    assert summary.repaired == 1
    assert summary.internal_statuses == {'completed': 1}
    assert summary.input_tokens == 10


def test_summary_ignores_reward_attached_to_infrastructure_error(
    tmp_path: Path,
) -> None:
    trial = tmp_path / 'rate-limited'
    trial.mkdir()
    (trial / 'result.json').write_text(
        json.dumps(
            {
                'exception_info': {'exception_type': 'ApiRateLimitError'},
                'verifier_result': {'rewards': {'reward': 0.0}},
            }
        )
    )

    summary = summarize_run(tmp_path)

    assert summary.infrastructure_failures == 1
    assert summary.infrastructure_failure_types == {'ApiRateLimitError': 1}
    assert summary.scored_trials == 0
    assert summary.pass_at_2 == 0


def test_summary_counts_cpp_compile_failure_without_reward_as_attempt(
    tmp_path: Path,
) -> None:
    trial = tmp_path / 'cpp'
    (trial / 'verifier').mkdir(parents=True)
    (trial / 'result.json').write_text(
        json.dumps(
            {
                'exception_info': None,
                'verifier_result': {'rewards': {'reward': 1.0}},
            }
        )
    )
    (trial / 'verifier' / 'aider-attempts.json').write_text(
        json.dumps(
            {
                'first_reward': None,
                'feedback_requested': True,
                'missing_reward_compile_failure': True,
            }
        )
    )

    summary = summarize_run(tmp_path)

    assert summary.first_attempt_known == 1
    assert summary.pass_at_1 == 0
    assert summary.pass_at_2 == 1
    assert summary.repaired == 1


def test_aider_runner_passes_mount_as_one_json_argument(tmp_path: Path) -> None:
    command = build_command(
        harbor='harbor',
        env_file=tmp_path / '.env',
        output_dir=tmp_path / 'out',
        cache_dir=tmp_path / 'cache',
        model='gpt-test',
        base_url='http://host.docker.internal:54982',
        tasks=('polyglot_python_proverb',),
        concurrency=2,
    )

    mounts = json.loads(command[command.index('--mounts') + 1])
    assert mounts[0]['target'] == '/opt/forgecode-cache'
    assert command[command.index('-n') + 1] == '2'
    assert command[command.index('-r') + 1] == '1'
    assert command[-2:] == ['-i', 'polyglot_python_proverb']
    assert 'FORGECODE_API_KEY=${ANTHROPIC_API_KEY}' in command


def test_aider_runner_can_force_environment_rebuild(tmp_path: Path) -> None:
    command = build_command(
        harbor='harbor',
        env_file=tmp_path / '.env',
        output_dir=tmp_path / 'out',
        cache_dir=tmp_path / 'cache',
        model='gpt-test',
        base_url='http://host.docker.internal:54982',
        force_build=True,
    )

    assert '--force-build' in command


def test_aider_runner_passes_install_retry_settings(tmp_path: Path) -> None:
    command = build_command(
        harbor='harbor',
        env_file=tmp_path / '.env',
        output_dir=tmp_path / 'out',
        cache_dir=tmp_path / 'cache',
        model='gpt-test',
        base_url='http://host.docker.internal:54982',
        install_retries=9,
        install_retry_delay_seconds=33,
    )

    assert 'install_retries=9' in command
    assert 'install_retry_delay_seconds=33' in command


def test_aider_runner_configures_setup_timeout_multiplier(tmp_path: Path) -> None:
    command = build_command(
        harbor='harbor',
        env_file=tmp_path / '.env',
        output_dir=tmp_path / 'out',
        cache_dir=tmp_path / 'cache',
        model='gpt-test',
        base_url='http://host.docker.internal:54982',
        agent_setup_timeout_multiplier=9,
    )

    index = command.index('--agent-setup-timeout-multiplier')
    assert command[index + 1] == '9'


def test_container_base_url_only_rewrites_loopback() -> None:
    assert container_base_url('http://localhost:54982') == (
        'http://host.docker.internal:54982'
    )
    assert container_base_url('https://provider.example/v1') == (
        'https://provider.example/v1'
    )


def test_ubuntu_mirror_override_only_changes_selected_task(
    tmp_path: Path,
) -> None:
    selected = (
        tmp_path
        / 'hash-one'
        / 'polyglot_cpp_demo'
        / 'environment'
        / 'Dockerfile'
    )
    other = (
        tmp_path
        / 'hash-two'
        / 'polyglot_java_demo'
        / 'environment'
        / 'Dockerfile'
    )
    for path in (selected, other):
        path.parent.mkdir(parents=True)
        path.write_text(
            'FROM buildpack-deps:jammy\nRUN apt-get update\n',
            encoding='utf-8',
        )

    changed = apply_ubuntu_mirror(
        ('polyglot_cpp_demo',),
        'https://mirror.example/ubuntu',
        tasks_cache=tmp_path,
    )

    assert changed == 1
    assert 'mirror.example' in selected.read_text(encoding='utf-8')
    assert 'mirror.example' not in other.read_text(encoding='utf-8')
    assert apply_ubuntu_mirror(
        ('polyglot_cpp_demo',),
        'https://mirror.example/ubuntu',
        tasks_cache=tmp_path,
    ) == 0
