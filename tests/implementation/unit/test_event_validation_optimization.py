"""Compare the actual optimized validator to authoritative unchanged schemas."""
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import pytest

from forge.application.models import ContractError, _SCHEMAS, _REGISTRY, validate, validate_event, _absolute_references


def accepted(schema, value):
    return Draft202012Validator(schema,registry=_REGISTRY,format_checker=FormatChecker()).is_valid(value)


def test_nullable_event_fields_keep_original_verdict_for_valid_and_invalid_values():
    event=json.loads(Path('contracts/v1/examples/event-envelope.valid.json').read_text(encoding='utf-8'))
    schema=_SCHEMAS['event-envelope'];original=deepcopy(schema)
    for field in schema['properties']:
        for value in (None,False,0,1.5,{},[], '', '0'*16,'0'*32,'f'*16,'f'*32,'unknown','x'*129):
            candidate={**event,field:value}
            expected=accepted(schema,candidate)
            try:validate('event-envelope',candidate);actual=True
            except ContractError:actual=False
            assert actual==expected,(field,value)
    assert schema==original


def test_every_event_payload_fixture_and_mutation_matches_original_schema():
    cases=json.loads(Path('contracts/v1/additional-fixtures.json').read_text(encoding='utf-8'))['cases']
    original=deepcopy(_SCHEMAS)
    for case in cases:
        key=case['schema'].removeprefix('schemas/').removesuffix('.schema.json')
        if not key.startswith('event.'):continue
        for value in (case['value'],{**case['value'],'unexpected_field':'rejected'},None):
            expected=accepted(_SCHEMAS[key],value)
            try:validate(key,value);actual=True
            except ContractError:actual=False
            assert actual==expected,key
    assert _SCHEMAS==original


@pytest.mark.parametrize('invalid',[float('nan'),2**54,'\ud800'])
def test_single_tree_check_still_rejects_invalid_nested_payload_json(invalid):
    event=json.loads(Path('contracts/v1/examples/event-envelope.valid.json').read_text(encoding='utf-8'))
    event['attributes']['nested']={'value':invalid}
    with pytest.raises(ContractError):validate_event(event)


def test_relative_or_dynamic_schema_scope_is_never_expanded():
    assert _absolute_references({'properties':{'scope':{'$ref':'urn:forgecode:contracts:v1:scope'}}})
    for marker in ({'$ref':'#/$defs/local'},{'$id':'another-resource'},{'$dynamicRef':'#anchor'},{'$anchor':'local'}):
        assert not _absolute_references({'properties':{'scope':marker}})
