import json

import pytest

from benchmark.harbor.summarize import summarize_run


@pytest.mark.parametrize('exception,status,reward,category', [
    (None, {'status': 'completed'}, 1, 'passed'),
    (None, {'status': 'failed', 'stop_reason': 'acceptance_unmet'}, 0, 'acceptance_failed'),
    (None, {'status': 'failed', 'stop_reason': 'authentication_error'}, 0, 'model_failure'),
    ('ToolExecutionError', {}, None, 'harness_failure'),
    ('AgentSetupTimeoutError', {}, None, 'environment_failure'),
    ('VerifierTimeoutError', {}, 0, 'grader_failure'),
    (None, {'status': 'failed', 'stop_reason': 'model_budget_exhausted'}, 1, 'budget_exhausted'),
    (None, {'status': 'cancelled'}, None, 'cancelled_or_incomplete'),
    ('UnexpectedException', {}, None, 'unknown'),
])
def test_shared_terminal_classification_preserves_grade_axes(exception, status, reward, category):
    from benchmark.adapters.protocol import classify_result, normalize
    result = {'exception_info': {'exception_type': exception} if exception else None,
              'verifier_result': {'rewards': {'reward': reward}}, 'finished_at': 'observed'}
    actual = classify_result(result, status)
    assert actual['category'] == category
    assert normalize(runner_exit=1, result=result, agent_status=status)['category'] == category
    assert actual['official_rewards'] == {'reward': reward}
    if category == 'budget_exhausted':
        assert actual['grade_result'] == 'pass' and actual['termination_reason'] == 'model_budget_exhausted'


def test_recovered_errors_and_nonzero_tool_exit_do_not_replace_final_success():
    from benchmark.adapters.protocol import classify_result
    status = {'status': 'completed', 'stop_reason': 'completed',
              'observed_errors': ['rate_limit'], 'tool_exit_codes': [2, 0]}
    assert classify_result({'verifier_result': {'rewards': {'reward': 1}}}, status)['category'] == 'passed'


@pytest.mark.parametrize('stage,category', [('environment_setup', 'environment_failure'),
    ('agent_setup', 'environment_failure'), ('agent_execution', 'harness_failure'), ('verifier', 'grader_failure')])
def test_generic_official_exception_uses_structured_phase(stage, category):
    from benchmark.adapters.protocol import classify_result
    result = {'exception_info': {'exception_type': 'RuntimeError'}, stage: {'started_at': 'fixture', 'finished_at': 'fixture'}}
    classified = classify_result(result)
    assert classified['category'] == category and classified['failure_stage'] == stage


def test_report_counts_unstarted_tasks_and_keeps_official_scores(tmp_path):
    from benchmark.harbor.summarize import evaluation_report
    from benchmark.cli import main
    (tmp_path / 'experiment.json').write_text(json.dumps({'run_id': 'fixture', 'dataset': 'fixture',
        'usage': 'evaluation-only', 'taskset': {'tasks': {'a': {'directory': 'a'}, 'b': {'directory': 'b'}}}}))
    job = tmp_path / 'job'; job.mkdir()
    official = {'finished_at': 'now', 'stats': {'official_score': 0.5}}
    (job / 'result.json').write_text(json.dumps(official))
    path = trial(job, reward=1, reason='tool_budget_exhausted')
    (path / 'config.json').write_text(json.dumps({'task': {'path': '/tasks/a'}}))
    report = evaluation_report(tmp_path)
    assert report['official_job_result'] == official and report['task_count'] == 2
    assert report['classification_counts']['budget_exhausted'] == 1
    assert report['classification_counts']['cancelled_or_incomplete'] == 1
    assert report['valid_grading_rate'] == .5 and report['tasks']['a']['grade_result'] == 'pass'
    assert main(['report', str(tmp_path)]) == main(['status', str(tmp_path)]) == 0


def test_atif_export_is_offline_private_source_preserving_and_refuses_overwrite(tmp_path):
    from forge.sessions.store import SessionStore
    from benchmark.harbor.summarize import export_atif, evaluation_trace_index
    journal = SessionStore(tmp_path, data_root=tmp_path / 'job/trial/agent/state').create(model='fixture')
    journal.record_turn_started('diagnostic fixture', None)
    journal.record_turn_cancelled('cancelled fixture')
    (tmp_path / 'experiment.json').write_text(json.dumps({'run_id': 'fixture', 'dataset': 'terminal-bench/terminal-bench-2'}))
    output = tmp_path / 'share'
    original = journal.path.read_bytes()
    index = evaluation_trace_index(tmp_path)
    assert index['journals'][0]['record_closed'] and not index['journals'][0]['responses_complete']
    assert export_atif(tmp_path, output)['usage'] == 'evaluation-only'
    assert json.loads((output / 'trajectory-1.json').read_text())['extra']['usage'] == 'evaluation-only'
    assert journal.path.read_bytes() == original
    with pytest.raises(FileExistsError): export_atif(tmp_path, output)


def test_comparison_lists_uncontrolled_changes_and_preserves_each_task(tmp_path, capsys):
    from benchmark.harbor.summarize import compare_runs
    from benchmark.cli import main
    base, candidate = tmp_path / 'base', tmp_path / 'candidate'
    source = {'files': {'forge/runtime.py': 'a', 'uv.lock': 'lock', 'benchmark/runner.py': 'runner'}}
    experiment = {'run_id': 'fixture', 'dataset': 'terminal-bench/terminal-bench-2', 'usage': 'evaluation-only',
        'model': {'model_id': 'fixture-a'}, 'parameters': {'temperature': None}, 'harness': {'compaction': True},
        'agent': 'fixture', 'budget': {'max_model_calls': 120}, 'harbor_version': '0.18.0', 'attempts': 1,
        'task_retries': 0, 'concurrency': 1, 'timeout_multiplier': 1, 'agent_setup_timeout_multiplier': 12,
        'source_manifest': source, 'driver_manifest': source, 'grader': {'protocol': 'fixture-verifier'},
        'taskset': {'version': 'frozen-fixture', 'tasks': {'a': {'directory': 'a', 'content_sha256': 'task'}}}}
    for directory, reward in ((base, 0), (candidate, 1)):
        directory.mkdir()
        (directory / 'experiment.json').write_text(json.dumps(experiment))
        path = trial(directory / 'job', reward=reward)
        (path / 'config.json').write_text(json.dumps({'task': {'path': '/tasks/a'}}))
        (directory / 'resources.jsonl').write_text(json.dumps({'phase': 'environment', 'task': 'a', 'session': 'trial__env',
            'identity': {'host': 'explicit-fixture', 'containers': [{'image': 'sha256:fixture'}]}}) + '\n')
    experiment['model']['model_id'] = 'fixture-b'
    (candidate / 'experiment.json').write_text(json.dumps(experiment))
    comparison = compare_runs(base, candidate, change='model')
    assert comparison['controlled_conditions'] and comparison['statistical_significance'] == 'not_estimated'
    assert comparison['tasks']['a']['reward_delta'] == '1'
    assert comparison['differences'] == ['model.configuration.model_id']
    assert not compare_runs(base, candidate, change='harness')['controlled_conditions']
    experiment['model']['model_id'] = 'fixture-a'
    experiment['source_manifest']['files']['forge/runtime.py'] = 'harness-candidate'
    (candidate / 'experiment.json').write_text(json.dumps(experiment))
    assert compare_runs(base, candidate, change='harness')['controlled_conditions']
    experiment['budget']['max_model_calls'] = 121
    experiment['source_manifest']['files']['uv.lock'] = 'changed'
    (candidate / 'experiment.json').write_text(json.dumps(experiment))
    comparison = compare_runs(base, candidate, change='model')
    assert 'budget.max_model_calls' in comparison['unexpected_differences']
    assert 'dependencies_and_runner.uv.lock' in comparison['unexpected_differences']
    (candidate / 'resources.jsonl').unlink()
    assert 'candidate.environment' in compare_runs(base, candidate, change='model')['missing_evidence']
    assert main(['compare', str(base), str(candidate), '--change', 'model']) == 0
    assert json.loads(capsys.readouterr().out)['tasks']['a']['candidate_reward'] == '1'


def test_sft_export_public_test_isolation_and_qualified_synthetic_fixture(tmp_path, monkeypatch):
    from hashlib import sha256
    from types import SimpleNamespace
    from forge.runtime.request_snapshot import RequestSnapshot
    from forge.sessions.store import SessionStore
    from benchmark.harbor.summarize import export_sft
    from extensions import qwen_tool_distillation as training
    from benchmark.cli import main
    output = tmp_path / 'export'
    for dataset in ('terminal-bench/terminal-bench-2', 'swe-bench/swe-bench-verified'):
        (tmp_path / 'experiment.json').write_text(json.dumps({'dataset': dataset, 'usage': 'train'}))
        with pytest.raises(SystemExit, match='evaluation-only'):
            main(['export', str(tmp_path), '--format', 'sft'])
    # Deliberately synthetic: verifies export plumbing, not training-data results.
    path = trial(tmp_path / 'job', reward=1)
    (path / 'config.json').write_text(json.dumps({'task': {'path': '/tasks/a'}}))
    journal = SessionStore(tmp_path, data_root=path / 'agent/state').create(model='synthetic-fixture')
    journal.record_turn_started('synthetic fixture prompt', None)
    snapshot = RequestSnapshot.capture(messages=[{'role': 'user', 'content': 'synthetic fixture prompt'}],
        tools=[], system='fixture system', client=SimpleNamespace(model='fixture'))
    journal.append('model_input_snapshot', snapshot.as_dict())
    journal.append('model_request_started', {'request_id': 'r', 'input_sha256': snapshot.sha256})
    journal.append('model_request_chunk', {'request_id': 'r', 'text': 'fixture answer'})
    journal.append('model_request_finished', {'request_id': 'r', 'input_sha256': snapshot.sha256, 'outcome': 'completed',
        'response_complete': True, 'text_characters': 14, 'stop_reason': 'end_turn'})
    journal.append('turn_completed', {'status': 'completed'})
    tokenizer = tmp_path / 'tokenizer'; tokenizer.mkdir()
    (tokenizer / 'tokenizer_config.json').write_text('{"fixture":true}')
    identity = training.tokenizer_identity(tokenizer, model_id='synthetic-character-counter-fixture', revision='fixture-v1')
    digest = sha256(journal.path.read_bytes()).hexdigest()
    source = {'task_id': 'a', 'repository': 'synthetic-fixture/repository', 'source_revision': 'a' * 40,
        'task_template': 'synthetic-export-contract', 'split': 'train', 'license_id': 'MIT', 'source_is_public': True,
        'metadata': {'fixture': True, 'overlap_check': {'status': 'passed', 'method': 'fixture exact identities',
            'reference_sha256': 'b' * 64, 'assignments': [{'task_id': 'a', 'repository': 'synthetic-fixture/repository', 'split': 'train'}]}}}
    source['metadata']['task_content_sha256'] = 'c' * 64
    overlap = source['metadata']['overlap_check']
    overlap['assignments'].append({'task_id': 'holdout', 'repository': 'synthetic-fixture/holdout', 'split': 'test', 'content_sha256': 'd' * 64})
    overlap['reference_sha256'] = sha256(json.dumps(overlap['assignments'], sort_keys=True).encode()).hexdigest()
    experiment = {'run_id': 'synthetic-fixture', 'dataset': 'synthetic-export-contract', 'usage': 'training',
        'grader': {'id': 'synthetic-fixture-validator'}, 'taskset': {'tasks': {'a': {'directory': 'a', 'content_sha256': 'c' * 64}}},
        'training': {'tokenizer': identity, 'sources': {digest: source}}}
    (tmp_path / 'experiment.json').write_text(json.dumps(experiment))
    monkeypatch.setattr(training, 'qwen_token_counter', lambda directory: len)
    assert export_sft(tmp_path, output, tokenizer_dir=tokenizer)['rows'] == 1
    row = json.loads((output / 'samples.jsonl').read_text())
    assert row['metadata']['source_metadata']['fixture'] is True
    assert row['messages'][-1] == {'role': 'assistant', 'content': 'fixture answer', 'loss': True}
    assert all(not message['loss'] for message in row['messages'][:-1])
    assert sha256(journal.path.read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError): export_sft(tmp_path, output, tokenizer_dir=tokenizer)
    result_path = path / 'result.json'
    result = json.loads(result_path.read_text())
    result['verifier_result']['rewards']['reward'] = 0
    result_path.write_text(json.dumps(result))
    with pytest.raises(training.TraceRejected, match='independently successful'): export_sft(tmp_path, tmp_path / 'failed', tokenizer_dir=tokenizer)
    result['verifier_result']['rewards']['reward'] = 1
    result_path.write_text(json.dumps(result))
    complete_bytes = journal.path.read_bytes()
    journal.record_turn_started('cancelled synthetic fixture', None)
    journal.record_turn_cancelled('synthetic cancellation')
    with pytest.raises(training.TraceRejected, match='Cancelled'):
        training.journal_training_records(journal.path, source=source, outcome={})
    journal.path.write_bytes(complete_bytes)
    journal.path.write_bytes(complete_bytes + b'{"partial"')
    with pytest.raises(training.TraceRejected): export_sft(tmp_path, tmp_path / 'incomplete', tokenizer_dir=tokenizer)
    journal.path.write_bytes(complete_bytes)
    (tokenizer / 'tokenizer_config.json').write_text('{"fixture":"changed"}')
    with pytest.raises(training.TraceRejected, match='tokenizer bytes'): export_sft(tmp_path, tmp_path / 'rejected', tokenizer_dir=tokenizer)
    assert not (tmp_path / 'rejected').exists()
    (tmp_path / 'tasks').mkdir()
    (tmp_path / 'tasks/taskset.json').write_text(json.dumps({'dataset': 'swe-bench/swe-bench-verified', 'usage': 'train'}))
    with pytest.raises(training.TraceRejected, match='evaluation-only'): export_sft(tmp_path, tmp_path / 'relabelled', tokenizer_dir=tokenizer)


def test_missing_official_result_keeps_kernel_usage_and_timeout(tmp_path):
    path = trial(tmp_path, reason='time_budget_exhausted')
    (path / 'result.json').unlink()
    (path / 'config.json').write_text('{}')
    (path / 'agent/forgecode-status.json').write_text('{"exit_code":0,"timed_out":false}')
    summary = summarize_run(tmp_path)
    assert summary.model_calls == 4
    assert summary.agent_timeouts == 1
    assert summary.raw_rewards == {}
    assert 'time_budget_exhausted' in summary.trial_assessments['trial']['observed_faults']


def test_live_journal_survives_missing_terminal_payload_and_deduplicates_export(tmp_path):
    path = tmp_path / 'trial'
    (path / 'agent/forgecode-state/sessions').mkdir(parents=True)
    (path / 'config.json').write_text('{}')
    (tmp_path / 'result.json').write_text('{"finished_at":null}')
    events = [dict(uuid=str(i), turn_id='turn', type=kind, payload=payload) for i, (kind, payload) in enumerate([
        ('model_request_started', {}), ('model_request_finished', {'usage': {'input_tokens':12,'output_tokens':3}}),
        ('tool_requested', {}), ('turn_completed', {'status':'failed','stop_reason':'time_budget_exhausted'}),
    ])]
    data = '\n'.join(json.dumps(e) for e in events) + '\n{"unfinished"'
    (path / 'agent/forgecode-state/sessions/session-test.jsonl').write_text(data)
    (path / 'agent/session-test.jsonl').write_text(data)
    summary = summarize_run(tmp_path)
    assert (summary.model_calls, summary.tool_calls, summary.input_tokens, summary.output_tokens) == (1, 1, 12, 3)
    assert summary.agent_timeouts == 1
    assert summary.telemetry_missing == summary.unfinished_trials == ()


def trial(tmp_path, *, reason='', reward=0, exception=None, timed_out=False):
    path = tmp_path / 'trial'
    (path / 'agent').mkdir(parents=True)
    (path / 'result.json').write_text(json.dumps({
        'exception_info': exception, 'verifier_result': {'rewards': {'reward': reward}},
    }), encoding='utf-8')
    (path / 'agent/forgecode.txt').write_text('FORGECODE_BENCHMARK_RESULT=' + json.dumps({
        'status': 'failed', 'stop_reason': reason, 'model_calls': 4,
    }), encoding='utf-8')
    if timed_out:
        (path / 'agent/forgecode-status.json').write_text(
            json.dumps({'timed_out': True, 'exit_code': 124}), encoding='utf-8')
    return path


@pytest.mark.parametrize('reason', ['server_error', 'stream_interrupted', 'empty_model_response', 'incomplete_tool_call'])
@pytest.mark.parametrize('reward', [0, 1])
def test_kernel_protocol_failure_preserves_raw_but_is_not_eligible(tmp_path, reason, reward):
    trial(tmp_path, reason=reason, reward=reward)
    summary = summarize_run(tmp_path)
    assert summary.raw_rewards == {'trial': reward}
    assert summary.model_service_failures == 1
    assert summary.agent_failures == summary.agent_timeouts == 0
    assert summary.scored_trials == summary.pass_at_2 == 0


def test_one_deadline_has_one_primary_outcome(tmp_path):
    trial(tmp_path, reason='time_budget_exhausted', timed_out=True,
          exception={'exception_type': 'RuntimeError'})
    summary = summarize_run(tmp_path)
    assert summary.agent_timeouts == 1
    assert summary.agent_failures == summary.infrastructure_failures == 0


def test_timeout_does_not_erase_official_pass_and_missing_trial_counts(tmp_path):
    trial(tmp_path, reason='time_budget_exhausted', reward=1, timed_out=True)
    missing = tmp_path / 'missing'
    missing.mkdir()
    (missing / 'config.json').write_text('{}')
    summary = summarize_run(tmp_path)
    data = summary.to_dict()
    assert summary.agent_timeouts == 1
    assert summary.pass_at_2 == 1
    assert data['final_pass_count'] == 1
    assert data['final_pass_denominator'] == 2
    assert data['final_pass_rate'] == .5
    assert data['eligible_final_pass_rate'] == 1


def test_agent_timeout_exception_is_not_infrastructure(tmp_path):
    trial(tmp_path, reason='time_budget_exhausted', timed_out=True,
          exception={'exception_type': 'AgentTimeoutError'})
    summary = summarize_run(tmp_path)
    assert summary.agent_timeouts == 1
    assert summary.infrastructure_failures == 0


def test_identical_payload_copies_do_not_double_usage(tmp_path):
    path = trial(tmp_path)
    (path / 'agent/forgecode-copy.txt').write_bytes((path / 'agent/forgecode.txt').read_bytes())
    assert summarize_run(tmp_path).model_calls == 4


def test_verifier_failure_and_model_fault_remain_separate_observations(tmp_path):
    path = trial(tmp_path, reason='empty_model_response')
    (path / 'verifier').mkdir()
    (path / 'verifier/test-stdout.txt').write_text('uvx: command not found', encoding='utf-8')
    summary = summarize_run(tmp_path)
    assert summary.verifier_environment_failures == 1
    assert summary.agent_failures == summary.model_service_failures == 0
    assessment = summary.to_dict()['trial_assessments']['trial']
    assert 'empty_model_response' in assessment['observed_faults']


@pytest.mark.parametrize('reward', [True, float('nan'), float('inf')])
def test_invalid_rewards_are_not_scored(tmp_path, reward):
    trial(tmp_path, reward=reward)
    assert summarize_run(tmp_path).scored_trials == 0


def test_generic_exception_text_does_not_establish_provider_failure(tmp_path):
    trial(tmp_path, exception={'exception_type': 'RuntimeError',
                              'exception_message': 'Capitalization in the model output is wrong.'})
    summary = summarize_run(tmp_path)
    assert summary.model_service_failures == 0
    assert summary.trial_assessments['trial']['primary_outcome'] == 'infrastructure_failure'
