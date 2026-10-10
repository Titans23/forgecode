"""F18 accounting uses provider usage fixtures, exact decimals and bounded metadata."""
import pytest

from forge.observability.usage_ledger import normalize_usage, PriceBook, calculate_cost
from forge.observability.context_snapshots import describe_context, compare_context
from forge.observability.export_queue import ObservationOptions, redact


@pytest.mark.parametrize('provider,raw,expected',[
    ('anthropic',{'input_tokens':50,'output_tokens':10,'cache_creation_input_tokens':20,'cache_read_input_tokens':30},(100,50,20,30)),
    ('openai_responses',{'input_tokens':100,'output_tokens':10,'input_tokens_details':{'cached_tokens':30,'cache_write_tokens':20}},(100,50,20,30)),
    ('deepseek',{'prompt_tokens':100,'completion_tokens':10,'prompt_cache_hit_tokens':30,'prompt_cache_miss_tokens':70},(100,70,0,30)),
])
def test_provider_cache_components_are_disjoint(provider,raw,expected):
    normalized=normalize_usage(provider,raw)
    assert tuple(normalized[k] for k in ('input_tokens','uncached_input_tokens','cache_write_tokens','cache_read_tokens'))==expected
    assert normalized['output_tokens']==10


@pytest.mark.parametrize('provider,raw',[
    ('openai_responses',{'input_tokens':5,'output_tokens':1,'input_tokens_details':{'cached_tokens':6}}),
    ('anthropic',{'input_tokens':True,'output_tokens':1}),
    ('deepseek',{'prompt_tokens':5,'completion_tokens':1,'prompt_cache_hit_tokens':2,'prompt_cache_miss_tokens':4}),
])
def test_inconsistent_usage_is_not_clamped_to_free_or_actual(provider,raw):
    with pytest.raises(ValueError):
        normalize_usage(provider,raw)


def book():
    return PriceBook({'revision':'fixture-2026-10-07','currency':'USD','source':'deterministic-test-rates',
        'rates':[{'provider':'anthropic','model':'scripted-test','per_million':
            {'input_tokens':'3','output_tokens':'15','cache_write_tokens':'3.75','cache_read_tokens':'0.3'}}]})


def test_cost_exactly_recomputes_and_missing_rates_are_unknown():
    rates=book().lookup('anthropic','scripted-test')
    normalized=normalize_usage('anthropic',{'input_tokens':50,'output_tokens':10,'cache_creation_input_tokens':20,'cache_read_input_tokens':30})
    assert calculate_cost(normalized,rates)=='0.000384'
    assert calculate_cost(normalized,None) is None and normalize_usage('anthropic',None) is None
    assert calculate_cost(normalized,{'input_tokens':'3','output_tokens':'15'}) is None
    assert book().lookup('anthropic','other-model') is None


def test_cache_ttl_split_requires_its_own_prices():
    usage=normalize_usage('anthropic',{'input_tokens':10,'output_tokens':1,'cache_creation_input_tokens':30,
        'cache_creation':{'ephemeral_5m_input_tokens':10,'ephemeral_1h_input_tokens':20}})
    rates={'input_tokens':'3','output_tokens':'15','cache_write_5m_tokens':'3.75','cache_write_1h_tokens':'6'}
    assert calculate_cost(usage,rates)=='0.0002025'
    assert calculate_cost(usage,book().lookup('anthropic','scripted-test')) is None


def test_exact_high_precision_cost_fits_the_public_usage_contract():
    from forge.application.models import validate
    normalized=normalize_usage('anthropic',{'input_tokens':2**53-1,'output_tokens':0})
    amount=calculate_cost(normalized,{'input_tokens':'1.000000000000000001'})
    assert amount=='9007199254.740991009007199254740991' and len(amount)>32
    validate('observability.usage.result',{'input_tokens':2**53-1,'output_tokens':0,'cost_decimal':amount,
        'currency':'USD','unknown_requests':0,'pricing_snapshot':None,'known_cost_decimal':amount,'estimated_cost_decimal':'0'})


def test_price_book_is_frozen_and_does_not_guess_invalid_rates():
    source={'revision':'local','currency':'USD','source':'explicit reference',
        'rates':[{'provider':'anthropic','model':'test','per_million':{'input_tokens':'1'}}]}
    prices=PriceBook(source)
    source['rates'][0]['per_million']['input_tokens']='99'
    assert prices.lookup('anthropic','test')['input_tokens']=='1'
    with pytest.raises(ValueError):
        PriceBook({**source,'currency':'CNY'})


def test_context_pairs_and_exact_constraint_retention_do_not_claim_summary_semantics():
    messages=[{'role':'user','content':'Do not change value.txt.'},
        {'role':'assistant','content':[{'type':'tool_use','id':'call-a','name':'read_file','input':{'path':'value.txt'}}]},
        {'role':'user','content':[{'type':'tool_result','tool_use_id':'call-a','content':'B'*16000}]}]
    before=describe_context(messages)
    after=describe_context(messages[:1]+messages[1:])
    assert before['pairing_complete'] and before['tool_pairs'][0]['state']=='paired'
    assert before['estimated_tokens']>4000
    assert compare_context(before,after)['retained_constraint_message_ids']==before['constraint_message_ids']
    summary=describe_context([{'role':'user','content':'Summary: preserve value.txt.'}])
    assert compare_context(before,summary)['retained_constraint_message_ids']==[]
    broken=describe_context(messages[2:])
    assert not broken['pairing_complete']


def test_redaction_known_secrets_precedes_generic_and_export_is_explicit_metadata():
    value={'text':'literal-special-key Authorization: Bearer other-secret',
        'Authorization':'Bearer private','nested':{'api_key':'private'}}
    clean=redact(value,('literal-special-key',))
    assert 'literal-special-key' not in str(clean) and 'other-secret' not in str(clean) and 'private' not in str(clean)
    assert ObservationOptions().endpoint is None and ObservationOptions().capture_mode=='metadata'
    with pytest.raises(ValueError):
        ObservationOptions(endpoint='http://127.0.0.1:4318/v1/traces')
    with pytest.raises(ValueError):
        ObservationOptions(endpoint='http://remote.invalid/v1/traces',metadata_export_confirmed=True)
