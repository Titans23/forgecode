"""Offline plans and real SQLite accounting; no provider or native proof."""
from copy import deepcopy
import asyncio
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from benchmark.adapters.harbor import export_runspec
from benchmark.core.spec import freeze_spec
from forge.application.models import ContractError, canonical_hash, validate
from forge.engine.persistence import new_id
from forge.observability.usage_ledger import confirm_usage
from scripts.delivery_experiment import freeze_plans
from tests.implementation.integration.test_evaluations import make_evaluation, create, start, claim_next, finish


def test_unbounded_plan_survives_draft_export_and_import_without_execution_rights(tmp_path):
    methods, original = make_evaluation(tmp_path / 'sender')
    receiver, _ = make_evaluation(tmp_path / 'receiver')
    try:
        run, _ = create(methods, original)
        template = methods.evaluation_client.template({'template_id': run['run_id']})
        choices = {**template['defaults'], 'spend_policy': 'human_unbounded', 'spend_ceiling': None}
        draft = methods.evaluation_client.draft({'client_action_id': new_id('act'),
            'template_id': run['run_id'], 'choices': choices})
        spec, values, _ = freeze_spec(methods.store, draft['spec'])
        plan = export_runspec(spec, values)
        treatments = freeze_plans(plan)
        for treatment in treatments.values():
            assert treatment['spec']['budget'] == {**original['budget'],
                'spend_policy': 'human_unbounded', 'spend_ceiling': None}
            assert treatment['read_only_plan'] is True
        from benchmark.core.repair_comparison import compare_repairs
        finite = deepcopy(treatments['B']['spec'])
        finite['budget'].update(spend_policy='unknown_usage_stop_next_request', spend_ceiling='0')
        assert not compare_repairs(treatments['A']['spec'], finite,
            treatments['A']['resolved_snapshots'], treatments['B']['resolved_snapshots'])['comparable']
        assert json.loads(methods.evaluations.run(run['run_id'])['spec_json']) == original
        path = tmp_path / 'unbounded-plan.json'
        path.write_text(json.dumps(plan), encoding='utf-8')
        imported = receiver.evaluation_client.import_plan({'client_action_id': new_id('act'), 'path': str(path)})
        remote = receiver.evaluation_client.template({'template_id': imported['template_id']})
        assert remote['defaults']['spend_ceiling'] is None
        assert remote['defaults']['spend_policy'] == 'human_unbounded'
        validation = receiver.evaluations.validate({'spec': remote['spec']})
        assert not validation['compatible']
        assert any(i['kind'] == 'unverified_configuration' for i in validation['issues'])
        accepted, _ = create(receiver, remote['spec'])
        start(receiver, accepted)
        attempt = receiver.store.connection.execute('SELECT selected_attempt_id FROM trials WHERE run_id=?',
            (accepted['run_id'],)).fetchone()[0]
        asyncio.run(receiver.evaluations.scheduler.execute(attempt))
        assert receiver.store.connection.execute('SELECT execution_state FROM attempts WHERE id=?',
            (attempt,)).fetchone()[0] == 'blocked'
        for owner in (methods, receiver):
            assert owner.store.connection.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0] == 0
    finally:
        receiver.store.close()
        methods.store.close()


def test_both_contract_runtimes_require_explicit_matching_money_policy():
    spec = json.loads(Path('contracts/v1/examples/run-spec.valid.json').read_text(encoding='utf-8'))
    fixtures = json.loads(Path('contracts/v1/method-fixtures.json').read_text(encoding='utf-8'))['cases']
    draft = next(c['value'] for c in fixtures if c['schema'] == 'evaluation.draft.request' and c['expected'] == 'valid')
    cases = []
    for policy, ceiling, expected in (
        ('human_unbounded', None, True), ('human_unbounded', '0', False),
        ('human_unbounded', '1', False), ('human_unbounded', '', False),
        ('unknown_usage_stop_next_request', None, False),
        ('preauthorization_with_reservation', None, False),
        ('unknown_usage_stop_next_request', '0', True),
        ('preauthorization_with_reservation', '12.5', True),
        ('human_unbounded', 0, False), ('unspecified', None, False)):
        for schema, source, key in (('run-spec', spec, 'budget'), ('evaluation.draft.request', draft, 'choices')):
            value = deepcopy(source)
            value[key].update(spend_policy=policy, spend_ceiling=ceiling)
            cases.append({'schema': schema, 'value': value, 'expected': expected})
    for case in cases:
        try:
            validate(case['schema'], case['value'])
            actual = True
        except ContractError:
            actual = False
        assert actual == case['expected'], (case['schema'], case['value'].get('budget', case['value'].get('choices')))
    script = """import {readFileSync} from 'node:fs';
import {validate} from './packages/contracts/dist/index.js';
const cases=JSON.parse(readFileSync(0,'utf8'));
console.log(JSON.stringify(cases.map(c=>{try{validate(c.schema,c.value);return true;}catch{return false;}})));
"""
    result = subprocess.run(['node', '--input-type=module', '-e', script], input=json.dumps(cases),
        capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
    assert json.loads(result.stdout) == [c['expected'] for c in cases]


@pytest.mark.parametrize('drift', ['mode', 'domains'])
def test_rehashed_plan_cannot_disagree_with_frozen_network_policy(tmp_path, drift):
    methods, spec = make_evaluation(tmp_path)
    try:
        spec, values, _ = freeze_spec(methods.store, spec)
        if drift == 'mode':
            values['policy']['network']['mode'] = 'allowlist'
        else:
            values['policy']['network'].update(mode='allowlist', allowed_domains=['one.example'])
            values['network_cache'].update(network_mode='allowlist', allowed_domains=['two.example'])
            spec['execution']['network_cache_configuration']['sha256'] = canonical_hash(values['network_cache'])
        spec['execution']['sandbox_policy']['sha256'] = canonical_hash(values['policy'])
        with pytest.raises(ContractError, match='[Nn]etwork'):
            export_runspec(spec, values)
        values['network_cache'].update(network_mode=values['policy']['network']['mode'],
            allowed_domains=values['policy']['network']['allowed_domains'])
        spec['execution']['network_cache_configuration']['sha256'] = canonical_hash(values['network_cache'])
        assert export_runspec(spec, values)['read_only_plan'] is True
    finally:
        methods.store.close()


def append(store, kind, attributes, scope):
    producer = store.connection.execute("SELECT value FROM store_meta WHERE key='producer_id'").fetchone()[0]
    sequence = store.connection.execute('SELECT COALESCE(MAX(source_seq),0)+1 FROM events WHERE source_id=?', (producer,)).fetchone()[0]
    # Construct from known stored IDs before changing a test event. This reaches
    # the durable event/projection boundary without replacing any ledger code.
    body = store.event_body(kind, producer, sequence, attributes)
    body.update(scope)
    return store.append_event(body, producer, sequence)


@pytest.mark.parametrize('field', ['run_id', 'trial_id', 'attempt_id', 'workspace_id', 'session_id', 'turn_id',
    'trace_id', 'span_id', 'provider', 'requested_model'])
def test_terminal_usage_cannot_move_between_request_scopes(tmp_path, field):
    methods, spec = make_evaluation(tmp_path)
    store = methods.store
    try:
        run, _ = create(methods, spec)
        start(methods, run)
        work = claim_next(methods)
        scope = {'run_id': run['run_id'], 'trial_id': work['trial_id'], 'attempt_id': work['business_id'],
            'trace_id': work['trace_id'], 'span_id': uuid4().hex[:16], 'parent_span_id': work['span_id']}
        attributes = {'model_request_id': new_id('request'), 'invocation_id': new_id('invocation'), 'attempt_no': 1,
            'role': 'main', 'provider': spec['model']['provider'], 'requested_model': spec['model']['requested_model'],
            'returned_model': None, 'usage_quality': 'unknown'}
        append(store, 'model.request.started', attributes, scope)
        raw = {'schema': 'forge.usage.components.v1', 'input_tokens': 5, 'output_tokens': 1}
        actual = {**attributes, 'usage_quality': 'actual', 'raw_usage': raw,
            'usage': {'input_tokens': 5, 'output_tokens': 1}, 'usage_is_final': True}
        wrong_scope, wrong_attributes = dict(scope), dict(actual)
        if field in ('provider', 'requested_model'):
            wrong_attributes[field] = 'different-provider-or-model'
        else:
            wrong_scope[field] = uuid4().hex[:16 if field == 'span_id' else 32] if field in ('trace_id', 'span_id') else new_id({
                'workspace_id': 'ws', 'session_id': 'ses', 'turn_id': 'turn', 'run_id': 'run', 'trial_id': 'trial', 'attempt_id': 'attempt'}[field])
        for kind in ('model.request.finished', 'model.usage.confirmed'):
            with pytest.raises(ContractError) as error:
                append(store, kind, wrong_attributes, wrong_scope)
            assert error.value.kind == 'EVENT_CONFLICT'
            assert tuple(store.connection.execute('SELECT quality,cost FROM usage_ledger').fetchone()) == ('unknown', None)
            assert store.connection.execute('SELECT state FROM model_requests').fetchone()[0] == 'running'
        append(store, 'model.request.failed', {**attributes, 'error_kind': 'interrupted', 'retryable': False, 'usage': None}, scope)
        finish(methods, work, agent_outcome='failed')
        # A genuine late confirmation keeps its original attempt even after it ends.
        confirm_usage(store, attributes['model_request_id'], raw, provider_request_id='local-fixture-receipt')
        report = methods.evaluations.report_data(run['run_id'])
        assert report['metrics']['request_count'] == 1
        assert report['metrics']['total_known_cost'] == '0.00002'
        assert store.connection.execute('SELECT attempt_id FROM attempt_requests').fetchone()[0] == work['business_id']
    finally:
        store.close()


def test_request_with_run_but_missing_attempt_is_not_unattributed_spend(tmp_path):
    methods, spec = make_evaluation(tmp_path)
    try:
        run, _ = create(methods, spec)
        attrs = {'model_request_id': new_id('request'), 'invocation_id': new_id('invocation'), 'attempt_no': 1,
            'role': 'main', 'provider': spec['model']['provider'], 'requested_model': spec['model']['requested_model'],
            'returned_model': None, 'usage_quality': 'unknown'}
        with pytest.raises(ContractError) as error:
            append(methods.store, 'model.request.started', attrs, {'run_id': run['run_id']})
        assert error.value.kind == 'EVENT_CONFLICT'
        assert methods.store.connection.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0] == 0
        assert methods.store.connection.execute('SELECT COUNT(*) FROM usage_ledger').fetchone()[0] == 0
    finally:
        methods.store.close()
