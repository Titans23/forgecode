"""Trusted CLI-only historical import. Original Journals remain readonly evidence."""
from hashlib import sha256
from itertools import islice
import json
import os
from pathlib import Path
import tempfile
from uuid import NAMESPACE_URL, uuid5

from benchmark.core.bundle import selected_file
from forge.application.models import ContractError, canonical_hash
from forge.engine.journal_projection import journal_facts
from forge.engine.persistence import encoded, new_id, sync_directory, utc_now
from forge.sessions.store import SESSION_ID_PATTERN, SessionStore
from forge.storage_paths import private_storage_path


MAX_FILE=104857600
MAX_TOTAL=1073741824


def safe_source(path, root):
    path=Path(os.path.abspath(path))
    if path.resolve(strict=True)!=path or not path.is_relative_to(root):
        raise ContractError('Legacy source contains a redirected or escaping path',kind='POLICY_DENIED',code=-32010)
    if path.stat().st_size>MAX_FILE:
        raise ContractError('Legacy source file exceeds quota',kind='ARTIFACT_LIMIT',code=-32010)
    return path


def read_bytes(path, root):
    path=safe_source(path,root)
    with selected_file(path) as file:
        data=file.read(MAX_FILE+1)
    if len(data)>MAX_FILE:raise ContractError('Legacy source file exceeds quota',kind='ARTIFACT_LIMIT',code=-32010)
    return data


class LegacyImporter:
    def __init__(self, store, *, profile_id):
        self.store,self.profile_id=store,profile_id
        self.backup_root=private_storage_path(store.data_dir)/'legacy-backups'

    def scan(self, project, source):
        project=Path(project).resolve(strict=True)
        source=Path(os.path.abspath(source))
        if not project.is_dir() or source.resolve(strict=True)!=source or not source.is_dir():
            raise ContractError('Legacy project/source must be real directories',kind='POLICY_DENIED',code=-32010)
        paths=list(islice(source.glob('session-*.jsonl'),1001))
        if len(paths)>1000:raise ContractError('Legacy session count exceeds quota',kind='ARTIFACT_LIMIT',code=-32010)
        entries=[];total=0
        reader=SessionStore(project,data_root=self.store.data_dir)
        for path in sorted(paths):
            if not SESSION_ID_PATTERN.fullmatch(path.stem):
                raise ContractError('Legacy filename has an unsupported session identity')
            raw=read_bytes(path,source)
            try:
                records=reader._read_records(path)
                if len(records)>20000:raise ValueError('Journal record quota exceeded')
                referenced={}
                entry_size=len(raw)
                for record in records:
                    ref=record.get('payload_ref')
                    if ref:
                        relative=Path(ref['path'])
                        if relative.is_absolute() or '..' in relative.parts:
                            raise ValueError('Payload path escapes')
                        if relative.as_posix() in referenced:continue
                        target=safe_source(path.parent/relative,source)
                        if total+entry_size+target.stat().st_size>MAX_TOTAL:
                            raise ContractError('Legacy backup exceeds quota',kind='ARTIFACT_LIMIT',code=-32010)
                        payload=read_bytes(target,source)
                        entry_size+=len(payload)
                        if sha256(payload).hexdigest()!=ref['sha256']:raise ValueError('Payload hash changed')
                        referenced[relative.as_posix()]={'relative_path':relative.as_posix(),'size_bytes':len(payload),
                            'sha256':sha256(payload).hexdigest()}
                facts=journal_facts(path,native_id=path.stem,project_root=project)
                if facts['state'] not in ('complete','partial_tail'):raise ValueError('Journal ownership or chain is invalid')
                if read_bytes(path,source)!=raw:raise ValueError('Journal changed during scanning')
                version=records[0].get('schema_version',1)
                if type(version) is not int or not 1<=version<=255:raise ValueError('Unsupported version identity')
            except (ValueError,KeyError,TypeError,OSError,RuntimeError):
                raise ContractError('Legacy Journal or payload is invalid; originals were preserved',kind='POLICY_DENIED',code=-32010) from None
            files=[{'relative_path':path.name,'size_bytes':len(raw),'sha256':sha256(raw).hexdigest()},*referenced.values()]
            total+=sum(item['size_bytes'] for item in files)
            if total>MAX_TOTAL:raise ContractError('Legacy backup exceeds quota',kind='ARTIFACT_LIMIT',code=-32010)
            key=sha256(os.path.normcase(str(path)).encode()).hexdigest()
            identity='legacy-'+str(uuid5(NAMESPACE_URL,'forge:'+self.profile_id+':'+key))
            known={'schema_version','uuid','parent_uuid','session_id','sequence','timestamp','type','turn_id','payload','payload_ref','tool_call_id'}
            unknown=sorted(set().union(*(set(record)-known for record in records)))
            entries.append({'legacy_id':identity,'native_session_id':path.stem,'source_key':key,'source_path':str(path),
                'source_sha256':sha256(raw).hexdigest(),'native_schema':version,'record_count':len(records),
                'state':facts['state'],'unknown_record_fields':unknown,'files':files})
        if not entries:raise ContractError('No native legacy sessions were found',kind='NOT_FOUND',code=-32010)
        plan={'schema_version':'forge.legacy.preview.v1','profile_id':self.profile_id,'project':str(project),
            'source_directory':str(source),'entries':entries,'total_size_bytes':total}
        return {**plan,'sha256':canonical_hash(plan)}

    def _backup(self, plan, *, create=False):
        body={key:value for key,value in plan.items() if key!='sha256'}
        if canonical_hash(body)!=plan.get('sha256') or plan.get('profile_id')!=self.profile_id:
            raise ContractError('Legacy backup manifest identity changed',kind='MANIFEST_MISMATCH',code=-32010)
        base=self.backup_root;base.mkdir(exist_ok=True,mode=0o700)
        if base.resolve()!=base:raise ContractError('Legacy backup directory redirects storage',kind='POLICY_DENIED',code=-32010)
        destination=base/plan['sha256']
        expected={entry['source_key']+'/'+item['relative_path']:item for entry in plan['entries'] for item in entry['files']}
        if not destination.exists():
            if not create:raise ContractError('Legacy backup is unavailable; history was not recreated',kind='NOT_FOUND',code=-32010)
            staging=Path(tempfile.mkdtemp(prefix='.pending-',dir=base))
            for entry in plan['entries']:
                for item in entry['files']:
                    data=read_bytes(Path(entry['source_path']).parent/item['relative_path'],Path(plan['source_directory']))
                    if sha256(data).hexdigest()!=item['sha256'] or len(data)!=item['size_bytes']:
                        raise ContractError('Legacy source changed during backup',kind='STALE_FILE',code=-32010)
                    target=staging/entry['source_key']/item['relative_path'];target.parent.mkdir(parents=True,exist_ok=True)
                    with target.open('xb') as output:output.write(data);output.flush();os.fsync(output.fileno())
                    sync_directory(target.parent)
            with (staging/'manifest.json').open('x',encoding='utf-8',newline='\n') as output:
                output.write(encoded(plan)+'\n');output.flush();os.fsync(output.fileno())
            sync_directory(staging)
            os.replace(staging,destination)
            sync_directory(base)
        if destination.resolve()!=destination:raise ContractError('Legacy backup identity is unverified',kind='POLICY_DENIED',code=-32010)
        manifest=read_bytes(destination/'manifest.json',destination)
        try:matches=json.loads(manifest)==plan
        except (ValueError,UnicodeError):matches=False
        if not matches:raise ContractError('Legacy backup manifest changed',kind='MANIFEST_MISMATCH',code=-32010)
        for relative,item in expected.items():
            data=read_bytes(destination/relative,destination)
            if sha256(data).hexdigest()!=item['sha256'] or len(data)!=item['size_bytes']:
                raise ContractError('Legacy backup content changed',kind='MANIFEST_MISMATCH',code=-32010)
        return destination.relative_to(base.parent).as_posix()

    def prepare(self,project,source):
        if self.store.read_only:raise ContractError('Readonly Engine cannot prepare migration',kind='INCOMPATIBLE_PROTOCOL',code=-32010)
        plan=self.scan(project,source)
        return {**plan,'backup_key':self._backup(plan,create=True),'requires_confirmation':True}

    def import_confirmed(self,project,source,*,confirmation_sha256):
        preview=self.prepare(project,source)
        if confirmation_sha256!=preview['sha256']:
            raise ContractError('Legacy preview changed or was not confirmed; prepare and inspect again',kind='STALE_REVISION',code=-32010)
        workspace=self.store.register_workspace(project)
        imported=[];reused=[]
        with self.store.transaction():
            for entry in preview['entries']:
                old=self.store.connection.execute('SELECT * FROM legacy_imports WHERE profile_id=? AND source_key=?',
                    (self.profile_id,entry['source_key'])).fetchone()
                if old:
                    if old['source_sha256']!=entry['source_sha256']:
                        raise ContractError('Previously imported source changed; immutable history was preserved',kind='EVENT_CONFLICT',code=-32010)
                    reused.append(old['id']);continue
                timestamp=utc_now()
                self.store.connection.execute('INSERT INTO legacy_imports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (entry['legacy_id'],workspace['id'],self.profile_id,entry['source_key'],entry['source_path'],
                     entry['source_sha256'],entry['native_session_id'],entry['native_schema'],entry['record_count'],
                     preview['backup_key'],entry['state'],timestamp))
                path=self.backup_root.parent/preview['backup_key']/entry['source_key']/Path(entry['source_path']).name
                reader=SessionStore(Path(project),data_root=self.store.data_dir)
                producer=new_id('producer')
                source_id='import:legacy:'+sha256((self.profile_id+':'+entry['source_key']).encode()).hexdigest()
                self.store.connection.execute('INSERT OR IGNORE INTO producers VALUES(?,?)',(source_id,producer))
                for record in reader._read_records(path):
                    payload=reader._payload(record,path)
                    digest=sha256(encoded({'record':record,'payload':payload}).encode()).hexdigest()
                    body=self.store.event_body('journal.projected',producer,record['sequence'],
                        {'native_type':record['type'],'native_uuid':record['uuid'],'legacy_session_id':entry['native_session_id'],'record_hash':digest},
                        workspace_id=workspace['id'])
                    body['origin']='imported'
                    self.store._insert_event(body,source_id,record['sequence'])
                    self.store.connection.execute('INSERT INTO event_provenance VALUES(?,?,?,?,?)',
                        (body['event_id'],source_id,record['sequence'],digest,'imported'))
                imported.append(entry['legacy_id'])
        return {'imported_ids':imported,'reused_ids':reused,'sha256':preview['sha256'],'backup_key':preview['backup_key'],'readonly':True}

    def list(self, *, offset=0):
        if type(offset) is not int or offset<0:raise ContractError('Invalid history offset')
        return [dict(row) for row in self.store.connection.execute('SELECT id,workspace_id,native_session_id,native_schema,'
            'source_sha256,record_count,journal_state,imported_at_utc FROM legacy_imports WHERE profile_id=? ORDER BY rowid LIMIT 100 OFFSET ?',
            (self.profile_id,offset)).fetchall()]

    def inspect(self,legacy_id):
        row=self.store.connection.execute('SELECT * FROM legacy_imports WHERE id=? AND profile_id=?',(legacy_id,self.profile_id)).fetchone()
        if row is None:raise ContractError('Imported legacy history not found',kind='NOT_FOUND',code=-32010)
        try:
            backup=self.backup_root.parent/row['backup_key']
            manifest=json.loads(read_bytes(backup/'manifest.json',backup))
            if 'legacy-backups/'+manifest['sha256']!=row['backup_key']:
                raise ValueError('Mapping and manifest disagree')
            self._backup(manifest)
        except (OSError,ValueError,KeyError):
            raise ContractError('Legacy backup is unavailable or corrupted; originals were not used to repair it',kind='MANIFEST_MISMATCH',code=-32010) from None
        return {'legacy_id':row['id'],'readonly':True,'origin':'imported_unverified','native_schema':row['native_schema'],
            'source_sha256':row['source_sha256'],'record_count':row['record_count'],'journal_state':row['journal_state'],
            'backup_key':row['backup_key'],'source_path':row['source_path']}
