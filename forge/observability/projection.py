"""Derived query tables, updated only when a new trusted event is committed."""
import json
from forge.application.models import canonical_hash
from forge.observability.events import span_attributes


def project(store, body):
    if body['origin'] not in ('trusted_engine','trusted_bridge','grader_adapter'):
        return
    connection=store.connection
    event_type=body['event_type']
    attributes=body['attributes']
    if body['trace_id'] and body['span_id'] and event_type!='model.usage.confirmed':
        identity=(body['trace_id'],body['span_id'])
        row=connection.execute('SELECT parent_id FROM spans WHERE trace_id=? AND span_id=?',identity).fetchone()
        if row is not None and row[0]!=body['parent_span_id']:
            from forge.application.models import ContractError
            raise ContractError('Span parent conflicts with its original identity',kind='EVENT_CONFLICT',code=-32010)
        connection.execute('INSERT OR IGNORE INTO spans VALUES(?,?,?,?,?,?)',
            (*identity,body['parent_span_id'],attributes.get('operation_started_at_utc') or body['occurred_at_utc'],None,json.dumps(span_attributes(body))))
        if event_type.endswith(('.finished','.failed')) or event_type in ('context.prepared','completion.accepted','completion.rejected','sandbox.prepared','sandbox.denied','sandbox.cleanup_finished'):
            connection.execute('UPDATE spans SET end=?,attributes=? WHERE trace_id=? AND span_id=?',
                (body['occurred_at_utc'],json.dumps(span_attributes(body)),*identity))
        connection.execute('INSERT OR IGNORE INTO span_details VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (*identity,body['workspace_id'],body['session_id'],body['turn_id'],body['run_id'],event_type,'running',None,attributes.get('operation_start_monotonic_ns') or body['monotonic_ns'],None,json.dumps(attributes)))
        if event_type=='model.request.chunk':
            connection.execute('UPDATE span_details SET first_chunk_at=COALESCE(first_chunk_at,?) WHERE trace_id=? AND span_id=?',
                (attributes.get('first_client_text_chunk_at_utc') or body['occurred_at_utc'],*identity))
        ended=connection.execute('SELECT end FROM spans WHERE trace_id=? AND span_id=?',identity).fetchone()[0]
        if ended:
            result=attributes.get('result') or attributes.get('outcome')
            state='indeterminate' if result=='indeterminate' or attributes.get('cleanup_state') in ('unknown','residual') else 'cancelled' if result=='cancelled' or attributes.get('execution_state')=='cancelled' else 'error' if event_type.endswith(('.failed','.denied')) or result in ('failed','denied') or attributes.get('grade_state')=='grader_error' or attributes.get('execution_state') in ('error','blocked') else 'ok'
            connection.execute('UPDATE span_details SET state=?,end_monotonic=?,metadata_json=? WHERE trace_id=? AND span_id=?',
                (state,body['monotonic_ns'],json.dumps(attributes),*identity))
    if event_type=='model.request.started':
        connection.execute('INSERT INTO model_requests VALUES(?,?,?,?,?,?)',
            (attributes['model_request_id'],attributes['invocation_id'],attributes['attempt_no'],attributes['role'],None,'running'))
        from forge.observability.usage_ledger import start_request
        start_request(store,body)
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
        from forge.observability.usage_ledger import update_request
        update_request(store,body)
    elif event_type=='model.usage.confirmed':
        from forge.observability.usage_ledger import update_request
        update_request(store,body)
    if event_type in ('context.prepared','compaction.started','compaction.finished','compaction.failed') and body['turn_id']:
        metadata=attributes.get('context_metadata',{})
        snapshot=store._configuration_snapshot(metadata,canonical_hash(metadata))
        row=connection.execute('SELECT id FROM context_snapshots WHERE turn_id=? AND version=?',(body['turn_id'],attributes['context_version'])).fetchone()
        if row:
            connection.execute('UPDATE context_snapshots SET metadata_json=?,snapshot_ref=? WHERE id=?',(json.dumps(attributes),json.dumps(snapshot),row[0]))
        else:
            from forge.engine.persistence import new_id
            connection.execute('INSERT INTO context_snapshots VALUES(?,?,?,?,?,?,?,?)',
                (new_id('snap'),body['turn_id'],attributes['context_version'],None,None,attributes['reason'],json.dumps(attributes),json.dumps(snapshot)))
    if event_type=='verification.finished' and body['turn_id']:
        a=attributes
        facts=a.get('evidence_metadata',{})
        validity='current' if facts.get('stable_during_command') else 'missing'
        connection.execute('INSERT INTO evidence_refs VALUES(?,?,?,?,?,?)',
            (a['evidence_id'],body['turn_id'],a['workspace_revision'],a['environment_epoch'],a['result'],None))
        connection.execute('INSERT INTO evidence_details VALUES(?,?,?,?,?)',
            (a['evidence_id'],json.dumps(a),validity,'observed_command' if validity=='current' else 'complete_workspace_observation_unavailable',body['occurred_at_utc']))
    elif event_type=='verification.invalidated':
        connection.execute('UPDATE evidence_details SET validity=?,reason=? WHERE id=?',('stale',attributes['reason'],attributes['evidence_id']))
    from forge.observability.export_queue import enqueue
    enqueue(store,body)
