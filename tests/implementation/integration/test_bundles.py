"""Real ZIP, SQLite and file IO; synthetic mini scores never claim model performance."""
import base64
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import stat
import sqlite3
import shutil
import subprocess
import sys
import zipfile

import pytest

from benchmark.core.bundle import staged_bundle, write_bundle
from benchmark.core.results import read_results, report_bundle
from forge.application.models import ContractError, validate
from forge.engine.methods import EngineMethods
from forge.engine.persistence import Store, encoded, new_id, utc_now
from forge.engine.rpc import RpcServer
from tests.implementation.integration.test_evaluations import make_evaluation, create, start, claim_next, grade_fixture, finish


def mini_run(tmp_path):
    methods,spec=make_evaluation(tmp_path)
    run,_=create(methods,spec)
    start(methods,run)
    for result,outcome in [('pass','completed'),('fail','completed'),(None,'failed'),(None,'failed')]:
        work=claim_next(methods)
        grade=grade_fixture(methods,work,result) if result else None
        finish(methods,work,agent_outcome=outcome,grade_id=grade,
            error_origin='runner_crash' if work['trial_id']==run['trial_ids'][-1] else None)
    methods.evaluations.retry({'client_action_id':new_id('act'),'trial_id':run['trial_ids'][-1],'reason':'explicit synthetic infrastructure retry'})
    work=claim_next(methods)
    grade=grade_fixture(methods,work,'pass')
    finish(methods,work,agent_outcome='completed',grade_id=grade)
    # Actual durable unknown usage fact, not fabricated zero cost.
    with methods.store.transaction():
        req=new_id('request')
        methods.store.connection.execute('INSERT INTO model_requests VALUES(?,?,1,?,NULL,?)',(req,new_id('inv'),'primary','finished'))
        methods.store.connection.execute('INSERT INTO usage_ledger(request_id,quality,cost_quality) VALUES(?,?,?)',(req,'unknown','unknown'))
        methods.store.connection.execute('INSERT INTO attempt_requests VALUES(?,?)',(req,work['business_id']))
        for role,quality,cost in [('explore','actual','0.125'),('compaction','estimated','0.05')]:
            req=new_id('request')
            methods.store.connection.execute('INSERT INTO model_requests VALUES(?,?,1,?,NULL,?)',(req,new_id('inv'),role,'finished'))
            methods.store.connection.execute('INSERT INTO usage_ledger(request_id,quality,cost_quality,cost,currency) VALUES(?,?,?,?,?)',(req,quality,quality,cost,'USD'))
            methods.store.connection.execute('INSERT INTO attempt_requests VALUES(?,?)',(req,work['business_id']))
    return methods,run


def export(methods,run,path,classification='metadata_only'):
    service=methods.artifacts
    params={'client_action_id':new_id('act'),'scope':{'kind':'run','id':run['run_id']},
        'destination_token':service.grant_destination(path,scope={'kind':'run','id':run['run_id']},classification=classification),
        'classification':classification}
    return service.export(params),params


def import_file(methods,path):
    params={'client_action_id':new_id('act'),'source_token':methods.artifacts.grant_source(path)}
    return methods.artifacts.import_bundle(params),params


def test_roundtrip_hand_computable_metrics_and_read_only_import(tmp_path):
    methods,run=mini_run(tmp_path)
    try:
        path=tmp_path/'results.zip'
        result,params=export(methods,run,path)
        validate('bundle.export.result',result)
        assert methods.artifacts.export(params)['reused_existing_action']
        baseline=methods.evaluations.report_data(run['run_id'])['metrics']
        report=report_bundle(path)['runs'][0]
        assert report['metrics']==baseline
        assert baseline['counts']['planned']==4 and baseline['counts']['passed']==2
        assert baseline['counts']['failed']==1 and baseline['counts']['unscored']==1
        assert baseline['counts']['attempts']==5 and baseline['first_attempt_success_rate']['value']=='0.25'
        assert baseline['cost_per_success']['status']=='unknown'
        assert baseline['total_known_cost']=='0.125' and baseline['total_estimated_cost']=='0.05'
        assert baseline['request_count']==3 and baseline['unknown_cost_requests']==1
        before={table:methods.store.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            for table in ('runs','trials','attempts','work_items','model_requests','usage_ledger','connections','policies')}
        imported,ip=import_file(methods,path)
        validate('bundle.import.result',imported)
        assert imported['origin']=='imported_unverified' and imported['run_ids'][0]!=run['run_id']
        assert methods.artifacts.import_bundle(ip)['reused_existing_action']
        again,_=import_file(methods,path)
        assert again['reused_existing_action'] and again['run_ids']==imported['run_ids']
        assert methods.artifacts.imported_report(imported['run_ids'][0])['metrics']==baseline
        for table,count in before.items(): assert methods.store.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==count
        with pytest.raises(ContractError): methods.evaluations.start({'client_action_id':new_id('act'),'run_id':imported['run_ids'][0]})
    finally: methods.store.close()


@pytest.mark.parametrize('name',['../escape','/absolute','C:/escape','artifacts/x:stream','artifacts/NUL','artifacts/x.','artifacts/a/../b'])
def test_unsafe_archive_paths_never_escape_staging(tmp_path,name):
    path=tmp_path/'unsafe.zip'
    with zipfile.ZipFile(path,'w') as archive: archive.writestr(name,b'unsafe')
    with pytest.raises(ContractError):
        with staged_bundle(path): pass
    assert not (tmp_path/'escape').exists()


@pytest.mark.parametrize('kind',['symlink','hardlink-extra','duplicate','case-conflict','bomb','nested'])
def test_archive_index_rejects_links_duplicates_and_bombs(tmp_path,kind):
    path=tmp_path/'bad.zip'
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        info=zipfile.ZipInfo('artifacts/opaque')
        if kind=='symlink': info.create_system=3; info.external_attr=(stat.S_IFLNK|0o777)<<16
        if kind=='hardlink-extra': info.extra=b'\x0d\x00\x00\x00'
        archive.writestr(info,b'PK\x03\x04nested' if kind=='nested' else b'a')
        if kind in ('duplicate','case-conflict'): archive.writestr('artifacts/opaque' if kind=='duplicate' else 'ARTIFACTS/OPAQUE',b'b')
        if kind=='bomb': archive.writestr('events.jsonl',b'x'*200000)
    with pytest.raises(ContractError):
        with staged_bundle(path): pass


def test_checksum_schema_and_relational_tampering_publish_nothing(tmp_path):
    methods,run=mini_run(tmp_path)
    try:
        path=tmp_path/'good.zip';export(methods,run,path)
        with zipfile.ZipFile(path) as archive: files={i.filename:archive.read(i) for i in archive.infolist()}
        for mode in ('checksum','schema','foreign-trial','missing-file'):
            data=dict(files)
            if mode=='checksum': data['attempts.jsonl']+=b'changed'
            elif mode=='schema':
                manifest=json.loads(data['manifest.json']);manifest['schema_version']='unsupported';data['manifest.json']=encoded(manifest).encode()
            elif mode=='missing-file': data.pop('grades.jsonl')
            else:
                rows=[json.loads(x) for x in data['attempts.jsonl'].splitlines()];rows[0]['trial_id']=new_id('trial')
                data['attempts.jsonl']=b''.join(encoded(x).encode()+b'\n' for x in rows)
                data.pop('manifest.json');data.pop('checksums.sha256')
            bad=tmp_path/f'{mode}.zip'
            if mode=='foreign-trial': write_bundle(bad,data,task_ids=['case-pass','case-fail','case-unscored','case-retry'])
            else:
                with zipfile.ZipFile(bad,'w') as archive:
                    for name,raw in data.items(): archive.writestr(name,raw)
            with pytest.raises(ContractError): import_file(methods,bad)
            assert methods.store.connection.execute('SELECT COUNT(*) FROM imported_runs').fetchone()[0]==0
    finally: methods.store.close()


def test_same_identity_changed_facts_quarantined_without_overwrite(tmp_path):
    methods,run=mini_run(tmp_path)
    try:
        good=tmp_path/'good.zip';export(methods,run,good);first,_=import_file(methods,good)
        with zipfile.ZipFile(good) as archive: files={i.filename:archive.read(i) for i in archive.infolist() if i.filename not in ('manifest.json','checksums.sha256')}
        rows=[json.loads(x) for x in files['grades.jsonl'].splitlines()]
        rows[0]['result']='fail';rows[0]['reward']='0'
        files['grades.jsonl']=b''.join(encoded(x).encode()+b'\n' for x in rows)
        bad=tmp_path/'conflict.zip';write_bundle(bad,files,task_ids=['case-pass','case-fail','case-unscored','case-retry'])
        with pytest.raises(ContractError) as error: import_file(methods,bad)
        assert error.value.kind=='EVENT_CONFLICT'
        assert methods.store.connection.execute('SELECT COUNT(*) FROM bundle_conflicts').fetchone()[0]==1
        assert methods.store.connection.execute('SELECT COUNT(*) FROM imported_runs').fetchone()[0]==1
        assert methods.artifacts.imported_report(first['run_ids'][0])['metrics']['counts']['passed']==2
    finally: methods.store.close()


def test_artifact_chunks_profile_integrity_and_redaction_preserve_original(tmp_path):
    methods,run=mini_run(tmp_path)
    try:
        attempt=methods.store.connection.execute('SELECT id FROM attempts LIMIT 1').fetchone()[0]
        raw=b'{"api_key":"secret-original","detail":"Bearer secret-original"}'
        artifact=methods.store.publish_artifact(raw,origin='trusted_engine',classification='metadata',attempt_id=attempt)
        private=methods.store.publish_artifact(b'private raw grader output',origin='official-harbor',classification='private-runner',attempt_id=attempt)
        path=tmp_path/'redacted.zip';exported,_=export(methods,run,path,'redacted_artifacts')
        with zipfile.ZipFile(path) as archive:
            copy=archive.read('artifacts/'+artifact['id'])
            assert b'secret-original' not in copy and methods.store.read_artifact(artifact['id'])==raw
            evidence=json.loads(archive.read('evidence.json'))
            assert any(x['artifact_id']==private['id'] and x['status']=='omitted_private' for x in evidence['artifacts'])
        desc=methods.artifacts.describe({'artifact_id':exported['artifact']['artifact_id']});validate('artifact.describe.result',desc)
        chunk=methods.artifacts.read_chunk({'artifact_id':desc['artifact_id'],'offset':0,'length':11})
        assert base64.b64decode(chunk['data_base64'])==path.read_bytes()[:11]
        with pytest.raises(ContractError): methods.artifacts.read_chunk({'artifact_id':desc['artifact_id'],'offset':0,'length':262145})
        with pytest.raises(ContractError): methods.artifacts.describe({'artifact_id':private['id']})
        old=methods.service.profile_id;methods.service.profile_id='other-profile'
        with pytest.raises(ContractError): methods.artifacts.describe({'artifact_id':desc['artifact_id']})
        methods.service.profile_id=old
        (methods.store.data_dir/ artifact['relative_storage_key']).unlink()
        assert not methods.artifacts.describe({'artifact_id':artifact['id']})['available']
        second=tmp_path/'missing.zip';export(methods,run,second)
        assert artifact['id'] in report_bundle(second)['runs'][0]['missing_evidence']
    finally: methods.store.close()


def test_host_file_grants_scope_expiry_and_no_overwrite(tmp_path):
    methods,run=mini_run(tmp_path)
    try:
        path=tmp_path/'out.zip';result,params=export(methods,run,path)
        with pytest.raises(ContractError): methods.artifacts.grant_destination(path,scope=params['scope'],classification='metadata_only')
        with pytest.raises(ContractError): methods.artifacts.export({**params,'client_action_id':new_id('act'),'scope':{'kind':'all'}})
        with pytest.raises(ContractError): methods.artifacts.import_bundle({'client_action_id':new_id('act'),'source_token':'x'*40})
        token=methods.artifacts.grant_source(path)
        path.write_bytes(b'changed')
        with pytest.raises(ContractError): methods.artifacts.import_bundle({'client_action_id':new_id('act'),'source_token':token})
    finally: methods.store.close()


def test_offline_cli_does_not_execute_embedded_code(tmp_path):
    methods,run=mini_run(tmp_path)
    try:
        marker=tmp_path/'executed';path=tmp_path/'bundle.zip'
        attempt=methods.store.connection.execute('SELECT id FROM attempts LIMIT 1').fetchone()[0]
        methods.store.publish_artifact(f'__import__("pathlib").Path({str(marker)!r}).touch()'.encode(),origin='official-harbor',classification='private-runner',attempt_id=attempt)
        exported,_=export(methods,run,path)
        with zipfile.ZipFile(path) as archive: files={item.filename:archive.read(item) for item in archive.infolist() if item.filename not in ('manifest.json','checksums.sha256')}
        opaque=new_id('art');code=f'__import__("pathlib").Path({str(marker)!r}).touch()'.encode()
        files['artifacts/'+opaque]=code
        evidence=json.loads(files['evidence.json']);evidence['classification']='redacted_artifacts'
        evidence['artifacts'].append({'artifact_id':opaque,'run_id':run['run_id'],'source_sha256':hashlib.sha256(code).hexdigest(),
            'export_sha256':hashlib.sha256(code).hexdigest(),'status':'redacted_copy'})
        files['evidence.json']=encoded(evidence).encode();embedded=tmp_path/'embedded.zip'
        write_bundle(embedded,files,task_ids=exported['manifest']['task_ids'])
        process=subprocess.run([sys.executable,'-m','benchmark.core.results','report','--from-bundle',str(embedded)],capture_output=True,text=True,timeout=30)
        assert process.returncode==0 and json.loads(process.stdout)['origin']=='imported_unverified'
        imported,_=import_file(methods,embedded)
        assert imported['origin']=='imported_unverified'
        assert not marker.exists()
    finally: methods.store.close()


def test_valid_index_nested_archive_is_rejected_during_actual_extraction(tmp_path):
    path=tmp_path/'nested.zip'
    write_bundle(path,{'artifacts/'+new_id('art'):b'PK\x03\x04actual nested archive magic'},task_ids=['synthetic'])
    with pytest.raises(ContractError,match='Nested archives'):
        with staged_bundle(path): pass


def test_real_source_hardlink_and_expired_selection_are_rejected(tmp_path):
    methods,run=mini_run(tmp_path)
    try:
        path=tmp_path/'out.zip';export(methods,run,path)
        link=tmp_path/'linked.zip';os_link=__import__('os').link;os_link(path,link)
        with pytest.raises(ContractError,match='unlinked'): methods.artifacts.grant_source(link)
        link.unlink()
        token=methods.artifacts.grant_source(path);methods.artifacts.grants[token]['expires']=0
        with pytest.raises(ContractError) as error: methods.artifacts.import_bundle({'client_action_id':new_id('act'),'source_token':token})
        assert error.value.kind=='UNAUTHORIZED'
    finally: methods.store.close()


def test_export_response_loss_recovers_frozen_bytes_without_overwrite(tmp_path,monkeypatch):
    methods,run=mini_run(tmp_path)
    try:
        path=tmp_path/'out.zip';scope={'kind':'run','id':run['run_id']}
        params={'client_action_id':new_id('act'),'scope':scope,'classification':'metadata_only',
            'destination_token':methods.artifacts.grant_destination(path,scope=scope,classification='metadata_only')}
        original=methods.service._record_action
        def fail_once(*args): raise RuntimeError('injected response publication failure')
        monkeypatch.setattr(methods.service,'_record_action',fail_once)
        with pytest.raises(RuntimeError): methods.artifacts.export(params)
        frozen=path.read_bytes()
        assert methods.store.connection.execute('SELECT COUNT(*) FROM bundle_exports').fetchone()[0]==1
        assert methods.service._existing_action('bundle.export',params) is None
        monkeypatch.setattr(methods.service,'_record_action',original)
        # Lose all in-memory grants, as on restart; durable intent is authoritative.
        methods.artifacts.grants.clear()
        result=methods.artifacts.export(params)
        assert path.read_bytes()==frozen and result['manifest']['bundle_id']==report_bundle(path)['bundle_id']
        assert methods.artifacts.export(params)['reused_existing_action']
    finally: methods.store.close()


def test_sqlite_failure_during_second_run_publish_rolls_back_all_runs(tmp_path):
    methods,run=mini_run(tmp_path)
    try:
        _,spec=make_evaluation(tmp_path,service=methods.service);second,_=create(methods,spec)
        scope={'kind':'all'};path=tmp_path/'all.zip'
        params={'client_action_id':new_id('act'),'scope':scope,'classification':'metadata_only',
            'destination_token':methods.artifacts.grant_destination(path,scope=scope,classification='metadata_only')}
        methods.artifacts.export(params)
        source_ids=sorted((run['run_id'],second['run_id']))
        methods.store.connection.execute(f"CREATE TRIGGER fail_second_import BEFORE INSERT ON imported_runs WHEN NEW.source_run_id='{source_ids[1]}' BEGIN SELECT RAISE(ABORT,'actual transaction failure'); END;")
        with pytest.raises(sqlite3.IntegrityError): import_file(methods,path)
        for table in ('imported_runs','imported_bundles','imported_facts'): assert methods.store.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0
        assert methods.store.connection.execute("SELECT COUNT(*) FROM artifacts WHERE origin='imported_unverified'").fetchone()[0]==0
        methods.store.connection.execute('DROP TRIGGER fail_second_import')
        imported,_=import_file(methods,path)
        assert len(imported['run_ids'])==2
        raw=methods.store.connection.execute('SELECT raw_artifact_id FROM imported_bundles').fetchone()[0]
        assert methods.store.read_artifact(raw)==path.read_bytes()
        with pytest.raises(ContractError): methods.artifacts.read_chunk({'artifact_id':raw,'offset':0,'length':64})
    finally: methods.store.close()


def test_imported_report_rpc_and_human_annotation_never_authorize_execution(tmp_path):
    methods,run=mini_run(tmp_path)
    try:
        attempt=methods.store.connection.execute('SELECT id FROM attempts LIMIT 1').fetchone()[0]
        before=methods.evaluations.report_data(run['run_id'])['metrics']
        methods.artifacts.annotate(attempt,author='human-fixture',category='verification',evidence_refs=[])
        with pytest.raises(ContractError): methods.artifacts.annotate(attempt,author='human-fixture',category='automatic-root-cause',evidence_refs=[])
        path=tmp_path/'annotated.zip';export(methods,run,path);imported,_=import_file(methods,path)
        report=methods.evaluations.report({'run_id':imported['run_ids'][0]})
        data=json.loads(methods.store.read_artifact(report['report_artifact']['artifact_id']))
        assert data['origin']=='imported_unverified' and data['metrics']==before and len(data['annotations'])==1
        comparison=methods.evaluations.compare({'run_ids':[run['run_id'],imported['run_ids'][0]],'protocol':'paired_task_set'})
        assert not comparison['comparable'] and any('unverified_origin' in x for x in comparison['differences'])
    finally: methods.store.close()


def test_real_rpc_main_file_grants_renderer_denial_and_chunk_download(tmp_path):
    methods,run=mini_run(tmp_path);methods.initialized=True
    main=RpcServer(methods,principal='main');renderer=RpcServer(methods,principal='renderer')
    async def exercise():
        path=tmp_path/'rpc.zip'
        request={'jsonrpc':'2.0','id':'select','method':'bundle.prepare_export','params':{'path':str(path),'scope':{'kind':'run','id':run['run_id']},'classification':'metadata_only'}}
        denied=await renderer.handle_frame(encoded(request).encode())
        assert denied['error']['data']['kind']=='UNAUTHORIZED'
        grant=(await main.handle_frame(encoded(request).encode()))['result']['destination_token']
        exported=(await main.handle_frame(encoded({'jsonrpc':'2.0','id':'export','method':'bundle.export','params':{'client_action_id':new_id('act'),'scope':request['params']['scope'],'classification':'metadata_only','destination_token':grant}}).encode()))['result']
        selected=(await main.handle_frame(encoded({'jsonrpc':'2.0','id':'source','method':'bundle.prepare_import','params':{'path':str(path)}}).encode()))['result']['source_token']
        imported=(await main.handle_frame(encoded({'jsonrpc':'2.0','id':'import','method':'bundle.import','params':{'client_action_id':new_id('act'),'source_token':selected}}).encode()))['result']
        assert imported['origin']=='imported_unverified'
        chunk=await renderer.handle_frame(encoded({'jsonrpc':'2.0','id':'chunk','method':'artifact.read_chunk','params':{'artifact_id':exported['artifact']['artifact_id'],'offset':0,'length':262144}}).encode())
        assert base64.b64decode(chunk['result']['data_base64'])==path.read_bytes()[:262144]
        assert chunk['result']['sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    try: asyncio.run(exercise())
    finally: methods.store.close()


@pytest.mark.parametrize('mode',['wrong-count','oversized-central-index','file-quota','total-quota','entry-quota'])
def test_preallocation_index_and_expansion_quotas_are_enforced(tmp_path,monkeypatch,mode):
    from benchmark.core import bundle
    path=tmp_path/'quota.zip';write_bundle(path,{'facts.json':b'12345'},task_ids=['synthetic'])
    if mode in ('wrong-count','oversized-central-index'):
        raw=bytearray(path.read_bytes());offset=raw.rfind(b'PK\x05\x06')
        if mode=='wrong-count': raw[offset+8:offset+12]=(1).to_bytes(2,'little')*2
        else: raw[offset+12:offset+16]=(33554433).to_bytes(4,'little')
        path.write_bytes(raw)
    elif mode=='file-quota': monkeypatch.setattr(bundle,'MAX_FILE',4)
    elif mode=='total-quota': monkeypatch.setattr(bundle,'MAX_TOTAL',path.stat().st_size-1)
    else: monkeypatch.setattr(bundle,'MAX_FILES',2)
    with pytest.raises(ContractError):
        with staged_bundle(path): pass


@pytest.mark.parametrize('mode',['typed-relation','huge-decimal','duplicate-request'])
def test_checksum_valid_but_unsafe_structured_facts_are_rejected(tmp_path,mode):
    methods,run=mini_run(tmp_path)
    try:
        path=tmp_path/'good.zip';result,_=export(methods,run,path)
        with zipfile.ZipFile(path) as archive: files={item.filename:archive.read(item) for item in archive.infolist() if item.filename not in ('manifest.json','checksums.sha256')}
        kind='trials' if mode=='typed-relation' else 'requests'
        rows=[json.loads(x) for x in files[kind+'.jsonl'].splitlines()]
        if mode=='typed-relation': rows[0]['run_id']=[]
        elif mode=='huge-decimal': rows[0]['cost']='1e1000000000'
        else: rows.append(deepcopy(rows[0]))
        files[kind+'.jsonl']=b''.join(encoded(x).encode()+b'\n' for x in rows)
        invalid=tmp_path/'invalid.zip';write_bundle(invalid,files,task_ids=result['manifest']['task_ids'])
        with pytest.raises(ContractError): import_file(methods,invalid)
        assert methods.store.connection.execute('SELECT COUNT(*) FROM imported_runs').fetchone()[0]==0
    finally: methods.store.close()


def test_saved_mini_bundle_has_stable_hand_computable_offline_metrics():
    directory=Path('tests/implementation/fixtures/eval-mini-bundle')
    report=report_bundle(directory/'result-bundle.zip')
    metrics=report['runs'][0]['metrics']
    assert metrics==json.loads((directory/'bundle-expected.json').read_text())
    assert metrics['counts']['planned']==4 and metrics['counts']['attempts']==5
    assert metrics['planned_success_rate']['value']=='0.5' and metrics['grading_coverage']['value']=='0.75'
    assert metrics['completion_false_positive']['value']=='0.3333333333333333333333333333333333333333'
    assert metrics['total_known_cost']=='0.125' and metrics['total_estimated_cost']=='0.05' and metrics['unknown_cost_requests']==1
    assert report['origin']=='imported_unverified'


def test_migration_backs_up_and_binds_existing_attempt_artifact_without_guessing_owner(tmp_path):
    migrations=tmp_path/'old-migrations';migrations.mkdir()
    for path in Path('forge/engine/migrations').glob('*.sql'):
        if int(path.name[:3])<=9: shutil.copyfile(path,migrations/path.name)
    data_dir=tmp_path/'data';store=Store(data_dir,migrations_dir=migrations)
    owned,unbound=new_id('art'),new_id('art')
    try:
        spec=json.loads(Path('contracts/v1/examples/run-spec.valid.json').read_text())
        run,trial,attempt=new_id('run'),new_id('trial'),new_id('attempt')
        with store.transaction():
            ref=store._configuration_snapshot(spec,hashlib.sha256(encoded(spec).encode()).hexdigest())
            store.connection.execute('INSERT INTO experiments VALUES(?,?,?)',(spec['experiment_id'],'synthetic old database','paired_task_set'))
            store.connection.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(run,spec['experiment_id'],ref['sha256'],encoded(spec),'completed'))
            store.connection.execute('INSERT INTO run_details VALUES(?,?,?,?)',(run,'migration-fixture',ref['snapshot_id'],utc_now()))
            task=spec['dataset']['task_ids'][0]
            store.connection.execute('INSERT INTO trials VALUES(?,?,?,?,?,NULL)',(trial,run,task,spec['dataset']['task_revisions'][task],0))
            store.connection.execute('INSERT INTO attempts VALUES(?,?,1,?,NULL,?)',(attempt,trial,'finished','clean'))
            directory=data_dir/'artifacts';directory.mkdir()
            for artifact in (owned,unbound):
                raw=b'actual old bytes';(directory/artifact).write_bytes(raw)
                store.connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?)',(artifact,'artifacts/'+artifact,hashlib.sha256(raw).hexdigest(),len(raw),'trusted_engine','metadata'))
            store.connection.execute('INSERT INTO artifact_attempts VALUES(?,?)',(owned,attempt))
    finally: store.close()
    with Store(data_dir) as upgraded:
        expected=max(int(path.name[:3]) for path in Path('forge/engine/migrations').glob('*.sql'))
        assert upgraded.diagnostics()['schema_version']==expected and not upgraded.read_only
        backups=list((data_dir/'backups').glob('*.sqlite3'));assert len(backups)==1
        with sqlite3.connect(backups[0]) as backup: assert backup.execute('PRAGMA user_version').fetchone()[0]==9
        assert upgraded.connection.execute('SELECT profile_id FROM artifact_profiles WHERE artifact_id=?',(owned,)).fetchone()[0]=='migration-fixture'
        assert not upgraded.connection.execute('SELECT 1 FROM artifact_profiles WHERE artifact_id=?',(unbound,)).fetchone()
        assert upgraded.read_artifact(owned)==b'actual old bytes'
