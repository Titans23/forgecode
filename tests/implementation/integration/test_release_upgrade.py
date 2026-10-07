"""Actual SQLite/file backup and restore with exclusive ownership."""
import json
from hashlib import sha256
import sqlite3
import pytest
from forge.engine.persistence import Store
from forge.release.upgrade import backup_for_upgrade, restore_backup, inspect_database
from tests.implementation.unit.test_release_runtime import release_tree

def test_verified_upgrade_backup_and_matching_restore(tmp_path):
    resources=tmp_path/'resources'; resources.mkdir(); release_tree(resources)
    data=tmp_path/'data'
    with Store(data): pass
    (data/'journals').mkdir(); (data/'journals'/'actual.jsonl').write_bytes(b'original journal')
    before=(data/'engine.sqlite3').read_bytes()
    backup=backup_for_upgrade(data,resources,tmp_path/'backups')
    assert inspect_database(backup)['schema_version']==15
    assert (backup/'journals/actual.jsonl').read_bytes()==b'original journal'
    restored=restore_backup(backup,tmp_path/'restored',resources)
    assert inspect_database(restored)['schema_version']==15
    assert (restored/'journals/actual.jsonl').read_bytes()==b'original journal'
    assert (data/'engine.sqlite3').read_bytes()==before

def test_upgrade_refuses_running_and_unreconciled_tasks(tmp_path):
    r=tmp_path/'resources';r.mkdir();release_tree(r)
    data=tmp_path/'data'
    with Store(data) as store:
        with pytest.raises(Exception,match='writer'): backup_for_upgrade(data,r,tmp_path/'backups')
    con=sqlite3.connect(data/'engine.sqlite3')
    con.execute("INSERT INTO work_items VALUES(?,?,?,?,?,?,?)",('work','turn','turn','running','epoch','later',1))
    con.commit();con.close()
    with pytest.raises(ValueError,match='drain'): backup_for_upgrade(data,r,tmp_path/'backups')

def test_corrupt_backup_and_newer_database_are_preserved(tmp_path):
    r=tmp_path/'resources';r.mkdir();release_tree(r)
    data=tmp_path/'data'
    with Store(data):pass
    backup=backup_for_upgrade(data,r,tmp_path/'backups')
    (backup/'engine.sqlite3').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='integrity'): restore_backup(backup,tmp_path/'restored',r)
    c=sqlite3.connect(data/'engine.sqlite3');c.execute('PRAGMA user_version=99');c.close()
    original=sha256((data/'engine.sqlite3').read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='newer'): backup_for_upgrade(data,r,tmp_path/'backups')
    assert sha256((data/'engine.sqlite3').read_bytes()).hexdigest()==original

def test_uninstall_never_deletes_projects_or_data(tmp_path):
    from forge.release.upgrade import uninstall_policy
    p=uninstall_policy()
    assert p['preserve']==['workspaces','experiment_data','credentials','shared_srt']
    assert p['delete_data_requires_separate_confirmation'] is True

def test_backup_from_actual_schema_one_then_upgrade_restored_copy(tmp_path):
    from pathlib import Path
    import shutil
    migrations=tmp_path/'old-migrations';migrations.mkdir()
    source=Path(__file__).resolve().parents[3]/'forge/engine/migrations/001_initial.sql'
    shutil.copy2(source,migrations/source.name)
    data=tmp_path/'old-data'
    with Store(data,migrations_dir=migrations):pass
    r=tmp_path/'resources';r.mkdir();release_tree(r)
    backup=backup_for_upgrade(data,r,tmp_path/'backups')
    assert inspect_database(backup)['schema_version']==1
    restored=restore_backup(backup,tmp_path/'new-data',r)
    with Store(restored) as store:assert store.diagnostics()['schema_version']==15
    assert inspect_database(backup)['schema_version']==1
    assert inspect_database(data)['schema_version']==1

def test_restore_refuses_existing_destination(tmp_path):
    r=tmp_path/'resources';r.mkdir();release_tree(r)
    data=tmp_path/'data'
    with Store(data):pass
    backup=backup_for_upgrade(data,r,tmp_path/'backups')
    before=(data/'engine.sqlite3').read_bytes()
    with pytest.raises(ValueError,match='existing data'):restore_backup(backup,data,r)
    assert (data/'engine.sqlite3').read_bytes()==before

def test_large_artifact_stream_backup_and_restore(tmp_path):
    r=tmp_path/'resources';r.mkdir();release_tree(r)
    data=tmp_path/'data'
    with Store(data):pass
    artifact=data/'artifacts'/'large.bin';artifact.parent.mkdir()
    with artifact.open('wb') as stream:
        for i in range(8):stream.write(bytes([i])*1024*1024)
    backup=backup_for_upgrade(data,r,tmp_path/'backups')
    restored=restore_backup(backup,tmp_path/'restored',r)
    from forge.release.runtime import digest
    assert digest(artifact)==digest(backup/'artifacts/large.bin')==digest(restored/'artifacts/large.bin')
