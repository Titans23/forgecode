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

    def where(self,scope,alias):
        self.events.check_scope(scope)
        if scope['kind']=='all':
            return '1=1',()
        return alias+'.'+scope['kind']+'_id=?',(scope['id'],)

    def page(self,params,name,scope,table,where,bindings,view):
        key={'collection':name,'scope':scope}
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
        def view(row):
            span=self.store.connection.execute('SELECT * FROM spans WHERE trace_id=? AND span_id=?',(row['trace_id'],row['span_id'])).fetchone()
            duration=None
            if row['start_monotonic'] and row['end_monotonic']:
                duration=str(max(0,int(row['end_monotonic'])-int(row['start_monotonic'])))
            return {'trace_id':row['trace_id'],'span_id':row['span_id'],'parent_span_id':span['parent_id'],
                'name':row['name'],'started_at_utc':span['start'],'ended_at_utc':span['end'],'state':row['state'],
                'attributes':json.loads(span['attributes']),
                'metadata':{'mapping':json.loads(span['attributes']),'facts':json.loads(row['metadata_json']),
                    'duration_nanoseconds':duration,'first_client_text_chunk_at_utc':row['first_chunk_at']}}
        return self.page(params,'observability.spans',params['scope'],'span_details',where,bindings,view)

    def context(self,params):
        scope={'kind':'turn','id':params['turn_id']}
        self.events.check_scope(scope)
        def view(row):
            data=json.loads(row['metadata_json'])
            return {'snapshot':json.loads(row['snapshot_ref']),'tokens':data.get('after_tokens'),
                'capture_mode':data.get('capture_mode','metadata'),'artifact_refs':[],
                'version':row['version'],'reason':row['reason'],'metadata':data}
        return self.page(params,'observability.context',scope,'context_snapshots','d.turn_id=?',(params['turn_id'],),view)

    async def evidence(self,params):
        scope=params['scope']
        where,bindings=self.where(scope,'s')
        # Read one page before bounded asynchronous workspace observations.
        key={'collection':'observability.evidence','scope':scope}
        after=self.events.decode_cursor(params['cursor'],key) if params.get('cursor') else 0
        limit=params.get('limit',100)
        rows=self.store.connection.execute('SELECT d.rowid AS position,d.*,r.turn_id,s.workspace_id FROM evidence_details d '
            'JOIN evidence_refs r ON r.id=d.id JOIN turns t ON t.id=r.turn_id JOIN sessions s ON s.id=t.session_id '
            'WHERE ('+(where.replace('s.turn_id','r.turn_id').replace('s.session_id','s.id') if scope['kind']!='run' else '0=1')+
            ') AND d.rowid>? ORDER BY d.rowid LIMIT ?',(*(bindings if scope['kind']!='run' else ()),after,limit+1)).fetchall()
        observations={}
        items=[]
        size=0
        for row in rows[:limit]:
            metadata=json.loads(row['metadata_json'])
            validity,reason=row['validity'],row['reason']
            original=metadata.get('evidence_metadata',{}).get('workspace_observation')
            if validity=='current' and original:
                workspace=row['workspace_id']
                if workspace not in observations:
                    observations[workspace]=await self.service.evidence_observation(workspace)
                current=observations[workspace]
                if not current['complete']:
                    validity,reason='missing','current_workspace_observation_incomplete'
                elif current['sha256']!=original['sha256']:
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
