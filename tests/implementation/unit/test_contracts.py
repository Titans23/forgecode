import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from forge.application.models import ContractError, canonical_hash, strict_loads, validate, validate_event, validate_request, METHODS, BRIDGE_METHODS, EVENTS

ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / 'contracts' / 'v1'


@pytest.mark.parametrize('raw', [
    '{"a":1,"a":2}', '{"nested":{"x":1,"x":2}}', '{"x":NaN}', '{"x":Infinity}',
    '{"x":1e999}', '[' * 33 + '0' + ']' * 33, '"' + 'x' * 1048576 + '"',
    b'"\xff"', '"\\ud800"', '{"x":9007199254740992}',
], ids=['duplicate', 'nested_duplicate', 'nan', 'infinity', 'overflow', 'depth', 'frame_size', 'utf8', 'surrogate', 'unsafe_integer'])
def test_strict_json_rejects_ambiguous_or_unbounded_input(raw):
    with pytest.raises(ContractError):
        strict_loads(raw)


def test_depth_counts_containers_and_ignores_brackets_in_strings():
    assert strict_loads('[' * 32 + '"[{}]"' + ']' * 32)


def test_all_shared_seed_fixtures_match_declared_verdicts():
    manifest = json.loads((CONTRACTS / 'examples.manifest.json').read_text())
    for case in manifest['examples']:
        payload = strict_loads((CONTRACTS / case['example']).read_bytes())
        if case['expected'] == 'valid':
            validate(case['schema'], payload)
        else:
            with pytest.raises(ContractError):
                validate(case['schema'], payload)


def test_every_method_has_positive_and_negative_request_and_result_fixtures():
    fixtures = json.loads((CONTRACTS / 'method-fixtures.json').read_text())['cases']
    assert len(fixtures) == len(METHODS) * 4
    for case in fixtures:
        if case['expected'] == 'valid':
            validate(case['schema'], case['value'])
        else:
            with pytest.raises(ContractError):
                validate(case['schema'], case['value'])


def test_bridge_event_and_snapshot_contracts_have_shared_positive_and_negative_fixtures():
    fixtures = json.loads((CONTRACTS / 'additional-fixtures.json').read_text())['cases']
    assert len(fixtures) == len(BRIDGE_METHODS) * 4 + len(EVENTS) * 2 + 12
    for case in fixtures:
        if case['expected'] == 'valid':
            validate(case['schema'], case['value'])
        else:
            with pytest.raises(ContractError):
                validate(case['schema'], case['value'])


def test_event_payload_validation_and_namespace_separation():
    value = json.loads((CONTRACTS / 'examples/event-envelope.valid.json').read_text())
    validate_event(value)
    with pytest.raises(ContractError):
        validate_event({**value, 'attributes': {**value['attributes'], 'secret': 'synthetic'}})
    with pytest.raises(ContractError):
        validate_request({'jsonrpc': '2.0', 'id': 'r1', 'method': 'execute', 'params': {}}, 'main')
    assert not set(BRIDGE_METHODS).intersection(METHODS)


def test_unknown_fields_and_forged_channel_role_are_rejected():
    payload = json.loads((CONTRACTS / 'examples/start-turn.valid.json').read_text())
    with pytest.raises(ContractError):
        validate('start-turn', {**payload, 'role': 'admin'})
    validate_request({'jsonrpc': '2.0', 'id': 'r1', 'method': 'session.start_turn', 'params': payload}, 'renderer')
    with pytest.raises(ContractError):
        validate_request({'jsonrpc': '2.0', 'method': 'session.start_turn', 'params': payload}, 'renderer')
    with pytest.raises(ContractError):
        validate_request({'jsonrpc': '2.0', 'id': 42, 'method': 'system.health', 'params': {}}, 'renderer')


def test_main_methods_cannot_be_authorized_by_renderer_payload():
    request = {'jsonrpc': '2.0', 'id': 'r1', 'method': 'workspace.register',
               'params': {'client_action_id': 'act-11111111-1111-4111-8111-111111111111',
                          'path': 'D:/project', 'selection_nonce': 'n' * 32}}
    with pytest.raises(ContractError) as caught:
        validate_request(request, 'renderer')
    assert caught.value.kind == 'UNAUTHORIZED'
    validate_request(request, 'main')
    assert METHODS['credentials.inject']['audience'] == 'main'
    assert METHODS['sandbox.setup']['audience'] == 'main'


def test_canonical_hash_is_order_independent_and_configuration_sensitive():
    left = {'z': ['x', 'y'], 'budget': '2.5', '\U00010000': 'astral', '\ue000': 'bmp'}
    right = dict(reversed(list(left.items())))
    assert canonical_hash(left) == canonical_hash(right)
    assert canonical_hash({**left, 'budget': '2.6'}) != canonical_hash(left)
    assert canonical_hash({**left, 'z': ['y', 'x']}) != canonical_hash(left)
    assert canonical_hash({'x': 1.0}) == canonical_hash({'x': 1})
    with pytest.raises(ContractError):
        canonical_hash({'temperature': 0.5})


def test_runspec_requires_exact_task_revisions_and_conservative_trial_budget():
    spec = json.loads((CONTRACTS / 'examples/run-spec.valid.json').read_text())
    validate('run-spec', spec)
    changed = copy.deepcopy(spec)
    changed['dataset']['task_revisions']['unselected-task'] = 'v1'
    with pytest.raises(ContractError):
        validate('run-spec', changed)
    changed = copy.deepcopy(spec)
    changed['budget']['trial_wall_seconds'] = changed['budget']['attempt_wall_seconds'] - 1
    with pytest.raises(ContractError):
        validate('run-spec', changed)


def test_contract_generation_has_no_drift():
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check_contracts.py'), '--check'],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_authoritative_contracts_reject_duplicate_definitions(tmp_path):
    from scripts.check_contracts import read_contract
    source = tmp_path / 'duplicate.json'
    source.write_text('{"methods":{"workspace.authorize":{"audience":"main"},"workspace.authorize":{"audience":"renderer"}}}', encoding='utf-8')
    with pytest.raises(ValueError, match='Duplicate contract key'):
        read_contract(source)
