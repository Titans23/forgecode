from __future__ import annotations

import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark.harbor.run_dataset import container_base_url
import benchmark.harbor.run_terminal as terminal_runner
from benchmark.harbor.summarize import summarize_run
from benchmark.harbor.snapshot import freeze_source




def test_loopback_provider_is_rewritten_only_for_containers() -> None:
    assert container_base_url('http://localhost:1234/v1') == (
        'http://host.docker.internal:1234/v1'
    )


def test_preparation_requires_no_model_and_rejects_stale_or_incomplete_images(monkeypatch):
    from benchmark.harbor import prepare, run_dataset
    monkeypatch.setattr(run_dataset.ForgeConfig, 'from_env', lambda *args: pytest.fail('Preparation read model credentials'))
    args = run_dataset.build_parser(default_dataset='terminal-bench/terminal-bench-2').parse_args(['--all', '--prepare-only'])
    config, _ = run_dataset.effective_configuration(args)
    assert config is None
    digest, base, image = 'a' * 64, 'sha256:' + 'b' * 64, 'sha256:' + 'c' * 64
    records = {'official:fixture': {'id': base}, image: {'id': image, 'config': {'Labels': {
        prepare.SOURCE_LABEL: digest, prepare.BASE_LABEL: base}}}}
    monkeypatch.setattr(prepare, 'image_info', records.__getitem__)
    manifest = {'schema_version': 1, 'source_sha256': digest,
                'images': {'official:fixture': {'base_id': base, 'image_id': image}}}
    assert prepare.validate_prepared(manifest, digest, {'official:fixture'}) == {'official:fixture': image}
    with pytest.raises(ValueError, match='frozen source'):
        prepare.validate_prepared(manifest, 'd' * 64, {'official:fixture'})
    with pytest.raises(ValueError, match='cover all'):
        prepare.validate_prepared(manifest, digest, {'missing:fixture'})
    manifest['verifier_cache_image_id'] = 'sha256:cache'
    with pytest.raises(ValueError, match='provenance mismatch'):
        prepare.validate_prepared(manifest, digest, {'official:fixture'})
    records[image]['config']['Labels'][prepare.CACHE_LABEL] = 'sha256:cache'
    assert prepare.validate_prepared(manifest, digest, {'official:fixture'}) == {'official:fixture': image}
    records['official:fixture']['id'] = 'sha256:' + 'e' * 64
    with pytest.raises(ValueError, match='provenance mismatch'):
        prepare.validate_prepared(manifest, digest, {'official:fixture'})


def test_prepared_environment_preserves_official_constraints(tmp_path, monkeypatch):
    from benchmark.adapters.docker import EvaluationDockerEnvironment, BoundedDockerEnvironment
    from harbor.models.task.config import EnvironmentConfig
    original = EnvironmentConfig(docker_image='official:fixture', cpus=2, memory_mb=1024, allow_internet=False)
    received = {}
    monkeypatch.setattr(BoundedDockerEnvironment, '__init__', lambda self, **kwargs: received.update(kwargs))
    EvaluationDockerEnvironment(receipt_path=tmp_path / 'receipt', task_env_config=original,
                                prepared_images={'official:fixture': 'sha256:prepared'})
    assert original.docker_image == 'official:fixture'
    changed = received['task_env_config']
    assert changed.docker_image == 'sha256:prepared'
    assert changed.model_dump(exclude={'docker_image'}) == original.model_dump(exclude={'docker_image'})
    with pytest.raises(ValueError, match='not prepared'):
        EvaluationDockerEnvironment(receipt_path=tmp_path / 'receipt', task_env_config=original,
                                    prepared_images={'missing:fixture': 'sha256:prepared'})


def test_terminal_entrypoint_does_not_inject_model_tasks_or_concurrency(
    monkeypatch,
) -> None:
    received: list[str] = []

    def fake_run_dataset(argv, **kwargs) -> int:
        received.extend(argv)
        assert kwargs['default_dataset'] == 'terminal-bench/terminal-bench-2'
        return 0

    monkeypatch.setattr(terminal_runner, 'run_dataset', fake_run_dataset)

    assert terminal_runner.main(()) == 0

    assert received == []
    assert container_base_url('https://api.example/v1') == (
        'https://api.example/v1'
    )


@pytest.mark.parametrize('dataset', ['terminal-bench/terminal-bench-2', 'swe-bench/swe-bench-verified'])
def test_dry_run_freezes_shared_configuration_without_starting_a_job(tmp_path, monkeypatch, capsys, dataset):
    from benchmark.harbor import run_dataset as runner
    import forge.config as settings
    monkeypatch.setattr(settings.os, 'environ', {'MODEL_ID': 'process-model', 'XDG_DATA_HOME': str(tmp_path)})
    monkeypatch.chdir(tmp_path)
    user = tmp_path / 'user.env'
    user.write_text('FORGE_API_KEY=private-fixture-key\nFORGE_MODEL=user-model\n')
    monkeypatch.setattr(settings, 'FORGECODE_ENV_PATH', user)
    project = tmp_path / 'project.env'
    project.write_text('MODEL_ID=project-model\nMODEL_MAX_TOKENS=4096\n')
    monkeypatch.setattr(runner, 'preconditions', lambda source: {'linux': False, 'docker_access': False})
    output = tmp_path / 'uncreated'
    with pytest.raises(SystemExit):
        runner.main(['--dry-run'], default_dataset=dataset)
    assert runner.main(['--n-tasks', '1', '--env-file', str(project), '--model', 'explicit-model',
                        '--output-dir', str(output), '--dry-run'], default_dataset=dataset) == 0
    rendered = capsys.readouterr().out
    data = json.loads(rendered)
    assert data['model']['model_id'] == 'explicit-model' and data['model']['max_tokens'] == 4096
    assert data['model']['base_url'] == 'https://api.anthropic.com'
    assert data['concurrency'] == data['attempts'] == 1 and data['task_retries'] == 0
    assert data['budget'] == {'max_model_calls': 120, 'max_tool_calls': 240, 'wall_seconds': 1800}
    assert not data['ready'] and data['model_calls'] == 0
    assert 'private-fixture-key' not in rendered and not output.exists()
    assert runner.main(['--n-tasks', '1', '--env-file', str(project), '--concurrency', '4',
        '--max-model-calls', 'unlimited', '--max-tool-calls', 'unlimited', '--max-turn-seconds', 'unlimited', '--dry-run'],
        default_dataset=dataset) == 0
    unlimited = json.loads(capsys.readouterr().out)
    assert unlimited['concurrency'] == 4
    assert unlimited['budget'] == dict(max_model_calls=None, max_tool_calls=None, wall_seconds=None)
    from forge.application.models import validate
    assert validate('harness-config', unlimited['harness']) == unlimited['harness']


def test_offline_materialization_and_job_pin_tasks_source_and_model(tmp_path, monkeypatch):
    from benchmark.harbor import run_dataset as runner
    from benchmark.adapters.materialize import materialize_dataset, resolve_task
    from harbor.registry.client.package import PackageDatasetClient
    from harbor.tasks.client import TaskClient
    from harbor.models.task.id import PackageTaskId
    from harbor.models.job.config import JobConfig
    from harbor.job import Job
    from benchmark.harbor.deadline_plugin import ForgeCodeDeadlinePlugin
    import forge.config as settings
    monkeypatch.setattr(settings.os, 'environ', {'XDG_DATA_HOME': str(tmp_path)})
    monkeypatch.chdir(tmp_path)
    user = tmp_path / 'user.env'
    user.write_text('FORGE_API_KEY=offline-fixture-secret\nFORGE_MODEL=fixture-model\nFORGE_REASONING_EFFORT=high\n')
    monkeypatch.setattr(settings, 'FORGECODE_ENV_PATH', user)
    metadata = SimpleNamespace(version='sha256:' + 'd' * 64, task_ids=[
        PackageTaskId(org='terminal-bench', name=name, ref='sha256:' + 'a' * 64) for name in ('z', 'a')])
    async def get_metadata(self, name):
        assert name == 'terminal-bench/terminal-bench-2@latest'
        return metadata
    async def files(self, *args, **kwargs):
        return {}
    async def download(self, ids, *, output_dir, export):
        results = []
        assert export and [item.name for item in ids] == ['a']
        for task in ids:
            path = output_dir / task.name
            (path / 'environment').mkdir(parents=True)
            (path / 'tests').mkdir()
            (path / 'instruction.md').write_text('Offline structural fixture, not a benchmark result.')
            (path / 'task.toml').write_text('[agent]\ntimeout_sec=60\n')
            (path / 'environment/Dockerfile').write_text('FROM scratch\n')
            (path / 'tests/test.sh').write_text('exit 0\n')
            results.append(SimpleNamespace(path=path, content_hash='a' * 64))
        return SimpleNamespace(results=results)
    monkeypatch.setattr(PackageDatasetClient, 'get_dataset_metadata', get_metadata)
    monkeypatch.setattr(PackageDatasetClient, 'download_dataset_files', files)
    monkeypatch.setattr(TaskClient, 'download_tasks', download)
    output = tmp_path / 'run'
    taskset = asyncio.run(materialize_dataset('terminal-bench/terminal-bench-2', ref='latest',
        task_names=[], n_tasks=1, destination=output / 'tasks'))
    assert list(taskset['tasks']) == ['terminal-bench/a']
    assert taskset['usage'] == 'evaluation-only' and taskset['version'] == metadata.version
    args = runner.build_parser(default_dataset='terminal-bench/terminal-bench-2').parse_args(['--n-tasks', '1'])
    config, manifest = runner.effective_configuration(args)
    manifest.update(run_id='fixture-run', source_manifest={'fixture': True})
    job = runner.frozen_job(args, manifest, taskset, tmp_path / 'source', output)
    raw = job.model_dump_json(context={'redact_sensitive_env': False})
    restored = JobConfig.model_validate_json(raw)
    assert 'offline-fixture-secret' not in raw and config.api_key == 'offline-fixture-secret'
    assert restored.agents[0].env['FORGECODE_MODEL_MAX_TOKENS'] == '65536'
    assert restored.agents[0].env['FORGECODE_CONTEXT_WINDOW'] == '262144'
    assert restored.agents[0].env['FORGECODE_REASONING_EFFORT'] == 'high'
    assert Path(restored.agents[0].kwargs['source_dir']) == tmp_path / 'source'
    assert restored.agents[0].max_timeout_sec == 1800 and restored.retry.max_retries == 0
    assert len(restored.metrics) == 1 and restored.metrics[0].type.value == 'mean'
    assert restored.verifier.env == manifest['grader']['env']
    assert restored.verifier.env['UV_HTTP_TIMEOUT'] == '120' and restored.verifier.max_timeout_sec is None
    isolated = runner.frozen_job(args, {**manifest, 'prepared_image_mapping': {'fixture': 'sha256:prepared'}},
                                taskset, tmp_path / 'source', output)
    assert not isolated.environment.mounts
    async def resolve_metrics():
        metrics = await Job._resolve_metrics(restored, restored.tasks)
        plugin = ForgeCodeDeadlinePlugin()
        await plugin.on_job_start(SimpleNamespace(config=restored, _metrics=metrics))
        try:
            assert len(metrics[restored.tasks[0].source]) == 1
        finally:
            await plugin.on_job_end(None)
    asyncio.run(resolve_metrics())
    assert not restored.datasets and len(restored.tasks) == 1
    assert json.loads(restored.agents[0].kwargs['frozen_configuration'])['model']['model_id'] == 'fixture-model'
    # Exercise the actual CLI preparation with tiny offline source/task fixtures.
    source = tmp_path / 'source'
    for relative in ('pyproject.toml', 'README.md', 'uv.lock', 'forge/__init__.py',
                     'benchmark/__init__.py', 'benchmark/harbor/__init__.py', 'benchmark/core/__init__.py'):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('')
    monkeypatch.setattr(runner, 'PROJECT_ROOT', source)
    monkeypatch.setattr(runner, 'preconditions', lambda source: {'offline_fixture': True})
    monkeypatch.setattr(runner, 'harbor_executable', lambda: 'offline-fixture-harbor')
    prepared = tmp_path / 'prepared'
    def controller(command, *, cwd, env, output_dir, metadata):
        assert command[:3] == ['offline-fixture-harbor', 'run', '--config']
        assert cwd == output_dir == prepared and env['FORGECODE_API_KEY'] == 'offline-fixture-secret'
        assert metadata['run_id'].startswith('run-')
        return 0
    monkeypatch.setattr('benchmark.harbor.controller.run_controller', controller)
    arguments = ['--n-tasks', '1', '--source-dir', str(source), '--output-dir', str(prepared), '--taskset-dir', str(output / 'tasks')]
    assert runner.main(arguments, default_dataset='terminal-bench/terminal-bench-2') == 0
    saved = json.loads((prepared / 'experiment.json').read_text())
    assert saved['source_manifest']['files']['uv.lock']
    assert 'offline-fixture-secret' not in (prepared / 'harbor.json').read_text()
    with pytest.raises(FileExistsError):
        runner.main(arguments, default_dataset='terminal-bench/terminal-bench-2')
    (output / 'tasks/a/instruction.md').write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        resolve_task(output / 'tasks', taskset, 'terminal-bench/a')
    from benchmark.adapters.materialize import reuse_taskset
    with pytest.raises(ValueError, match='changed'):
        reuse_taskset(output / 'tasks', dataset=taskset['dataset'], ref='latest', task_names=[], n_tasks=1, destination=tmp_path / 'tampered-copy')
    assert not (tmp_path / 'tampered-copy').exists()
    with pytest.raises(ValueError, match='Unknown exact task'):
        asyncio.run(materialize_dataset('terminal-bench/terminal-bench-2', ref='latest',
            task_names=['missing'], n_tasks=None, destination=tmp_path / 'invalid'))
    assert not (tmp_path / 'invalid').exists()


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


def test_snapshot_pins_package_bytes_and_excludes_environment_secrets(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / 'repo'
    for relative, content in {
        'pyproject.toml': '[project]\nname="probe"',
        'README.md': 'probe',
        'forge/runner.py': 'VERSION = 1',
        'benchmark/__init__.py': '',
        'benchmark/harbor/entry.py': 'VERSION = 1',
        'benchmark/core/spec.py': 'VERSION = 1',
        '.env': 'DO_NOT_COPY_SECRET=value',
        'benchmark/runs/private.txt': 'excluded',
        'forge/__pycache__/probe.pyc': 'excluded',
    }.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    import subprocess
    original = subprocess.run
    def metadata(command, **kwargs):
        if 'diff' in command:
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        return original(command, **kwargs)
    monkeypatch.setattr('benchmark.harbor.snapshot.subprocess.run', metadata)
    frozen = freeze_source(source, tmp_path / 'snapshots')
    (source / 'forge/runner.py').write_text('VERSION = 2', encoding='utf-8')
    (source / 'benchmark/core/spec.py').write_text('VERSION = 2', encoding='utf-8')

    assert (frozen / 'forge/runner.py').read_text() == 'VERSION = 1'
    assert (frozen / 'benchmark/core/spec.py').read_text() == 'VERSION = 1'
    assert not (frozen / '.env').exists()
    assert not (frozen / 'benchmark/runs').exists()
    assert not (frozen / 'forge/__pycache__').exists()
    manifest = json.loads((frozen.parent / 'manifest.json').read_text())
    assert 'forge/runner.py' in manifest['files']
    assert len(manifest['content_sha256']) == 64
    assert manifest['git_diff_status'] == 'timeout' and manifest['dirty_diff_sha256'] is None




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
