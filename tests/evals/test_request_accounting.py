"""Read full request evidence from the existing Journal; no provider calls."""
from uuid import uuid4
import asyncio

import pytest

from evals.runner import observed_requests
from evals.native_swe_campaign import stop_reason
from forge.observability.recorder import JournalRecorder
from forge.sessions.store import SessionStore
from forge.runtime.model_budget import BudgetedModelClient
from forge.runtime.turn_state import TurnState
from forge.runtime.state import ModelUsageUpdate, ModelResponseCompleted, TokenUsage


def journal(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    return SessionStore(root, data_root=tmp_path / 'data').create(model='fixture', provider='anthropic')


def request(recorder, role, usage=None, *, final=True, reason=None):
    identity = str(uuid4())
    attributes = {'model_request_id': 'request-' + identity, 'invocation_id': 'invocation-' + str(uuid4()),
        'attempt_no': 1, 'role': role, 'requested_model': 'fixture', 'returned_model': None,
        'usage_quality': 'unknown', 'provider': 'anthropic'}
    recorder.emit('model.request.started', attributes)
    if usage is not None:
        totals = {'input_tokens': usage['input_tokens'] + usage.get('cache_read_input_tokens', 0)
                  + usage.get('cache_creation_input_tokens', 0), 'output_tokens': usage['output_tokens']}
        end = {**attributes, 'returned_model': 'fixture', 'raw_usage': usage,
               'usage_is_final': final, 'usage_quality': 'actual' if final else 'unknown',
               'usage': totals if final else None}
        if reason:
            end.update(error_kind=reason, retryable=False)
        recorder.emit('model.request.failed' if reason else 'model.request.finished', end)
    return identity


def test_all_roles_override_root_only_events_without_double_counting(tmp_path):
    store = journal(tmp_path)
    recorder = JournalRecorder(store)
    identity = request(recorder, 'main', {'input_tokens': 10, 'output_tokens': 2, 'cache_read_input_tokens': 5})
    store.append('model_request_started', {'request_id': identity})
    store.append('model_request_finished', {'request_id': identity, 'outcome': 'completed',
        'usage_is_final': True, 'usage': {'input_tokens': 10, 'output_tokens': 2,
            'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 5}})
    request(recorder, 'explore', {'input_tokens': 20, 'output_tokens': 3, 'cache_creation_input_tokens': 4})
    store.append('turn_completed', {'statistics': {'model_requests': 2}})
    report = observed_requests(store.path)
    assert report['observed_requests'] == 2 and report['accounting_complete'] and report['usage_complete']
    assert report['request_roles'] == {'main': 1, 'explore': 1}
    assert report['reported_usage'] == {'input_tokens': 30, 'output_tokens': 5,
        'cache_creation_input_tokens': 4, 'cache_read_input_tokens': 5}


def test_actual_parent_child_budget_and_recorder_lifecycle(tmp_path):
    store = journal(tmp_path)
    recorder = JournalRecorder(store)
    parent = TurnState(max_model_calls=5)
    child = TurnState(max_model_calls=5, parent=parent)
    def root_sink(kind, attributes):
        store.append(kind, attributes)
        recorder.record_request(kind, attributes)
    parent.request_event_sink = root_sink
    child.request_event_sink = recorder.record_request  # Child has no root SessionJournal.
    class Client:
        provider = 'anthropic'
        model = 'fixture'
        max_tokens = 100
        def __init__(self, usage):
            self.usage = usage
        async def stream(self, *args, **kwargs):
            yield ModelUsageUpdate(self.usage, usage_is_final=True)
            yield ModelResponseCompleted('end_turn')
    async def exercise():
        with recorder.turn():
            async for _ in BudgetedModelClient(Client(TokenUsage(10, 2, 0, 5)), parent, 'model').stream(
                    [{'role': 'user', 'content': 'parent'}]):
                pass
            with recorder.turn(nested=True):
                async for _ in BudgetedModelClient(Client(TokenUsage(20, 3, 4, 0)), child, 'model').stream(
                        [{'role': 'user', 'content': 'child'}]):
                    pass
    asyncio.run(exercise())
    store.append('turn_completed', {'statistics': {'model_requests': parent.model_calls}})
    report = observed_requests(store.path)
    assert parent.model_calls == report['observed_requests'] == 2
    assert report['request_roles'] == {'main': 1, 'explore': 1}
    assert report['accounting_complete'] and report['usage_complete']
    assert report['reported_usage'] == {'input_tokens': 30, 'output_tokens': 5,
        'cache_creation_input_tokens': 4, 'cache_read_input_tokens': 5}


def test_nested_failure_and_partial_usage_preserve_stop_condition(tmp_path):
    store = journal(tmp_path)
    recorder = JournalRecorder(store)
    request(recorder, 'main', {'input_tokens': 10, 'output_tokens': 2})
    request(recorder, 'explore', {'input_tokens': 7, 'output_tokens': 0}, final=False, reason='timeout')
    report = observed_requests(store.path)
    assert report['observed_requests'] == 2 and not report['usage_complete']
    assert report['reported_usage']['input_tokens'] == 17
    assert len(report['unknown_usage_request_ids']) == 1
    assert stop_reason({'cleanup': {'state': 'clean'}, 'request_evidence': report}) == 'timeout'


def test_missing_child_events_in_legacy_log_cannot_claim_complete_accounting(tmp_path):
    store = journal(tmp_path)
    store.append('model_request_started', {'request_id': 'root'})
    store.append('model_request_finished', {'request_id': 'root', 'outcome': 'completed',
        'usage_is_final': True, 'usage': {'input_tokens': 10, 'output_tokens': 2,
            'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0}})
    store.append('turn_completed', {'statistics': {'model_requests': 2}})
    report = observed_requests(store.path)
    assert report['observed_requests'] == 1 and report['missing_request_count'] == 1
    assert not report['accounting_complete'] and not report['usage_complete']
    assert stop_reason({'cleanup': {'state': 'clean'}, 'request_evidence': report}) == 'request_accounting_unavailable'


def test_duplicate_all_role_request_identity_rejects_ambiguous_evidence(tmp_path):
    store = journal(tmp_path)
    recorder = JournalRecorder(store)
    request(recorder, 'main', {'input_tokens': 1, 'output_tokens': 1})
    records = SessionStore._read_records(store.path)
    event = next(SessionStore._payload(record, store.path)['event'] for record in records
                 if record['type'] == 'observation')
    recorder.emit('model.request.started', event['attributes'])
    with pytest.raises(ValueError, match='Duplicate model request'):
        observed_requests(store.path)


def test_interrupted_without_terminal_keeps_observed_lower_bound_and_unknown_coverage(tmp_path):
    store = journal(tmp_path)
    request(JournalRecorder(store), 'main', {'input_tokens': 7, 'output_tokens': 2})
    report = observed_requests(store.path)
    assert report['observed_requests'] == 1 and report['reported_usage']['input_tokens'] == 7
    assert not report['accounting_complete'] and not report['usage_complete']
    assert report['terminal_shared_requests'] is None
    from evals.native_swe_campaign import costs
    cost = costs({'usage': report['reported_usage'], 'request_evidence': report})
    assert cost['requests'] is None and cost['tokens'] is None
    assert cost['reported_requests_lower_bound'] == 1
    assert cost['reported_tokens_lower_bound']['input_tokens'] == 7


@pytest.mark.parametrize('fault', ['orphan_finish', 'contradictory_finish'])
def test_orphan_or_contradictory_terminal_cannot_produce_complete_accounting(tmp_path, fault):
    store = journal(tmp_path)
    recorder = JournalRecorder(store)
    request(recorder, 'main', {'input_tokens': 1, 'output_tokens': 1})
    records = SessionStore._read_records(store.path)
    end = [SessionStore._payload(record, store.path)['event'] for record in records
           if record['type'] == 'observation'][-1]['attributes']
    if fault == 'orphan_finish':
        end = {**end, 'model_request_id': 'request-' + str(uuid4())}
    else:
        end = {**end, 'raw_usage': {'input_tokens': 99, 'output_tokens': 1},
               'usage': {'input_tokens': 99, 'output_tokens': 1}}
    recorder.emit('model.request.finished', end)
    with pytest.raises(ValueError):
        observed_requests(store.path)
