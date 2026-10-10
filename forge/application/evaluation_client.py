"""Profile-owned experimental drafts and bounded views over the real evaluation service."""
from copy import deepcopy
from hashlib import sha256
import json

from benchmark.adapters.harbor import export_runspec
from forge.file_selection import selected_file
from benchmark.core.metrics import compare_specs
from benchmark.core.spec import REFERENCES, freeze_spec, resolve_snapshot
from forge.application.models import ContractError, canonical_hash, validate, strict_loads
from forge.engine.persistence import encoded, new_id, utc_now
from forge.observability.export_queue import redact
import re


def secret_values(values,secrets=()):
    # A source index named secrets.py still contains file hashes, not credentials.
    def index_view(value):
        if isinstance(value,dict):
            return {key:list(item.values()) if key=='files' and isinstance(item,dict) and
                all(isinstance(digest,str) and re.fullmatch('[0-9a-f]{64}',digest) for digest in item.values()) else index_view(item)
                for key,item in value.items()}
        if isinstance(value,list):return [index_view(item) for item in value]
        return value
    view=index_view(values)
    return redact(view,secrets)!=view


def configuration_issues(store, profile, spec):
    issues=[]
    for group,key,_ in REFERENCES.values():
        rows=store.connection.execute('SELECT profile_id,origin FROM evaluation_snapshot_origins WHERE snapshot_id=?',
            (spec[group][key]['snapshot_id'],)).fetchall()
        owned=[r for r in rows if r['profile_id']==profile]
        if rows and not owned:
            raise ContractError('Configuration belongs to another profile',kind='NOT_FOUND',code=-32010)
        if owned and owned[0]['origin']=='imported_unverified' and not issues:
            issues.append({'task_id':None,'kind':'unverified_configuration','message':'Imported configuration requires trusted source/environment reconciliation before execution.'})
    return issues


class EvaluationClient:
    def __init__(self,methods):
        self.methods,self.evaluations,self.store=methods,methods.evaluations,methods.store
        self.profile=methods.service.profile_id
        self.target_observations={}

    def _spec(self,run_id):
        imported=self.store.connection.execute('SELECT spec_json FROM imported_runs WHERE id=? AND profile_id=?',(run_id,self.profile)).fetchone()
        if imported:return json.loads(imported[0]),'imported_unverified'
        run=self.evaluations.run(run_id)
        spec=json.loads(run['spec_json'])
        return spec,'imported_unverified' if configuration_issues(self.store,self.profile,spec) else 'trusted_configuration'

    def _template(self,identity):
        if identity.startswith('run-'):
            spec,origin=self._spec(identity)
            if self.store.connection.execute('SELECT 1 FROM imported_runs WHERE id=?',(identity,)).fetchone():
                raise ContractError('Imported result has no resolved configuration snapshots; import its separate plan',kind='NOT_FOUND',code=-32010)
        else:
            row=self.store.connection.execute('SELECT * FROM evaluation_templates WHERE id=? AND profile_id=?',(identity,self.profile)).fetchone()
            if not row:raise ContractError('Template not found in this profile',kind='NOT_FOUND',code=-32010)
            spec,origin=json.loads(row['spec_json']),row['origin']
        values={name:resolve_snapshot(self.store,spec[group][key],schema) for name,(group,key,schema) in REFERENCES.items()}
        return spec,values,origin

    def templates(self,params):
        rows=self.store.connection.execute("SELECT id,spec_json,origin FROM evaluation_templates WHERE profile_id=? ORDER BY rowid DESC LIMIT 20",(self.profile,)).fetchall()
        items=[]
        for row in rows:
            spec=json.loads(row['spec_json']);items.append({'template_id':row['id'],'dataset':spec['dataset']['name'],
                'revision':spec['dataset']['revision'],'task_count':len(spec['dataset']['task_ids']),'source_commit':spec['source']['commit'],'origin':row['origin']})
        for row in self.store.connection.execute('SELECT r.id,r.spec_json FROM runs r JOIN run_details d ON d.run_id=r.id WHERE d.profile_id=? ORDER BY r.rowid DESC LIMIT 20',(self.profile,)):
            spec,origin=self._spec(row['id']);items.append({'template_id':row['id'],'dataset':spec['dataset']['name'],
                'revision':spec['dataset']['revision'],'task_count':len(spec['dataset']['task_ids']),'source_commit':spec['source']['commit'],'origin':origin})
        return {'items':items,'history_gap':False,'requires_configuration_import':not items}

    def template(self,params):
        spec,values,origin=self._template(params['template_id'])
        h,p=values['harness'],values['model_parameters']
        defaults={**spec['budget'],'task_ids':spec['dataset']['task_ids'],'target_platform':spec['execution']['target_platform'],
            'connection_id':spec['model']['connection_id'] if spec['model_mode']=='live' else None,
            'max_delivery_repairs':h['max_delivery_repairs'],'max_context_tokens':h['max_context_tokens'],
            'max_output_tokens':p['max_output_tokens'],'temperature':p['temperature'],'top_p':p['top_p'],'reasoning_effort':p['reasoning_effort'],
            'compaction_enabled':h['compaction_enabled'],'explore_enabled':h['explore_enabled'],
            'repeats':spec['protocol']['repeats'],'max_infrastructure_attempts':spec['protocol']['max_infrastructure_attempts'],
            'infrastructure_retry_categories':spec['protocol'].get('infrastructure_retry_categories',[]),
            'feedback':spec['protocol']['feedback'],'network_mode':values['network_cache']['network_mode'],
            'allowed_domains':values['network_cache']['allowed_domains'],'cache_mode':values['network_cache']['cache_mode']}
        return {'spec':spec,'defaults':defaults,'configuration_origin':origin}

    def _save(self,spec,values,origin):
        for name,(group,key,_) in REFERENCES.items():
            ref=self.store._configuration_snapshot(values[name],canonical_hash(values[name]));spec[group][key]=ref
            self.store.connection.execute('INSERT OR IGNORE INTO evaluation_snapshot_origins VALUES(?,?,?)',(ref['snapshot_id'],self.profile,origin))

    def draft(self,params):
        def build():
            spec,values,origin=self._template(params['template_id']);spec,values=deepcopy(spec),deepcopy(values)
            c=params['choices'];task_ids=c['task_ids']
            if not set(task_ids)<=set(spec['dataset']['task_ids']):raise ContractError('Task is absent from the frozen dataset')
            spec['dataset']['task_ids']=task_ids;spec['dataset']['task_revisions']={k:spec['dataset']['task_revisions'][k] for k in task_ids}
            if c['connection_id']:
                row=self.methods.connections.row(c['connection_id']);metadata=json.loads(row['configuration_json'])
                spec['model'].update(connection_id=row['id'],connection_revision=row['revision'],provider=metadata['provider'],requested_model=metadata['model_id'])
                spec['model_mode']='live'
                if 'rates' not in values['pricing']:
                    values['pricing']={'revision':'not-provided','currency':c['currency'],'source':'No explicit PriceBook provided for the selected model','rates':[]}
            elif spec['model_mode']=='live':raise ContractError('Select an actual model connection',kind='CONNECTION_UNAVAILABLE',code=-32010)
            spec['budget']={key:c[key] for key in spec['budget']}
            values['model_parameters']={k:c[k] for k in ('temperature','top_p','reasoning_effort','max_output_tokens')}
            values['harness'].update(max_delivery_repairs=c['max_delivery_repairs'],max_context_tokens=c['max_context_tokens'],
                compaction_enabled=c['compaction_enabled'],explore_enabled=c['explore_enabled'],trusted_extensions_enabled=False,
                parent_budget={'max_model_calls':c['max_model_requests_per_attempt'],'max_tool_calls':c['max_tool_calls_per_attempt'],'wall_seconds':c['attempt_wall_seconds']})
            spec['harness']['max_delivery_repairs']=c['max_delivery_repairs']
            spec['protocol'].update(repeats=c['repeats'],max_infrastructure_attempts=c['max_infrastructure_attempts'],
                infrastructure_retry_categories=c['infrastructure_retry_categories'],feedback=c['feedback'])
            if c['target_platform']!=spec['execution']['target_platform']:
                # A request for another OS is not a capability measurement.
                values['environment']={'platform':c['target_platform'],'os_build':'unverified requested target','architecture':'x64',
                    'filesystem':'unverified','toolchains':[],'backend_version':'unavailable'}
                values['capabilities'].update(platform='unsupported',backend='unavailable',backend_version='unavailable',readiness='unavailable',
                    read_isolation='unavailable',write_isolation=False,direct_network_isolation=False,dns_isolation=False,socket_isolation=False,
                    process_cleanup=False,resource_enforcement={'memory':'unavailable','disk':'unavailable','pids':'unavailable'},
                    issues=['Requested target has no verified native capability; Engine metadata preflight only'],
                    measured_at_utc=self.target_observations.setdefault(c['target_platform'],utc_now()))
            spec['execution']['target_platform']=c['target_platform']
            if c['cache_mode']=='shared_read_only' and values['network_cache']['cache_snapshot'] is None:
                raise ContractError('Shared cache needs an explicit immutable snapshot')
            values['network_cache'].update(network_mode=c['network_mode'],allowed_domains=c['allowed_domains'],cache_mode=c['cache_mode'])
            values['policy']['network'].update(mode=c['network_mode'],allowed_domains=c['allowed_domains'])
            values['policy']['limits']['wall_time_seconds']=c['attempt_wall_seconds']
            self._save(spec,values,origin)
            spec,_,digest=freeze_spec(self.store,spec)
            return {'spec':spec,'spec_hash':digest,'configuration_origin':origin,'planned_trials':len(task_ids)*c['repeats']}
        return self.evaluations._mutate('evaluation.draft',params,build)

    def list_runs(self,params):
        key={'collection':'evaluation.list','profile':self.profile}
        after=self.methods.events.decode_cursor(params['cursor'],key) if params.get('cursor') else 0
        limit=params.get('limit',100)
        rows=self.store.connection.execute("SELECT * FROM (SELECT r.rowid*2 AS position,r.id,r.spec_json,r.state,'local' AS origin FROM runs r JOIN run_details d ON d.run_id=r.id WHERE d.profile_id=? UNION ALL SELECT rowid*2+1 AS position,id,spec_json,json_extract(report_json,'$.state') AS state,'imported_unverified' AS origin FROM imported_runs WHERE profile_id=?) WHERE position>? ORDER BY position LIMIT ?",(self.profile,self.profile,after,limit+1)).fetchall()
        items=[]
        for row in rows[:limit]:
            spec=json.loads(row['spec_json']);items.append({'run_id':row['id'],'state':row['state'],'origin':row['origin'],
                'dataset':spec['dataset']['name'],'revision':spec['dataset']['revision'],'model_mode':spec['model_mode'],
                'planned_trials':len(spec['dataset']['task_ids'])*spec['protocol']['repeats']})
        return {'items':items,'next_cursor':self.methods.events.cursor(key,rows[limit-1]['position']) if len(rows)>limit else None,'history_gap':False}

    def snapshot(self,params):
        spec,configuration_origin=self._spec(params['run_id']);data=deepcopy(self.evaluations.report_data(params['run_id']))
        origin=data.get('origin','local');key={'collection':'evaluation.snapshot','profile':self.profile,'run_id':params['run_id'],'task_id':params.get('task_id')}
        after=self.methods.events.decode_cursor(params['cursor'],key) if params.get('cursor') else 0
        trials=[t for t in data['trials'] if not params.get('task_id') or t['task_id']==params['task_id']]
        if params.get('task_id') and params['task_id'] not in spec['dataset']['task_ids']:raise ContractError('Task does not belong to this run')
        limit=params.get('limit',20);page=trials[after:after+limit];ids={t['id'] for t in page}
        attempts=[a for a in data['attempts'] if a['trial_id'] in ids]
        # Only explicit result fields, never runner payloads or raw model/tool output.
        keys=('id','trial_id','attempt_no','execution_state','error_origin','cleanup_state','agent_outcome','grade_state','grade_result','grade_reward','trace_complete','trace_id','span_id')
        views=[{k:a.get(k) for k in keys} for a in attempts]
        missing=set(data['missing_evidence'])
        if origin=='imported_unverified':
            for original,local in data.get('artifact_mapping',{}).items():
                try:self.store.read_artifact(local)
                except (ContractError,OSError):missing.add(original)
            compatible=False;issues=[{'task_id':None,'kind':'imported_unverified','message':'Imported result is read-only; its origin and grading claims are not trusted local facts.'}]
        else:
            try:
                _,values,_=freeze_spec(self.store,spec);issues=self.evaluations.compatibility(spec,values);compatible=not issues
            except ContractError as error:
                compatible=False;issues=[{'task_id':None,'kind':error.kind,'message':str(error)[:1024]}]
        result={'run_id':params['run_id'],'spec':spec,'spec_hash':data['spec_hash'],'state':data['state'],'origin':origin,
            'configuration_origin':configuration_origin,'read_only':origin=='imported_unverified','compatible':compatible,'issues':issues,
            'metrics':data['metrics'],'trials':page,'attempts':views,'missing_evidence':sorted(missing)[:100],
            'missing_evidence_count':len(missing),'next_cursor':self.methods.events.cursor(key,after+len(page)) if after+len(page)<len(trials) else None,
            'history_gap':False,'score_authority':data['score_authority']}
        if len(encoded(result).encode())>900000:raise ContractError('Snapshot exceeds bounded frame; reduce page size',kind='ARTIFACT_LIMIT',code=-32010)
        return result

    def comparison(self,params):
        specs=[];unverified=[]
        for i,identity in enumerate(params['run_ids']):
            spec,origin=self._spec(identity);specs.append(spec)
            if origin=='imported_unverified':unverified.append(f'run[{i}].unverified_origin')
        result=compare_specs(specs)
        if unverified:result={'comparable':False,'differences':(result['differences']+unverified)[:100]}
        return {**result,'uncertainty':'No statistical improvement inferred; same-task repeated trials require task-clustered analysis.'}

    def import_plan(self,params):
        def accept():
            with selected_file(params['path']) as source:raw=source.read(1048577)
            if len(raw)>1048576:raise ContractError('Configuration import exceeds 1 MiB',kind='ARTIFACT_LIMIT',code=-32010)
            document=strict_loads(raw)
            required={'schema_version','spec','spec_hash','resolved_snapshots','execution_label','read_only_plan','required_environment'}
            if set(document)-required-{'configuration_origin'} or not required<=set(document) or document['schema_version']!='forge.eval.plan-export.v1' or document['read_only_plan'] is not True:
                raise ContractError('Unsupported read-only configuration document')
            spec=deepcopy(validate('run-spec',document['spec']));values=document['resolved_snapshots']
            if not isinstance(values,dict) or set(values)!=set(REFERENCES) or document['spec_hash']!=canonical_hash(spec):raise ContractError('Configuration hash or snapshot index disagrees')
            if secret_values(values):raise ContractError('Configuration contains secret-bearing fields; remove them before import')
            for name,(group,key,schema) in REFERENCES.items():
                if canonical_hash(values[name])!=spec[group][key]['sha256']:raise ContractError('Resolved snapshot hash disagrees')
                if schema:validate(schema,values[name])
            if document['execution_label']!=spec['execution']['target_platform']:raise ContractError('Execution label disagrees')
            self._save(spec,values,'imported_unverified')
            # Imported connection IDs are foreign metadata until the user binds a local connection.
            freeze_spec(self.store,spec,check_connection=False)
            identity=new_id('plan');self.store.connection.execute('INSERT INTO evaluation_templates VALUES(?,?,?,?,?,?)',
                (identity,self.profile,'imported_unverified',encoded(spec),sha256(raw).hexdigest(),utc_now()))
            return {'template_id':identity,'origin':'imported_unverified','source_sha256':sha256(raw).hexdigest()}
        return self.evaluations._mutate('evaluation.import_plan',params,accept)

    def export_plan(self,params):
        spec,origin=self._spec(params['run_id'])
        if self.store.connection.execute('SELECT 1 FROM imported_runs WHERE id=?',(params['run_id'],)).fetchone():raise ContractError('Imported result lacks resolved snapshots; export its original configuration separately')
        _,values,_=self._template(params['run_id'])
        secrets=[self.methods.service.credentials.resolve(spec['model']['connection_id'])]
        if secret_values(values,secrets):raise ContractError('Configuration includes secret-bearing values; export refused')
        data={**export_runspec(spec,values),'configuration_origin':origin}
        row=self.store.publish_artifact(encoded(data).encode(),origin='trusted_engine',classification='metadata',profile_id=self.profile,max_bytes=1048576)
        from forge.application.evaluations import artifact_view
        return {'plan_artifact':artifact_view(row),'configuration_origin':origin,'required_environment':data['required_environment']}
