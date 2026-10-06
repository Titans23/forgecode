"""Pinned OTLP/HTTP JSON spans; only curated metadata leaves the local store."""
from datetime import datetime,timezone
import json

from forge.observability.events import SEMANTIC_MAPPING_VERSION,SEMANTIC_SOURCE_REVISION,span_attributes

OTLP_PROTOCOL_VERSION='1.11.0'


def _nanoseconds(timestamp):
    value=datetime.fromisoformat(timestamp.replace('Z','+00:00'))
    delta=value-datetime(1970,1,1,tzinfo=timezone.utc)
    return str((delta.days*86400+delta.seconds)*1000000000+delta.microseconds*1000)


def _value(value):
    if type(value) is bool:
        return {'boolValue':value}
    if type(value) is int:
        return {'intValue':str(value)}
    return {'stringValue':str(value)}


def export_span(store,body):
    if not body['trace_id'] or not body['span_id']:
        return None
    row=store.connection.execute('SELECT * FROM spans WHERE trace_id=? AND span_id=?',(body['trace_id'],body['span_id'])).fetchone()
    if row is None or row['end'] is None or body['event_type']=='model.usage.confirmed':
        return None
    details=store.connection.execute('SELECT state FROM span_details WHERE trace_id=? AND span_id=?',(body['trace_id'],body['span_id'])).fetchone()
    kind=body['event_type']
    if kind not in ('context.prepared','completion.accepted','completion.rejected','sandbox.prepared','sandbox.denied') and not kind.endswith(('.finished','.failed')):
        return None
    attributes=span_attributes(body)
    attributes['forge.semantic_mapping.source_revision']=SEMANTIC_SOURCE_REVISION
    for key in ('workspace_id','session_id','turn_id','run_id','trial_id','attempt_id','execution_id'):
        if body[key]:
            attributes['forge.'+key]=body[key]
    for key in ('role','attempt_no','usage_quality','result','cleanup_state','before_tokens','after_tokens'):
        if isinstance(body['attributes'].get(key),(str,int,bool)):
            attributes['forge.'+key]=body['attributes'][key]
    name=' '.join(str(v) for v in (attributes.get('gen_ai.operation.name'),attributes.get('gen_ai.request.model') or attributes.get('gen_ai.tool.name')) if v) or kind
    span={'traceId':body['trace_id'],'spanId':body['span_id'],'name':name[:128],'kind':3 if kind.startswith('model.') else 1,
        'startTimeUnixNano':_nanoseconds(row['start']),'endTimeUnixNano':_nanoseconds(row['end']),
        'attributes':[{'key':key,'value':_value(value)} for key,value in attributes.items()],
        'status':{'code':2 if details and details['state'] in ('error','indeterminate','cancelled') else 1}}
    if body['parent_span_id']:
        span['parentSpanId']=body['parent_span_id']
    if body['attributes'].get('trace_links'):
        span['links']=[{'traceId':link['trace_id'],'spanId':link['span_id']} for link in body['attributes']['trace_links']]
    return {'resourceSpans':[{'resource':{'attributes':[{'key':'service.name','value':{'stringValue':'forgecode-engine'}}]},
        'scopeSpans':[{'scope':{'name':'forgecode.harness','version':SEMANTIC_MAPPING_VERSION},'spans':[span]}]}]}
