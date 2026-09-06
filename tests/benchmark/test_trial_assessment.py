import json

import pytest

from benchmark.harbor.summarize import summarize_run


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
