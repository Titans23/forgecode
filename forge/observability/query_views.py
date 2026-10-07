"""Scoped, paged views over committed observations; reads never execute tools."""
from decimal import Decimal,localcontext
import json

from forge.application.models import ContractError
from forge.observability.usage_ledger import decimal_text,SAFE_INTEGER


class ObservationViews:
    def __init__(self,methods):
        self.methods=methods
        self.service=methods.service
        self.store=methods.store
        self.events=methods.events

    def check_scope(self,scope):
        self.events.check_scope(scope)
        table,column=('turn_profiles','turn_id') if scope['kind']=='turn' else ('run_details','run_id') if scope['kind']=='run' else (None,None)
        if table and not self.store.connection.execute('SELECT 1 FROM '+table+' WHERE '+column+'=? AND profile_id=?',(scope['id'],self.service.profile_id)).fetchone():
            raise ContractError('Observation scope belongs to another profile or has no proven owner',kind='UNAUTHORIZED',code=-32010)

    def where(self,scope,alias):
        self.check_scope(scope)
        owner='('+alias+'.turn_id IN (SELECT turn_id FROM turn_profiles WHERE profile_id=?) OR '+alias+'.run_id IN (SELECT run_id FROM run_details WHERE profile_id=?))'
        bindings=(self.service.profile_id,self.service.profile_id)
        if scope['kind']=='all': return owner,bindings
        return owner+' AND '+alias+'.'+scope['kind']+'_id=?',(*bindings,scope['id'])

    def page(self,params,name,scope,table,where,bindings,view):
        key={'collection':name,'scope':scope,**({'execution_id':params['execution_id']} if params.get('execution_id') else {}),
            **({'attempt_id':params['attempt_id']} if params.get('attempt_id') else {})}
        after=self.events.decode_cursor(params['cursor'],key) if params.get('cursor') else 0
        limit=params.get('limit',100)
        rows=self.store.connection.execute('SELECT d.rowid AS position,d.* FROM '+table+' d WHERE ('+where+') AND d.rowid>? ORDER BY d.rowid LIMIT ?',
            (*bindings,after,limit+1)).fetchall()
        items=[]
        size=0
        for row in rows[:limit]:
            item=view(row)
            length=len(json.dumps(item,ensure_ascii=False).encode('utf-8'))
            if items and size+length>196608:
                break
            items.append(item)
            size+=length
        return {'items':items,'next_cursor':self.events.cursor(key,rows[len(items)-1]['position']) if len(rows)>len(items) else None,'history_gap':False}

    def spans(self,params):
        where,bindings=self.where(params['scope'],'d')
        if params.get('execution_id'):
            where+=' AND EXISTS(SELECT 1 FROM execution_spans x WHERE x.trace_id=d.trace_id AND x.span_id=d.span_id AND x.execution_id=?)'
            bindings=(*bindings,params['execution_id'])
        if params.get('attempt_id'):
            where+=' AND EXISTS(SELECT 1 FROM attempt_details a WHERE a.trace_id=d.trace_id AND a.attempt_id=?)'
            bindings=(*bindings,params['attempt_id'])
        def view(row):
            span=self.store.connection.execute('SELECT * FROM spans WHERE trace_id=? AND span_id=?',(row['trace_id'],row['span_id'])).fetchone()
            duration=None
            if row['start_monotonic'] and row['end_monotonic']:
                duration=str(max(0,int(row['end_monotonic'])-int(row['start_monotonic'])))
            return {'trace_id':row['trace_id'],'span_id':row['span_id'],'parent_span_id':span['parent_id'],
                'name':row['name'],'started_at_utc':span['start'],'ended_at_utc':span['end'],'state':row['state'],
                'attributes':json.loads(span['attributes']),
                'metadata':{'mapping':json.loads(span['attributes']),'facts':json.loads(row['metadata_json']),
                    'duration_nanoseconds':duration,'start_monotonic_ns':row['start_monotonic'],'end_monotonic_ns':row['end_monotonic'],
                    'execution_id':(self.store.connection.execute('SELECT execution_id FROM execution_spans WHERE trace_id=? AND span_id=?',(row['trace_id'],row['span_id'])).fetchone() or [None])[0],
                    'first_client_text_chunk_monotonic_ns':row['first_chunk_monotonic'],'first_client_text_chunk_at_utc':row['first_chunk_at']}}
        return self.page(params,'observability.spans',params['scope'],'span_details',where,bindings,view)

    def context(self,params):
        scope={'kind':'turn','id':params['turn_id']}
        where,bindings=self.where(scope,'d')
        def view(row):
            data=json.loads(row['metadata_json'])
            return {'snapshot':json.loads(row['snapshot_ref']),'tokens':data.get('after_tokens'),
                'capture_mode':data.get('capture_mode','metadata'),'artifact_refs':[],
                'version':row['version'],'reason':row['reason'],'metadata':data}
        table="(SELECT c.*,c.rowid AS rowid,NULL AS run_id FROM context_snapshots c)"
        return self.page(params,'observability.context',scope,table,where,bindings,view)

    async def evidence(self,params):
        scope=params['scope']
        where,bindings=self.where(scope,'d')
        # Read one page before bounded asynchronous workspace observations.
        key={'collection':'observability.evidence','scope':scope}
        after=self.events.decode_cursor(params['cursor'],key) if params.get('cursor') else 0
        limit=params.get('limit',100)
        rows=self.store.connection.execute('SELECT d.rowid AS position,d.* FROM (SELECT e.*,e.rowid AS rowid,r.turn_id,s.workspace_id,t.session_id,NULL AS run_id FROM evidence_details e '
            'JOIN evidence_refs r ON r.id=e.id JOIN turns t ON t.id=r.turn_id JOIN sessions s ON s.id=t.session_id) d '
            'WHERE ('+where+') AND d.rowid>? ORDER BY d.rowid LIMIT ?',(*bindings,after,limit+1)).fetchall()
        observations={}
        items=[]
        size=0
        for row in rows[:limit]:
            metadata=json.loads(row['metadata_json'])
            validity,reason=row['validity'],row['reason']
            original=metadata.get('evidence_metadata',{}).get('workspace_observation')
            if original:
                workspace=row['workspace_id']
                if workspace not in observations:
                    observations[workspace]=await self.service.evidence_observation(workspace)
                current=observations[workspace]
                def content(value): return {'content_revision':value['revision'],'sha256':value['sha256'],'complete':value['complete']}
                metadata['workspace_comparison']={'observed':content(original),'current':content(current),'environment_state':'unverified_current'}
                if validity=='current' and not current['complete']:
                    validity,reason='missing','current_workspace_observation_incomplete'
                elif validity=='current' and current['sha256']!=original['sha256']:
                    validity,reason='stale','workspace_content_changed_since_verification'
            item={'evidence_id':row['id'],'kind':'internal_verification','validity':validity,'artifact_refs':[],
                'reason':reason,'metadata':metadata,'occurred_at_utc':row['created_at']}
            length=len(json.dumps(item,ensure_ascii=False).encode('utf-8'))
            if items and size+length>196608:
                break
            items.append(item)
            size+=length
        return {'items':items,'next_cursor':self.events.cursor(key,rows[len(items)-1]['position']) if len(rows)>len(items) else None,'history_gap':False}

    def usage(self,params):
        where,bindings=self.where(params['scope'],'d')
        rows=self.store.connection.execute('SELECT u.*,d.pricing_snapshot,m.role FROM usage_ledger u JOIN request_details d ON d.request_id=u.request_id '
            'JOIN model_requests m ON m.id=u.request_id WHERE '+where,bindings)
        total_input=total_output=0
        unknown=0
        count=0
        quality_counts={'actual':0,'estimated':0,'unknown':0}
        with localcontext() as decimal_context:
            decimal_context.prec=80
            known=estimated=Decimal(0)
            snapshots=set()
            roles={}
            for row in rows:
                count+=1
                normalized=json.loads(row['normalized_usage']) if row['normalized_usage'] else None
                if normalized:
                    total_input+=normalized.get('input_tokens') or 0
                    total_output+=normalized.get('output_tokens') or 0
                role=roles.setdefault(row['role'],{'requests':0,'known_cost_decimal':'0','estimated_cost_decimal':'0','unknown_requests':0})
                role['requests']+=1
                quality_counts[row['quality']]+=1
                if row['cost'] is None:
                    unknown+=1
                    role['unknown_requests']+=1
                else:
                    amount=Decimal(row['cost'])
                    key='estimated_cost_decimal' if row['cost_quality']=='estimated' else 'known_cost_decimal'
                    role[key]=decimal_text(Decimal(role[key])+amount)
                    if row['cost_quality']=='estimated': estimated+=amount
                    else: known+=amount
                if row['pricing_snapshot']: snapshots.add(row['pricing_snapshot'])
        result={'input_tokens':total_input if quality_counts['unknown']==0 and total_input<=SAFE_INTEGER else None,
            'output_tokens':total_output if quality_counts['unknown']==0 and total_output<=SAFE_INTEGER else None,
            'cost_decimal':decimal_text(known+estimated) if count and unknown==0 else None,'currency':'USD','unknown_requests':unknown,
            'pricing_snapshot':json.loads(next(iter(snapshots))) if len(snapshots)==1 else None,
            'known_cost_decimal':decimal_text(known),'estimated_cost_decimal':decimal_text(estimated),'request_count':count,
            'usage_quality_counts':quality_counts,'roles':roles,'hard_usd_bound_available':False,'exporter':self.service.exporter.status()}
        return result

    def output(self,params):
        from forge.observability.tool_outputs import output
        return output(self,params)

    def timings(self,params):
        self.check_scope({'kind':'turn','id':params['turn_id']})
        row=self.store.connection.execute('SELECT d.start_monotonic,d.end_monotonic,t.owner_epoch,l.owner_epoch FROM turn_traces t LEFT JOIN span_details d '
            'ON d.trace_id=t.trace_id AND d.span_id=t.span_id LEFT JOIN turn_lifecycle l ON l.turn_id=t.turn_id WHERE t.turn_id=?',(params['turn_id'],)).fetchone()
        duration=str(max(0,int(row[1])-int(row[0]))) if row and row[0] and row[1] and row[2]==row[3] else None
        wall=self.store.connection.execute("SELECT json_extract(body_json,'$.attributes.amount_decimal') FROM events WHERE json_extract(body_json,'$.turn_id')=? "
            "AND json_extract(body_json,'$.event_type')='budget.consumed' AND json_extract(body_json,'$.attributes.dimension')='wall_seconds' "
            "AND json_extract(body_json,'$.origin')='trusted_engine' ORDER BY store_seq DESC LIMIT 1",(params['turn_id'],)).fetchone()
        return {'turn_id':params['turn_id'],'engine_duration_nanoseconds':duration,'harness_wall_seconds':wall[0] if wall else None,
            'clock_source':'local_monotonic','server_first_token_time':None}

    def events_page(self,params):
        scope=params['scope'];where,bindings=self.where(scope,'d')
        key={'scope':scope,'event_types':params['event_types']} if params.get('event_types') else scope
        after=self.events.decode_cursor(params['cursor'],key) if params.get('cursor') else 0
        if params.get('event_types'):
            where+=" AND json_extract(d.body_json,'$.event_type') IN ("+','.join('?' for _ in params['event_types'])+')'
            bindings=(*bindings,*params['event_types'])
        high=self.store.connection.execute('SELECT COALESCE(MAX(store_seq),0),COALESCE(MIN(store_seq),1) FROM events').fetchone()
        if after>high[0] or after and after<high[1]-1:
            raise ContractError('Cursor is outside retained events',kind='INVALID_CURSOR',code=-32010)
        table="(SELECT e.*,json_extract(body_json,'$.turn_id') AS turn_id,json_extract(body_json,'$.run_id') AS run_id,json_extract(body_json,'$.workspace_id') AS workspace_id,json_extract(body_json,'$.session_id') AS session_id FROM events e)"
        rows=self.store.connection.execute('SELECT d.* FROM '+table+' d WHERE ('+where+') AND store_seq>? ORDER BY store_seq LIMIT ?',(*bindings,after,params.get('limit',100)+1)).fetchall()
        items=[];size=0
        for row in rows[:params.get('limit',100)]:
            item={**json.loads(row['body_json']),'store_seq':str(row['store_seq'])};length=len(json.dumps(item,ensure_ascii=False).encode())
            if items and size+length>196608: break
            items.append(item);size+=length
        return {'items':items,'next_cursor':self.events.cursor(key,int(items[-1]['store_seq'])) if items and len(rows)>len(items) else None,
            'history_gap':high[1]>1 or self.events.ephemeral_key}
