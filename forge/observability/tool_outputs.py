"""Safe stream copies only; private context/model debug files have no reader API."""
from hashlib import sha256
import json
from pathlib import Path

from benchmark.core.bundle import selected_file
from forge.application.models import ContractError, strict_loads
from forge.engine.persistence import encoded
from forge.observability.export_queue import redact
from forge.sessions.store import SessionStore


def output(views,params):
    store=views.store
    views.check_scope({'kind':'turn','id':params['turn_id']})
    row=store.connection.execute('SELECT x.*,t.native_ref,w.canonical_path FROM execution_spans x JOIN turns t ON t.id=x.turn_id '
        'JOIN sessions s ON s.id=t.session_id JOIN workspaces w ON w.id=s.workspace_id WHERE x.execution_id=? AND x.turn_id=?',
        (params['execution_id'],params['turn_id'])).fetchone()
    if row is None: raise ContractError('Execution does not belong to this turn',kind='NOT_FOUND',code=-32010)
    result={'execution_id':params['execution_id'],'state':'unavailable','reason':'capture_unavailable','artifact_id':None,'sha256':None,'size_bytes':None}
    event=store.connection.execute("SELECT body_json FROM events WHERE json_extract(body_json,'$.event_type')='tool.finished' "
        "AND json_extract(body_json,'$.execution_id')=? AND json_extract(body_json,'$.origin')='trusted_engine' ORDER BY store_seq DESC LIMIT 1",(params['execution_id'],)).fetchone()
    capture=json.loads(event[0])['attributes'].get('debug_capture',{}) if event else {}
    if capture.get('state')!='captured':
        result['reason']='capture_disabled' if capture.get('state')=='disabled' else capture.get('reason','capture_unavailable')
        return result
    expected_name='tool-'+params['execution_id']
    if capture.get('local_name')!=expected_name or not row['native_ref']:
        return {**result,'state':'missing','reason':'capture_binding_missing'}
    directory=SessionStore(Path(row['canonical_path']),data_root=store.data_dir/'harness').directory
    path=directory/'controlled-debug'/row['trace_id']/(expected_name+'.json')
    try:
        with selected_file(path) as source: raw=source.read(262145)
        if len(raw)>262144 or len(raw)!=capture['size_bytes'] or sha256(raw).hexdigest()!=capture['sha256']:
            raise ValueError('capture_integrity')
        value=strict_loads(raw)
        if not isinstance(value,dict): raise ValueError('capture_shape')
        streams={k:v for k,v in value.items() if k in ('stdout','stderr') and isinstance(v,str)}
        if not streams: return {**result,'reason':'no_safe_streams'}
    except (OSError,ValueError,KeyError,ContractError):
        return {**result,'state':'missing','reason':'capture_missing_or_changed'}
    # Recheck original capture on each read; cached copies never hide a source gap.
    cache=store.connection.execute('SELECT artifact_id FROM tool_output_views WHERE execution_id=? AND source_sha256=?',
        (params['execution_id'],capture['sha256'])).fetchone()
    if cache:
        artifact=views.methods.artifacts.describe({'artifact_id':cache[0]})
        if not artifact['available']: return {**result,'state':'missing','reason':'stream_artifact_missing_or_changed'}
        identity=artifact['artifact_id']; digest=artifact['sha256']; size=artifact['size_bytes']
    else:
        if store.read_only: return {**result,'reason':'store_read_only'}
        safe=encoded(redact(streams,getattr(store,'observation_secrets',()))).encode('utf-8')
        if len(safe)>262144:return {**result,'reason':'safe_stream_view_quota'}
        artifact=store.publish_artifact(safe,origin='trusted_engine',classification='metadata',profile_id=views.service.profile_id,
            media_type='application/json',redaction_version='stream-view-v1')
        with store.transaction(): store.connection.execute('INSERT INTO tool_output_views VALUES(?,?,?)',(params['execution_id'],capture['sha256'],artifact['id']))
        identity=artifact['id']; digest=artifact['sha256']; size=artifact['size']
    return {**result,'state':'available','reason':'controlled_debug_safe_streams','artifact_id':identity,'sha256':digest,'size_bytes':size}
