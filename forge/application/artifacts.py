"""Profile-bound artifact reads and trusted-host result bundle operations."""
import base64
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

from benchmark.core.bundle import MAX_TOTAL, file_identity, safe_name, selected_file, staged_bundle, write_bundle
from benchmark.core.results import CATEGORIES, identity, read_results
from forge.application.models import ContractError, canonical_hash, strict_loads, validate
from forge.engine.persistence import encoded, new_id, sync_directory, utc_now
from forge.observability.export_queue import redact


def directory_identity(path):
    path=Path(os.path.abspath(path))
    for parent in reversed((path,*path.parents)):
        info=parent.lstat()
        if parent.is_symlink() or getattr(info,'st_file_attributes',0)&0x400: raise ContractError('Destination parent contains a link or reparse point')
    if not path.is_dir(): raise ContractError('Destination parent must be a directory')
    info=path.stat()
    return [info.st_dev,info.st_ino]


class ArtifactService:
    def __init__(self,service,evaluations):
        self.service,self.store,self.evaluations=service,service.store,evaluations
        self.grants={}

    def _issue(self,kind,value):
        now=time.monotonic()
        self.grants={k:v for k,v in self.grants.items() if v['expires']>now}
        if len(self.grants)>=128: raise ContractError('File selection grant limit exceeded')
        token=secrets.token_urlsafe(32)
        self.grants[token]={'kind':kind,'profile':self.service.profile_id,'epoch':self.store.epoch,
            'expires':now+600,'value':deepcopy(value)}
        return token

    def grant_source(self,path):
        """Trusted host only. RPC never accepts a filesystem path from Renderer."""
        path=Path(os.path.abspath(path));selected=file_identity(path)
        if selected[2]>MAX_TOTAL: raise ContractError('Selected source exceeds bundle quota')
        return self._issue('source',{'path':str(path),'identity':selected})

    def grant_destination(self,path,*,scope,classification):
        validate('scope',scope)
        if classification not in ('metadata_only','redacted_artifacts'): raise ContractError('Unsupported export privacy')
        self._runs(scope)
        path=Path(os.path.abspath(path));parent=directory_identity(path.parent)
        safe_name(path.name)
        if path.exists() or path.is_symlink(): raise ContractError('Export never overwrites an existing file',kind='STALE_REVISION',code=-32010)
        return self._issue('destination',{'path':str(path),'parent_identity':parent,'scope':scope,'classification':classification})

    def _grant(self,token,kind):
        grant=self.grants.get(token)
        if not grant or grant['kind']!=kind or grant['profile']!=self.service.profile_id or grant['epoch']!=self.store.epoch or grant['expires']<=time.monotonic():
            raise ContractError('File selection grant is invalid or expired',kind='UNAUTHORIZED',code=-32010)
        return grant['value']

    def prepare_import(self,params):
        validate('bundle.prepare_import.request',params)
        return {'source_token':self.grant_source(params['path'])}

    def prepare_export(self,params):
        validate('bundle.prepare_export.request',params)
        return {'destination_token':self.grant_destination(params['path'],scope=params['scope'],classification=params['classification'])}

    def _row(self,artifact_id):
        row=self.store.connection.execute('SELECT a.*,p.media_type,p.redaction_version FROM artifacts a JOIN artifact_profiles p ON p.artifact_id=a.id WHERE a.id=? AND p.profile_id=?',
            (artifact_id,self.service.profile_id)).fetchone()
        if row is None: raise ContractError('Artifact not found in this profile',kind='NOT_FOUND',code=-32010)
        if row['classification'] not in ('metadata','redacted-export','imported-redacted'):
            raise ContractError('Raw private artifact requires a separate controlled export',kind='UNAUTHORIZED',code=-32010)
        return dict(row)

    def _path(self,row):
        path=self.store.data_dir/row['relative_storage_key']
        if path.parent!=self.store.data_dir/'artifacts': raise ContractError('Invalid controlled artifact key')
        return path

    def _read(self,row,offset=0,length=0):
        digest=sha256();size=0;chunk=b''
        with selected_file(self._path(row)) as file:
            while block:=file.read(1048576): digest.update(block);size+=len(block)
            if size!=row['size'] or digest.hexdigest()!=row['sha256']: raise ContractError('Artifact content changed',kind='MANIFEST_MISMATCH',code=-32010)
            file.seek(offset);chunk=file.read(length)
        return chunk

    def describe(self,params):
        validate('artifact.describe.request',params);row=self._row(params['artifact_id'])
        try: self._read(row);available=True
        except (OSError,ContractError): available=False
        return {'artifact_id':row['id'],'sha256':row['sha256'],'size_bytes':row['size'],'media_type':row['media_type'],
            'redaction_version':row['redaction_version'],'available':available}

    def read_chunk(self,params):
        validate('artifact.read_chunk.request',params);row=self._row(params['artifact_id'])
        if params['offset']>row['size']: raise ContractError('Artifact offset exceeds size')
        raw=self._read(row,params['offset'],params['length'])
        return {'artifact_id':row['id'],'offset':params['offset'],'data_base64':base64.b64encode(raw).decode(),
            'eof':params['offset']+len(raw)>=row['size'],'sha256':row['sha256']}

    def _runs(self,scope):
        if scope['kind']=='run': return [self.evaluations.run(scope['id'])]
        if scope['kind']!='all': raise ContractError('Result bundles require a run scope or all evaluation runs')
        return [dict(row) for row in self.store.connection.execute('SELECT r.* FROM runs r JOIN run_details d ON d.run_id=r.id WHERE d.profile_id=? ORDER BY r.rowid',(self.service.profile_id,))]

    def _freeze(self,scope,classification):
        files={};records={name:[] for name in ('trials','attempts','grades','requests','events','annotations','annotation_details')}
        evidence={'classification':classification,'artifacts':[],'missing':[]};tasks=[]
        with self.store.transaction():
            runs=self._runs(scope)
            if not runs: raise ContractError('No evaluation runs in the selected scope',kind='NOT_FOUND',code=-32010)
            for run in runs:
                run_id=run['id'];spec=json.loads(run['spec_json']);tasks.extend(spec['dataset']['task_ids'])
                files[f'runs/{run_id}/spec.json']=encoded(spec).encode()
                files[f'runs/{run_id}/state.json']=encoded({'state':run['state'],'spec_hash':run['spec_hash']}).encode()
                records['trials'].extend(dict(row) for row in self.store.connection.execute('SELECT * FROM trials WHERE run_id=? ORDER BY rowid',(run_id,)))
                records['attempts'].extend(dict(row) for row in self.store.connection.execute('SELECT a.*,d.agent_outcome,d.authoritative_grade_id,d.trace_id,d.span_id FROM attempts a JOIN trials t ON t.id=a.trial_id JOIN attempt_details d ON d.attempt_id=a.id WHERE t.run_id=? ORDER BY a.rowid',(run_id,)))
                records['grades'].extend(dict(row) for row in self.store.connection.execute('SELECT g.* FROM grades g JOIN attempts a ON a.id=g.attempt_id JOIN trials t ON t.id=a.trial_id WHERE t.run_id=? ORDER BY g.rowid',(run_id,)))
                records['requests'].extend({**dict(row),'run_id':run_id} for row in self.store.connection.execute('SELECT m.id,l.cost,l.cost_quality AS quality,l.currency FROM model_requests m JOIN usage_ledger l ON l.request_id=m.id JOIN attempt_requests ar ON ar.request_id=m.id JOIN attempts a ON a.id=ar.attempt_id JOIN trials t ON t.id=a.trial_id WHERE t.run_id=? ORDER BY m.rowid',(run_id,)))
                records['events'].extend(redact({**json.loads(row['body_json']),'store_seq':str(row['store_seq'])},self.store.observation_secrets) for row in self.store.connection.execute("SELECT store_seq,body_json FROM events WHERE json_extract(body_json,'$.run_id')=? ORDER BY store_seq",(run_id,)))
                records['annotations'].extend({**dict(row),'author':redact(row['author'],self.store.observation_secrets)[:128]} for row in self.store.connection.execute('SELECT n.* FROM annotations n JOIN attempts a ON a.id=n.attempt_id JOIN trials t ON t.id=a.trial_id WHERE t.run_id=? ORDER BY n.rowid',(run_id,)))
                records['annotation_details'].extend({'schema_version':'forge.annotation.details.v1',**dict(row),'note':redact(row['note'],self.store.observation_secrets)[:4000]} for row in self.store.connection.execute(
                    'SELECT d.* FROM annotation_details d JOIN annotations n ON n.id=d.annotation_id JOIN attempts a ON a.id=n.attempt_id JOIN trials t ON t.id=a.trial_id WHERE t.run_id=? ORDER BY n.rowid',(run_id,)))

                for row in self.store.connection.execute('SELECT x.* FROM artifacts x JOIN artifact_attempts ar ON ar.artifact_id=x.id JOIN attempts a ON a.id=ar.attempt_id JOIN trials t ON t.id=a.trial_id WHERE t.run_id=? ORDER BY x.rowid',(run_id,)).fetchall():
                    item={'artifact_id':row['id'],'run_id':run_id,'source_sha256':row['sha256'],'export_sha256':None,'status':'omitted_metadata_only'}
                    try: raw=self.store.read_artifact(row['id'])
                    except (OSError,ContractError): item['status']='missing';evidence['missing'].append(row['id'])
                    else:
                        if row['classification']!='metadata': item['status']='omitted_private'
                        elif classification=='redacted_artifacts':
                            try: value=strict_loads(raw,max_bytes=104857600)
                            except ContractError: item['status']='omitted_private'
                            else:
                                copy=encoded(redact(value,self.store.observation_secrets)).encode()
                                files['artifacts/'+row['id']]=copy;item['export_sha256']=sha256(copy).hexdigest();item['status']='redacted_copy'
                    evidence['artifacts'].append(item)
        for name,rows in records.items(): files[name+'.jsonl']=b''.join(encoded(row).encode()+b'\n' for row in rows)
        files['evidence.json']=encoded(evidence).encode()
        return files,tasks

    def _publish_destination(self,selection,content):
        path=Path(selection['path'])
        if directory_identity(path.parent)!=selection['parent_identity']: raise ContractError('Selected destination directory changed',kind='STALE_REVISION',code=-32010)
        if path.exists():
            with selected_file(path) as file:
                digest=sha256()
                while chunk:=file.read(1048576): digest.update(chunk)
            if digest.hexdigest()!=sha256(content).hexdigest(): raise ContractError('Destination conflict; existing bytes were retained',kind='STALE_REVISION',code=-32010)
            return
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.forge-export-',delete=False) as file:
                temporary=Path(file.name);file.write(content);file.flush();os.fsync(file.fileno())
            if directory_identity(path.parent)!=selection['parent_identity']: raise ContractError('Selected destination directory changed')
            # Atomic no-replace publication on both NTFS and Linux. A retry after
            # rename/response loss accepts only the previously frozen exact bytes.
            os.link(temporary,path,follow_symlinks=False);temporary.unlink();sync_directory(path.parent)
        except FileExistsError as error: raise ContractError('Destination was concurrently created',kind='STALE_REVISION',code=-32010) from error
        finally:
            if temporary: temporary.unlink(missing_ok=True)

    def export(self,params):
        validate('bundle.export.request',params)
        existing=self.service._existing_action('bundle.export',params)
        if existing is not None: return {**existing,'reused_existing_action':True}
        intent=self.store.connection.execute('SELECT * FROM bundle_exports WHERE profile_id=? AND client_action_id=?',(self.service.profile_id,params['client_action_id'])).fetchone()
        if intent:
            if intent['params_hash']!=canonical_hash(params): raise ContractError('Export action conflicts with frozen intent',kind='IDEMPOTENCY_CONFLICT',code=-32010)
            result=json.loads(intent['result_json']);selection=json.loads(intent['destination_json']);content=self.store.read_artifact(intent['artifact_id'])
        else:
            selection=self._grant(params['destination_token'],'destination')
            if selection['scope']!=params['scope'] or selection['classification']!=params['classification']: raise ContractError('File grant is bound to a different scope/privacy',kind='UNAUTHORIZED',code=-32010)
            files,tasks=self._freeze(params['scope'],params['classification'])
            with tempfile.TemporaryDirectory(dir=self.store.data_dir,prefix='bundle-export-') as directory:
                path=Path(directory)/'bundle.zip';manifest=write_bundle(path,files,task_ids=tasks)
                # Verify our actual frozen bytes with the same untrusted reader.
                with staged_bundle(path) as (root,index,_): read_results(root,index)
                content=path.read_bytes()
            artifact=self.store.publish_artifact(content,origin='trusted_engine',classification='redacted-export',max_bytes=MAX_TOTAL,
                profile_id=self.service.profile_id,media_type='application/zip')
            result={'manifest':manifest,'artifact':self.describe({'artifact_id':artifact['id']}),'reused_existing_action':False}
            validate('bundle.export.result',result)
            if len(encoded(result).encode())>1048576: raise ContractError('Export manifest exceeds RPC frame; select fewer runs/artifacts',kind='ARTIFACT_LIMIT',code=-32010)
            with self.store.transaction():
                self.store.connection.execute('INSERT INTO bundle_exports VALUES(?,?,?,?,?,?)',(self.service.profile_id,params['client_action_id'],canonical_hash(params),artifact['id'],encoded(result),encoded(selection)))
        self._publish_destination(selection,content)
        with self.store.transaction(): self.service._record_action('bundle.export',params,result)
        self.grants.pop(params['destination_token'],None)
        return result

    def _facts(self,data):
        result=[]
        for run_id,run in data['runs'].items(): result.append(('run',run_id,sha256(encoded(run).encode()).hexdigest()))
        for kind in ('trials','attempts','grades','requests','annotations'):
            for row in data['records'][kind]:
                detail=next((d for d in data['records'].get('annotation_details',[]) if d['annotation_id']==row['id']),None) if kind=='annotations' else None
                result.append((kind,row['id'],sha256(encoded({'record':row,'details':detail} if detail else row).encode()).hexdigest()))
        return result

    def _conflict(self,manifest,source_hash,existing,incoming,source):
        with self.store.transaction():
            artifact_id=self._publish_import_file(source,digest=source_hash,classification='quarantined-bundle',media_type='application/zip')
            self.store.connection.execute('INSERT INTO bundle_conflicts VALUES(?,?,?,?,?,?,?,?)',
                (new_id('conflict'),self.service.profile_id,manifest['bundle_id'],source_hash,existing,incoming,utc_now(),artifact_id))
        raise ContractError('Bundle identity conflicts with imported facts; quarantined without overwrite',kind='EVENT_CONFLICT',code=-32010)

    def _publish_import_file(self,source,*,digest,classification,media_type):
        """Caller owns the publication transaction. Failed publication leaves only
        unreferenced bytes, never half a visible run or a trusted ledger entry."""
        artifact_id=new_id('art');directory=self.store.data_dir/'artifacts'
        directory.mkdir(exist_ok=True,mode=0o700);directory_identity(directory)
        final=directory/artifact_id;actual=sha256();size=0
        with source.open('rb') as input_file,final.open('xb') as output:
            while chunk:=input_file.read(1048576): actual.update(chunk);size+=len(chunk);output.write(chunk)
            output.flush();os.fsync(output.fileno())
        if size>MAX_TOTAL or actual.hexdigest()!=digest: raise ContractError('Staged artifact changed before publication')
        sync_directory(directory)
        self.store.connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?)',(artifact_id,final.relative_to(self.store.data_dir).as_posix(),digest,size,'imported_unverified',classification))
        self.store.connection.execute('INSERT INTO artifact_profiles VALUES(?,?,?,?)',(artifact_id,self.service.profile_id,media_type,'metadata-v1'))
        return artifact_id

    def import_bundle(self,params):
        validate('bundle.import.request',params)
        existing=self.service._existing_action('bundle.import',params)
        if existing is not None: return {**existing,'reused_existing_action':True}
        selection=self._grant(params['source_token'],'source')
        with staged_bundle(selection['path'],staging_parent=self.store.data_dir,expected=selection['identity']) as (root,manifest,digest):
            data=read_results(root,manifest);facts=self._facts(data)
            old=self.store.connection.execute('SELECT * FROM imported_bundles WHERE profile_id=? AND bundle_id=?',(self.service.profile_id,manifest['bundle_id'])).fetchone()
            if old and old['source_hash']!=digest: self._conflict(manifest,digest,old['source_hash'],digest,root.parent/'source.zip')
            for kind,source_id,value in facts:
                previous=self.store.connection.execute('SELECT hash FROM imported_facts WHERE profile_id=? AND kind=? AND source_id=?',(self.service.profile_id,kind,source_id)).fetchone()
                if previous and previous[0]!=value: self._conflict(manifest,digest,previous[0],value,root.parent/'source.zip')
            run_ids=[];reused=bool(old)
            # All validation/conflict checks completed before any result is published.
            with self.store.transaction():
                raw_artifact_id=old['raw_artifact_id'] if old else self._publish_import_file(root.parent/'source.zip',digest=digest,classification='private-bundle',media_type='application/zip')
                for report in data['reports']:
                    previous=self.store.connection.execute('SELECT id FROM imported_runs WHERE profile_id=? AND source_run_id=?',(self.service.profile_id,report['run_id'])).fetchone()
                    if previous: run_ids.append(previous[0]);reused=True;continue
                    run_id=new_id('run');source_id=report['run_id'];mapping={}
                    for item in data['evidence']['artifacts']:
                        if item['run_id']!=source_id or item['status']!='redacted_copy': continue
                        artifact_id=self._publish_import_file(root/'artifacts'/item['artifact_id'],digest=item['export_sha256'],classification='imported-redacted',media_type='application/json')
                        mapping[item['artifact_id']]=artifact_id
                    imported={**report,'run_id':run_id,'source_run_id':source_id,'artifact_mapping':mapping}
                    self.store.connection.execute('INSERT INTO imported_runs VALUES(?,?,?,?,?)',(run_id,self.service.profile_id,source_id,encoded(data['runs'][source_id]['spec']),encoded(imported)))
                    run_ids.append(run_id)
                for kind,source_id,value in facts: self.store.connection.execute('INSERT OR IGNORE INTO imported_facts VALUES(?,?,?,?)',(self.service.profile_id,kind,source_id,value))
                result={'bundle_id':manifest['bundle_id'],'origin':'imported_unverified','run_ids':run_ids,'reused_existing_action':reused}
                validate('bundle.import.result',result)
                self.store.connection.execute('INSERT OR IGNORE INTO imported_bundles VALUES(?,?,?,?,?,?)',(self.service.profile_id,manifest['bundle_id'],digest,encoded(manifest),encoded(run_ids),raw_artifact_id))
                self.service._record_action('bundle.import',params,result)
        self.grants.pop(params['source_token'],None)
        return result

    def imported_report(self,run_id):
        row=self.store.connection.execute('SELECT report_json FROM imported_runs WHERE id=? AND profile_id=?',(run_id,self.service.profile_id)).fetchone()
        if row is None: raise ContractError('Imported run not found in this profile',kind='NOT_FOUND',code=-32010)
        return json.loads(row[0])

    def annotate(self,attempt_id,*,author,category,evidence_refs):
        """Retain the trusted host API; new labels also record time and history."""
        row=self.store.connection.execute('SELECT t.run_id FROM attempts a JOIN trials t ON t.id=a.trial_id WHERE a.id=?',(attempt_id,)).fetchone()
        if row is None:raise ContractError('Attempt not found',kind='NOT_FOUND',code=-32010)
        target={'run_id':row[0],'attempt_id':attempt_id}
        current=self.annotations.get(target)
        return self.annotations.annotate({'client_action_id':new_id('act'),**target,'expected_head':current['annotation_head'],
            'author':author,'category':category,'evidence_refs':evidence_refs,'note':''})['annotation_id']
