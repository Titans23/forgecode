"""Derived query tables, updated only when a new trusted event is committed."""
import json
from forge.observability.events import span_attributes


def project(store, body):
    if body['origin'] not in ('trusted_engine','trusted_bridge','grader_adapter'):
        return
    connection=store.connection
    event_type=body['event_type']
    attributes=body['attributes']
    if body['trace_id'] and body['span_id']:
        identity=(body['trace_id'],body['span_id'])
        row=connection.execute('SELECT parent_id FROM spans WHERE trace_id=? AND span_id=?',identity).fetchone()
        if row is not None and row[0]!=body['parent_span_id']:
            from forge.application.models import ContractError
            raise ContractError('Span parent conflicts with its original identity',kind='EVENT_CONFLICT',code=-32010)
        connection.execute('INSERT OR IGNORE INTO spans VALUES(?,?,?,?,?,?)',
            (*identity,body['parent_span_id'],body['occurred_at_utc'],None,json.dumps(span_attributes(body))))
        if event_type.endswith(('.finished','.failed')) or event_type in ('context.prepared','completion.accepted','completion.rejected'):
            connection.execute('UPDATE spans SET end=?,attributes=? WHERE trace_id=? AND span_id=?',
                (body['occurred_at_utc'],json.dumps(span_attributes(body)),*identity))
    if event_type=='model.request.started':
        connection.execute('INSERT INTO model_requests VALUES(?,?,?,?,?,?)',
            (attributes['model_request_id'],attributes['invocation_id'],attributes['attempt_no'],attributes['role'],None,'running'))
    elif event_type in ('model.request.finished','model.request.failed'):
        request_id=attributes['model_request_id']
        row=connection.execute('SELECT * FROM model_requests WHERE id=?',(request_id,)).fetchone()
        if row is None:
            from forge.application.models import ContractError
            raise ContractError('Model terminal event has no request boundary',kind='EVENT_CONFLICT',code=-32010)
        if row['state']!='running' or (row['invocation_id'],row['attempt_no'],row['role'])!=(attributes['invocation_id'],attributes['attempt_no'],attributes['role']):
            from forge.application.models import ContractError
            raise ContractError('Model terminal identity differs from its request',kind='EVENT_CONFLICT',code=-32010)
        connection.execute('UPDATE model_requests SET state=? WHERE id=?',
            ('finished' if event_type.endswith('.finished') else 'failed',request_id))
        usage=attributes.get('usage')
        quality=attributes['usage_quality']
        if quality=='actual' and (usage is None or any(v is None for v in usage.values())):
            from forge.application.models import ContractError
            raise ContractError('Actual usage requires observed token counts',kind='EVENT_CONFLICT',code=-32010)
        connection.execute('INSERT INTO usage_ledger VALUES(?,?,?,?,?,?)',
            (request_id,json.dumps(usage) if usage is not None else None,json.dumps(usage) if usage is not None else None,
             None,None,quality))
