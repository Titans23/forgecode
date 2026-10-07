"""Human failure history and evidence-bound regression candidates over actual runs."""
from collections import Counter
from copy import deepcopy
import json

from forge.application.models import ContractError, canonical_hash, validate
from forge.engine.persistence import encoded, new_id, utc_now
from forge.observability.export_queue import redact

TERMINAL = {'finished', 'cancelled', 'error', 'blocked'}
ATTEMPT_KEYS = ('id','trial_id','attempt_no','execution_state','error_origin','cleanup_state','agent_outcome',
                'grade_state','grade_result','grade_reward','trace_complete','trace_id','span_id')


def failure(attempt):
    return attempt['execution_state'] in TERMINAL and (
        attempt.get('grade_result') != 'pass' or attempt['execution_state'] != 'finished' or attempt['cleanup_state'] != 'clean')


def suggestions(attempt, events):
    """Bounded correlations; no annotation, score, or cause is written."""
    result = []
    def add(rule, category, basis):
        result.append({'rule':rule,'category':category,'authority':'suggestion_only','basis_ids':basis[:20]})
    failures = [e for e in events if e['event_type']=='tool.finished' and e['attributes'].get('result')=='failed']
    counts = Counter((e['attributes'].get('tool_name'),e['attributes'].get('exit_code')) for e in failures)
    repeated = [e['event_id'] for e in failures if counts[(e['attributes'].get('tool_name'),e['attributes'].get('exit_code'))]>1]
    if repeated: add('repeated_tool_error','tool_usage',repeated)
    stale = [e['event_id'] for e in events if e['event_type']=='verification.invalidated']
    if stale: add('stale_evidence','verification',stale)
    if attempt.get('error_origin') in ('environment_setup','runner_unavailable'):
        add('initialization_unavailable','environment',[attempt['id']])
    if attempt['execution_state'] in TERMINAL and attempt['cleanup_state'] != 'clean':
        add('cleanup_unconfirmed','sandbox',[attempt['id']])
    return result


def condition(spec, trial):
    value = deepcopy(spec)
    value.pop('experiment_id',None)
    value['dataset']['task_ids']=[trial['task_id']]
    value['dataset']['task_revisions']={trial['task_id']:trial['task_revision']}
    value['protocol']['repeats']=1
    return value


class AnnotationService:
    def __init__(self, methods):
        self.methods,self.store,self.evaluations = methods,methods.store,methods.evaluations
        self.profile = methods.service.profile_id

    def target(self, run_id, attempt_id):
        spec,configuration_origin = self.methods.evaluation_client._spec(run_id)
        data = self.evaluations.report_data(run_id)
        attempt = next((a for a in data['attempts'] if a['id']==attempt_id),None)
        if attempt is None: raise ContractError('Attempt not found in this run/profile',kind='NOT_FOUND',code=-32010)
        trial = next(t for t in data['trials'] if t['id']==attempt['trial_id'])
        return spec,data,attempt,trial,configuration_origin

    def history(self,run_id,attempt_id,data):
        if data.get('origin')=='imported_unverified':
            original=[{**a,'created_at':next((d['created_at'] for d in data.get('annotation_details',[]) if d['annotation_id']==a['id']),None),
                'note':next((d['note'] for d in data.get('annotation_details',[]) if d['annotation_id']==a['id']),''),
                'origin':'imported_unverified'} for a in data.get('annotations',[]) if a['attempt_id']==attempt_id]
            overlay=[{**dict(r),'origin':'local_human_overlay'} for r in self.store.connection.execute(
                'SELECT * FROM imported_annotation_notes WHERE profile_id=? AND run_id=? AND attempt_id=? ORDER BY rowid',
                (self.profile,run_id,attempt_id))]
            return original+overlay
        return [{**dict(r),'origin':'local_human'} for r in self.store.connection.execute(
            'SELECT n.*,d.created_at,d.note FROM annotations n LEFT JOIN annotation_details d ON d.annotation_id=n.id WHERE n.attempt_id=? ORDER BY n.rowid',
            (attempt_id,))]

    def evidence(self,run_id,attempt,data):
        if data.get('origin')=='imported_unverified':
            # Bundle v1 only binds an evidence index to a run, not an attempt.
            # A human overlay may cite a run artifact; the UI states this scope.
            rows=[]
            for item in data.get('evidence_changes',[]):
                local=data.get('artifact_mapping',{}).get(item['artifact_id'])
                available=False;size=0
                if local:
                    row=self.store.connection.execute('SELECT * FROM artifacts WHERE id=?',(local,)).fetchone()
                    if row:
                        size=row['size']
                        try:self.store.read_artifact(local);available=True
                        except (ContractError,OSError):pass
                rows.append({'artifact_id':item['artifact_id'],'sha256':item['source_sha256'],'size_bytes':size,
                    'media_type':'application/octet-stream','redaction_version':'metadata-v1','available':available})
            return rows
        rows=[]
        for row in self.store.connection.execute('SELECT a.* FROM artifacts a JOIN artifact_attempts x ON x.artifact_id=a.id WHERE x.attempt_id=? ORDER BY a.rowid',(attempt['id'],)):
            try:self.store.read_artifact(row['id']);available=True
            except (ContractError,OSError):available=False
            rows.append({'artifact_id':row['id'],'sha256':row['sha256'],'size_bytes':row['size'],
                'media_type':'application/octet-stream','redaction_version':'metadata-v1','available':available})
        return rows

    def list(self,params):
        validate('failure.list.request',params)
        data=self.evaluations.report_data(params['run_id'])
        key={'collection':'failure.list','profile':self.profile,'run_id':params['run_id'],'category':params.get('category')}
        after=self.methods.events.decode_cursor(params['cursor'],key) if params.get('cursor') else 0
        trials={t['id']:t for t in data['trials']}
        rows=[]
        for attempt in data['attempts']:
            if not failure(attempt):continue
            history=self.history(params['run_id'],attempt['id'],data)
            category=history[-1]['category'] if history else 'unknown'
            if params.get('category') and params['category']!=category:continue
            rows.append({'run_id':params['run_id'],'attempt_id':attempt['id'],'task_id':trials[attempt['trial_id']]['task_id'],
                'execution_state':attempt['execution_state'],'cleanup_state':attempt['cleanup_state'],'grade_result':attempt.get('grade_result'),
                'category':category,'origin':data.get('origin','local')})
        limit=params.get('limit',20);page=rows[after:after+limit]
        return {'items':page,'next_cursor':self.methods.events.cursor(key,after+len(page)) if after+len(page)<len(rows) else None,'history_gap':False}

    def get(self,params):
        validate('failure.get.request',params)
        run_id,attempt_id=params['run_id'],params['attempt_id']
        spec,data,attempt,trial,_=self.target(run_id,attempt_id)
        history=self.history(run_id,attempt_id,data);refs=self.evidence(run_id,attempt,data)
        if data.get('origin')=='imported_unverified':events=[]
        else:
            events=[json.loads(r[0]) for r in self.store.connection.execute(
                "SELECT body_json FROM events WHERE json_extract(body_json,'$.attempt_id')=? ORDER BY store_seq DESC LIMIT 200",(attempt_id,))]
            events=[e for e in events if e['origin'] in ('trusted_engine','trusted_bridge','grader_adapter') and e['run_id']==run_id and e['trial_id']==trial['id']]
        notes=[{'id':a['id'],'author':redact(a['author'],self.store.observation_secrets)[:128],'category':a['category'],'evidence_refs':json.loads(a['evidence_refs']),
            'supersedes':a['supersedes'],'created_at':a.get('created_at'),'note':redact(a.get('note') or '',self.store.observation_secrets)[:4000],
            'origin':a['origin']} for a in history[-20:]]
        candidates=[self.candidate_view(dict(r)) for r in self.store.connection.execute(
            'SELECT * FROM regression_candidates WHERE profile_id=? AND run_id=? AND attempt_id=? ORDER BY rowid DESC LIMIT 20',
            (self.profile,run_id,attempt_id))]
        result = {'run_id':run_id,'task_id':trial['task_id'],'task_revision':trial['task_revision'],'origin':data.get('origin','local'),
            'attempt':{k:attempt.get(k) for k in ATTEMPT_KEYS},'annotations':notes,'annotation_head':history[-1]['id'] if history else None,
            'history_gap':len(history)>20,'evidence':refs[:100],'evidence_count':len(refs),
            'evidence_scope':'run' if data.get('origin')=='imported_unverified' else 'attempt',
            'suggestions':suggestions(attempt,events),'rules_scope':'latest_200_attempt_events',
            'candidates':candidates,'is_failure':failure(attempt)}
        if len(encoded(result).encode())>900000:raise ContractError('Case exceeds bounded RPC frame',kind='ARTIFACT_LIMIT',code=-32010)
        return result

    def _head(self,params,data):
        history=self.history(params['run_id'],params['attempt_id'],data)
        actual=history[-1]['id'] if history else None
        if params['expected_head']!=actual:
            raise ContractError('Annotation changed; reload before editing/saving',kind='STALE_REVISION',code=-32010)
        return actual

    def annotate(self,params):
        def append():
            _,data,attempt,_,_=self.target(params['run_id'],params['attempt_id'])
            head=self._head(params,data)
            refs=self.evidence(params['run_id'],attempt,data)
            if not set(params['evidence_refs']) <= {r['artifact_id'] for r in refs}:
                raise ContractError('Evidence is not owned by this case')
            annotation_id=new_id('annotation');when=utc_now()
            if not params['author'].strip():raise ContractError('Human author must be nonempty')
            author=redact(params['author'].strip(),self.store.observation_secrets)[:128]
            note=redact(params['note'],self.store.observation_secrets)[:4000]
            values=(annotation_id,params['attempt_id'],author,params['category'],encoded(params['evidence_refs']),head)
            if data.get('origin')=='imported_unverified':
                self.store.connection.execute('INSERT INTO imported_annotation_notes VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (annotation_id,self.profile,params['run_id'],params['attempt_id'],author,params['category'],encoded(params['evidence_refs']),head,when,note))
            else:
                self.store.connection.execute('INSERT INTO annotations VALUES(?,?,?,?,?,?)',values)
                self.store.connection.execute('INSERT INTO annotation_details VALUES(?,?,?)',(annotation_id,when,note))
            return {'annotation_id':annotation_id,'reused_existing_action':False}
        return self.evaluations._mutate('failure.annotate',params,append)

    def save_candidate(self,params):
        validate('failure.save_candidate.request',params)
        existing=self.methods.service._existing_action('failure.save_candidate',params)
        if existing is not None:return {**existing,'reused_existing_action':True}
        spec,data,attempt,trial,origin=self.target(params['run_id'],params['attempt_id'])
        if not failure(attempt):raise ContractError('Select a terminal failed or unscored attempt')
        head=self._head(params,data);refs=self.evidence(params['run_id'],attempt,data)
        if len(refs)>100:raise ContractError('Case has more than 100 artifacts; candidate needs an explicit smaller fixture',kind='ARTIFACT_LIMIT',code=-32010)
        plan=condition(spec,trial);plan['experiment_id']=spec['experiment_id']
        validate('run-spec',plan)
        annotation=self.history(params['run_id'],attempt['id'],data)[-1] if head else None
        fixture={'schema_version':'forge.regression.candidate.v1','run_id':params['run_id'],'attempt_id':attempt['id'],
            'annotation_id':head,'task_id':trial['task_id'],'task_revision':trial['task_revision'],
            'configuration_hash':canonical_hash(condition(spec,trial)),'configuration_origin':origin,
            'reproduction_plan':plan,'expected_failure':'independent_grader_fail',
            'observed_grade_result':attempt.get('grade_result'),'evidence':refs,
            'note':redact(annotation.get('note') or '',self.store.observation_secrets) if annotation else '',
            'annotation':{k:annotation.get(k) for k in ('id','author','category','created_at','origin','supersedes')} if annotation else None,
            'status':'saved_pending_reproduction','root_cause_authority':'human_label_only',
            'fixture_kind':'frozen_evaluation_metadata',
            'execution_requirements':'Resolve the frozen source task and configuration snapshots using the existing evaluation executor; obtain current environment and spend authorization separately.'}
        # Whitelisted metadata and hashes only. Paths, raw model/debug and credentials never copied.
        fixture=redact(fixture,self.store.observation_secrets)
        artifact=self.store.publish_artifact(encoded(fixture).encode(),origin='trusted_engine',classification='metadata',
            profile_id=self.profile,max_bytes=1048576)
        def save():
            self._head(params,self.evaluations.report_data(params['run_id']))
            candidate_id=new_id('candidate')
            self.store.connection.execute('INSERT INTO regression_candidates VALUES(?,?,?,?,?,?,?)',
                (candidate_id,self.profile,params['run_id'],attempt['id'],head,artifact['id'],utc_now()))
            return {'candidate_id':candidate_id,'reused_existing_action':False}
        return self.evaluations._mutate('failure.save_candidate',params,save)

    def _candidate(self,identity):
        row=self.store.connection.execute('SELECT * FROM regression_candidates WHERE id=? AND profile_id=?',(identity,self.profile)).fetchone()
        if row is None:raise ContractError('Candidate not found in this profile',kind='NOT_FOUND',code=-32010)
        return dict(row)

    def proof(self,row,run_id=None,attempt_id=None):
        blockers=[]
        try: fixture=json.loads(self.store.read_artifact(row['artifact_id']))
        except (ContractError,OSError):return ['candidate_artifact_missing']
        grade=None
        spec,data,attempt,trial,origin=self.target(row['run_id'],row['attempt_id'])
        refs=self.evidence(row['run_id'],attempt,data)
        if data.get('origin')=='imported_unverified' or origin!='trusted_configuration':blockers.append('unverified_origin')
        if not refs or any(not r['available'] for r in refs):blockers.append('original_evidence_missing')
        if canonical_hash(condition(spec,trial))!=fixture['configuration_hash'] or canonical_hash(condition(fixture['reproduction_plan'],trial))!=fixture['configuration_hash']:blockers.append('candidate_configuration_redacted_or_changed')
        if attempt.get('grade_result')!='fail' or attempt.get('grade_state')!='graded':blockers.append('no_original_independent_failure')
        if attempt['execution_state']!='finished' or not attempt.get('trace_complete') or attempt['cleanup_state']!='clean':blockers.append('original_trace_or_cleanup_incomplete')
        if data.get('origin')!='imported_unverified':
            grade=self.store.connection.execute('SELECT * FROM grades WHERE id=?',(attempt.get('authoritative_grade_id'),)).fetchone()
            if not grade or grade['artifact_hash'] not in {r['sha256'] for r in refs if r['available']}:
                blockers.append('original_grade_evidence_unavailable')
        if run_id is None:return blockers
        other_spec,other_data,other,other_trial,other_origin=self.target(run_id,attempt_id)
        if other_data.get('origin')=='imported_unverified' or other_origin!='trusted_configuration':blockers.append('reproduction_origin_unverified')
        if other['id']==attempt['id'] or other.get('trace_id')==attempt.get('trace_id'):blockers.append('same_execution')
        if canonical_hash(condition(other_spec,other_trial))!=fixture['configuration_hash']:blockers.append('different_frozen_conditions')
        if other.get('grade_result')!='fail' or other.get('grade_state')!='graded':blockers.append('independent_failure_not_reproduced')
        if other['execution_state']!='finished' or other['cleanup_state']!='clean' or not other.get('trace_complete'):blockers.append('reproduction_trace_or_cleanup_incomplete')
        other_refs=self.evidence(run_id,other,other_data)
        if not other_refs or any(not r['available'] for r in other_refs):blockers.append('reproduction_evidence_missing')
        if other_data.get('origin')!='imported_unverified':
            detail=self.store.connection.execute('SELECT started_at FROM attempt_details WHERE attempt_id=?',(other['id'],)).fetchone()
            if not detail or not detail[0] or detail[0]<row['created_at']:blockers.append('execution_predates_candidate')
            other_grade=self.store.connection.execute('SELECT * FROM grades WHERE id=?',(other.get('authoritative_grade_id'),)).fetchone()
            if not other_grade or other_grade['artifact_hash'] not in {r['sha256'] for r in other_refs if r['available']}:
                blockers.append('reproduction_grade_evidence_unavailable')
            if grade and other_grade and grade['grader_hash']!=other_grade['grader_hash']:blockers.append('different_actual_grader')
        return list(dict.fromkeys(blockers))

    def candidate_view(self,row):
        artifact=self.methods.artifacts.describe({'artifact_id':row['artifact_id']})
        check=self.store.connection.execute('SELECT * FROM reproduction_checks WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1',(row['id'],)).fetchone()
        blockers=self.proof(row,check['run_id'] if check else None,check['attempt_id'] if check else None)
        return {'candidate_id':row['id'],'fixture':artifact,'created_at':row['created_at'],
            'status':'blocked' if blockers else 'reproduced' if check else 'saved_pending_reproduction',
            'blockers':blockers,'reproduction_run_id':check['run_id'] if check else None,
            'reproduction_attempt_id':check['attempt_id'] if check else None,
            'authority':'independent_grader_failure_only; human root cause is not proven'}

    def candidate(self,params):
        validate('failure.candidate.request',params)
        return self.candidate_view(self._candidate(params['candidate_id']))

    def check_reproduction(self,params):
        def check():
            row=self._candidate(params['candidate_id'])
            blockers=self.proof(row,params['run_id'],params['attempt_id'])
            status='blocked' if blockers else 'reproduced'
            self.store.connection.execute('INSERT INTO reproduction_checks VALUES(?,?,?,?,?,?,?)',
                (new_id('reproduction'),row['id'],params['run_id'],params['attempt_id'],utc_now(),status,encoded(blockers)))
            return {'candidate_id':row['id'],'observed_status':status,'blockers':blockers,'reused_existing_action':False}
        return self.evaluations._mutate('failure.check_reproduction',params,check)
