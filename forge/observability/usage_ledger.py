"""Disjoint provider token components and exact, frozen-reference accounting."""
from copy import deepcopy
from decimal import Decimal, localcontext
import json
import re

from forge.observability.events import digest

SAFE_INTEGER=2**53-1
RATE=re.compile(r'^(0|[1-9][0-9]*)(\.[0-9]*[1-9])?$')


def _count(value):
    if type(value) is not int or not 0<=value<=SAFE_INTEGER:
        raise ValueError('Usage requires nonnegative safe integers')
    return value


def usage_dict(usage):
    """Preserve SDK usage objects, excluding unset partial-stream fields."""
    if usage is None or isinstance(usage,(str,int,float,bool)):
        return usage
    if isinstance(usage,(list,tuple)):
        return [usage_dict(item) for item in usage]
    if hasattr(usage,'model_dump'):
        usage=usage.model_dump(exclude_none=True)
    elif not isinstance(usage,dict):
        usage=vars(usage)
    return {key:usage_dict(value) for key,value in usage.items() if value is not None}


def normalize_usage(provider,raw):
    if raw is None:
        return None
    component=raw.get('schema')=='forge.usage.components.v1'
    cache_write=cache_read=0
    if provider=='anthropic' or component:
        ordinary=_count(raw['input_tokens'])
        output=_count(raw['output_tokens'])
        cache_write=_count(raw.get('cache_creation_input_tokens',0))
        cache_read=_count(raw.get('cache_read_input_tokens',0))
        total=_count(ordinary+cache_write+cache_read)
    elif provider in ('openai_responses','openai','deepseek'):
        chat='prompt_tokens' in raw
        total=_count(raw['prompt_tokens' if chat else 'input_tokens'])
        output=_count(raw['completion_tokens' if chat else 'output_tokens'])
        details=raw.get('prompt_tokens_details' if chat else 'input_tokens_details') or {}
        cache_read=_count(raw.get('prompt_cache_hit_tokens',details.get('cached_tokens',0)))
        cache_write=_count(details.get('cache_write_tokens',0))
        ordinary=_count(total-cache_read-cache_write)
        if 'prompt_cache_miss_tokens' in raw and _count(raw['prompt_cache_miss_tokens'])!=ordinary+cache_write:
            raise ValueError('Cache hit/miss counts disagree with input total')
    else:
        raise ValueError('Provider usage semantics are unknown')
    components={'input_tokens':ordinary,'output_tokens':output,'cache_read_tokens':cache_read,'cache_write_tokens':cache_write}
    ttl=raw.get('cache_creation')
    if ttl and any(ttl.get(k,0) for k in ('ephemeral_5m_input_tokens','ephemeral_1h_input_tokens')):
        short=_count(ttl.get('ephemeral_5m_input_tokens',0))
        long=_count(ttl.get('ephemeral_1h_input_tokens',0))
        if short+long!=cache_write:
            raise ValueError('Cache TTL split disagrees with cache creation total')
        components.pop('cache_write_tokens')
        components.update(cache_write_5m_tokens=short,cache_write_1h_tokens=long)
    return {'input_tokens':total,'output_tokens':output,'uncached_input_tokens':ordinary,
        'cache_write_tokens':cache_write,'cache_read_tokens':cache_read,'components':components,
        'normalizer_version':'forge.usage.disjoint.v1'}


def token_usage(provider,raw):
    from forge.runtime.state import TokenUsage
    value=normalize_usage(provider,raw)
    return TokenUsage(value['uncached_input_tokens'],value['output_tokens'],value['cache_write_tokens'],value['cache_read_tokens'])


def decimal_text(value):
    text=format(value,'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


class PriceBook:
    """Caller-supplied immutable reference; no guessed or live mutable prices."""
    def __init__(self,value):
        self._value=deepcopy(value)
        if set(value)!={'revision','currency','source','rates'} or value['currency']!='USD':
            raise ValueError('Pricing requires a USD revision, source and exact model rates')
        if any(not isinstance(value[k],str) or not 1<=len(value[k])<=512 for k in ('revision','source')):
            raise ValueError('Pricing provenance is missing')
        if not isinstance(value['rates'],list) or len(value['rates'])>10000:
            raise ValueError('Pricing rate limit exceeded')
        seen=set()
        allowed={'input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','cache_write_5m_tokens','cache_write_1h_tokens'}
        for row in value['rates']:
            if set(row)!={'provider','model','per_million'} or any(not isinstance(row[k],str) or not 1<=len(row[k])<=256 for k in ('provider','model')):
                raise ValueError('Invalid exact pricing identity')
            key=(row['provider'],row['model'])
            if key in seen:
                raise ValueError('Duplicate pricing identity')
            seen.add(key)
            if not isinstance(row['per_million'],dict) or set(row['per_million'])-allowed:
                raise ValueError('Unknown pricing component')
            for rate in row['per_million'].values():
                if not isinstance(rate,str) or len(rate)>20 or not RATE.fullmatch(rate) or Decimal(rate)>1000000:
                    raise ValueError('Prices require bounded canonical decimal strings')
        self.sha256=digest(self._value)

    def as_dict(self):
        return deepcopy(self._value)

    def lookup(self,provider,model):
        for row in self._value['rates']:
            if (row['provider'],row['model'])==(provider,model):
                return deepcopy(row['per_million'])
        return None


def calculate_cost(usage,rates):
    if usage is None or rates is None:
        return None
    with localcontext() as context:
        context.prec=80
        cost=Decimal(0)
        for component,count in usage['components'].items():
            if not count:
                continue
            if component not in rates:
                return None
            cost+=Decimal(count)*Decimal(rates[component])/Decimal(1000000)
        return decimal_text(cost)


def start_request(store,body):
    from forge.application.models import ContractError
    a=body['attributes']
    scope = [body[key] for key in ('run_id', 'trial_id', 'attempt_id')]
    if any(scope) and not all(scope):
        raise ContractError('Model request evaluation scope is incomplete',kind='EVENT_CONFLICT',code=-32010)
    attempt=store.connection.execute('SELECT a.id,r.spec_json,d.owner_epoch,d.trace_id,w.state FROM attempts a JOIN trials t ON t.id=a.trial_id JOIN runs r ON r.id=t.run_id JOIN attempt_details d ON d.attempt_id=a.id JOIN work_items w ON w.id=d.work_item_id WHERE a.id=? AND t.run_id=? AND t.id=?',
        (body['attempt_id'],body['run_id'],body['trial_id'])).fetchone() if body['attempt_id'] else None
    if body['attempt_id'] and (attempt is None or attempt['owner_epoch']!=store.epoch or attempt['state'] not in ('running','cancel_requested') or attempt['trace_id']!=body['trace_id']):
        raise ContractError('Model request evaluation identities conflict',kind='EVENT_CONFLICT',code=-32010)
    frozen=store.connection.execute('SELECT pricing_snapshot FROM turn_observation_config WHERE turn_id=?',(body['turn_id'],)).fetchone()
    snapshot=json.loads(attempt['spec_json'])['observability']['pricing_snapshot'] if attempt else json.loads(frozen[0]) if frozen and frozen[0] else None
    store.connection.execute('INSERT INTO request_details VALUES(?,?,?,?,?,?,?,?,?,?)',
        (a['model_request_id'],body['turn_id'],body['workspace_id'],body['session_id'],body['run_id'],
         body['trace_id'],body['span_id'],a.get('provider','unreported'),a['requested_model'],json.dumps(snapshot) if snapshot else None))
    store.connection.execute('INSERT INTO usage_ledger(request_id,quality,cost_quality) VALUES(?,?,?)',
        (a['model_request_id'],'unknown','unknown'))
    if attempt:
        store.connection.execute('INSERT INTO attempt_requests VALUES(?,?)',(a['model_request_id'],attempt['id']))


def update_request(store,body):
    from forge.application.models import ContractError
    a=body['attributes']
    request_id=a['model_request_id']
    row=store.connection.execute('SELECT * FROM usage_ledger WHERE request_id=?',(request_id,)).fetchone()
    details=store.connection.execute('SELECT * FROM request_details WHERE request_id=?',(request_id,)).fetchone()
    if row is None or details is None:
        raise ContractError('Usage has no actual request boundary',kind='EVENT_CONFLICT',code=-32010)
    identity=store.connection.execute('SELECT invocation_id,attempt_no,role FROM model_requests WHERE id=?',(request_id,)).fetchone()
    if tuple(identity)!=(a['invocation_id'],a['attempt_no'],a['role']):
        raise ContractError('Usage attribution differs from the actual request',kind='EVENT_CONFLICT',code=-32010)
    if any(details[key] != body[key] for key in ('turn_id', 'workspace_id', 'session_id', 'run_id', 'trace_id', 'span_id')) or (
            details['provider'], details['requested_model']) != (a.get('provider', 'unreported'), a['requested_model']):
        raise ContractError('Usage scope differs from the actual request',kind='EVENT_CONFLICT',code=-32010)
    attempt = store.connection.execute('SELECT ar.attempt_id,a.trial_id FROM attempt_requests ar JOIN attempts a ON a.id=ar.attempt_id WHERE ar.request_id=?',
        (request_id,)).fetchone()
    if (body['attempt_id'], body['trial_id']) != (tuple(attempt) if attempt else (None, None)):
        raise ContractError('Usage evaluation scope differs from the actual request',kind='EVENT_CONFLICT',code=-32010)
    raw=a.get('raw_usage')
    observed=a.get('usage')
    # Older trusted events report totals only. Keep the totals without guessing cache prices.
    try:
        normalized=normalize_usage(details['provider'],raw) if raw is not None else (
            {**observed,'components':{}} if observed is not None else None)
    except (ValueError,KeyError,TypeError,AttributeError) as error:
        raise ContractError('Provider usage is inconsistent',kind='EVENT_CONFLICT',code=-32010) from error
    if a['usage_quality']=='actual' and (normalized is None or any(normalized.get(k) is None for k in ('input_tokens','output_tokens'))):
        raise ContractError('Actual usage requires observed token counts',kind='EVENT_CONFLICT',code=-32010)
    if normalized is not None and observed is not None and any(normalized[k]!=observed[k] for k in ('input_tokens','output_tokens')):
        raise ContractError('Raw and normalized token totals conflict',kind='EVENT_CONFLICT',code=-32010)
    normalized_json=json.dumps(normalized,sort_keys=True) if normalized is not None else None
    raw_json=json.dumps(raw,sort_keys=True) if raw is not None else None
    if row['quality']!='unknown':
        if (row['normalized_usage'],row['raw_usage'],row['quality'])!=(normalized_json,raw_json,a['usage_quality']):
            raise ContractError('Confirmed usage cannot be overwritten',kind='EVENT_CONFLICT',code=-32010)
        return
    price=None
    snapshot=json.loads(details['pricing_snapshot']) if details['pricing_snapshot'] else None
    if snapshot:
        source=store.connection.execute('SELECT normalized_json FROM configuration_snapshots WHERE id=? AND hash=?',
            (snapshot['snapshot_id'],snapshot['sha256'])).fetchone()
        value=json.loads(source[0])
        if 'rates' not in value:
            from forge.application.models import validate
            validate('pricing',value)
            value={'revision':value['revision'],'currency':value['currency'],'source':'frozen-pricing:'+value['effective_at_utc'],
                'rates':[{'provider':value['provider'],'model':value['model'],'per_million':{key:value[field] for key,field in (
                    ('input_tokens','input_per_million'),('output_tokens','output_per_million'),
                    ('cache_read_tokens','cache_read_per_million'),('cache_write_tokens','cache_write_per_million')) if value[field] is not None}}]}
        price=PriceBook(value)
    rates=price.lookup(details['provider'],a.get('returned_model') or details['requested_model']) if price else None
    amount=calculate_cost(normalized,rates) if a['usage_quality']!='unknown' and normalized and normalized.get('components') else None
    quality=a['usage_quality'] if amount is not None else 'unknown'
    store.connection.execute('UPDATE usage_ledger SET raw_usage=?,normalized_usage=?,price_revision=?,cost=?,quality=?,currency=?,cost_quality=? WHERE request_id=?',
        (raw_json,normalized_json,price.as_dict()['revision'] if price else None,amount,a['usage_quality'],'USD' if price else None,quality,request_id))
    if a.get('provider_request_id'):
        store.connection.execute('UPDATE model_requests SET provider_request_id=? WHERE id=?',(a['provider_request_id'],request_id))


def confirm_usage(store,request_id,raw_usage,*,returned_model=None,provider_request_id=None):
    """Trusted late-provider callback. A confirmation replaces the unknown row."""
    from forge.application.models import ContractError
    source=store.connection.execute("SELECT body_json FROM events WHERE json_extract(body_json,'$.event_type')='model.request.started' "
        "AND json_extract(body_json,'$.attributes.model_request_id')=? AND json_extract(body_json,'$.origin')='trusted_engine'",(request_id,)).fetchone()
    if source is None:
        raise ContractError('No owned model request',kind='NOT_FOUND',code=-32010)
    original=json.loads(source[0])
    attributes=original['attributes']
    try:
        usage=normalize_usage(attributes.get('provider','unreported'),raw_usage)
    except (ValueError,KeyError,TypeError,AttributeError) as error:
        raise ContractError('Provider usage is inconsistent',kind='EVENT_CONFLICT',code=-32010) from error
    attributes={**attributes,'raw_usage':raw_usage,'usage':{k:usage[k] for k in ('input_tokens','output_tokens')},
        'usage_quality':'actual','returned_model':returned_model,'provider_request_id':provider_request_id,'usage_is_final':True}
    producer=store.connection.execute("SELECT value FROM store_meta WHERE key='producer_id'").fetchone()[0]
    sequence=store.connection.execute('SELECT COALESCE(MAX(source_seq),0)+1 FROM events WHERE source_id=?',(producer,)).fetchone()[0]
    body=store.event_body('model.usage.confirmed',producer,sequence,attributes,
        **{key:original[key] for key in ('workspace_id','session_id','turn_id','run_id','trial_id','attempt_id','trace_id','span_id','parent_span_id','execution_id')})
    return store.append_event(body,producer,sequence)
