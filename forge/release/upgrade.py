"""Manual upgrade snapshots. Never delete user projects or overwrite a newer DB."""
from contextlib import closing
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from uuid import uuid4
from forge.release.runtime import digest, verify_asset, verify_manifest

def inspect_database(directory, *, immutable=True):
    path=Path(directory).resolve(strict=True)/'engine.sqlite3'
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro'+('&immutable=1' if immutable else ''),uri=True)) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ValueError('Database integrity check failed')
        return {'schema_version':db.execute('PRAGMA user_version').fetchone()[0]}

def _plain_files(root):
    for current,directories,names in os.walk(root,followlinks=False):
        for name in [*directories,*names]:
            p=Path(current)/name
            if p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400:
                raise ValueError('Data backup refuses redirected files')
        for name in names:
            p=Path(current)/name
            if p.is_file() and p.stat().st_nlink!=1: raise ValueError('Data backup refuses external hardlinks')
            yield p

def _inventory(root):
    return [{'path':p.relative_to(root).as_posix(),'size_bytes':p.stat().st_size,'sha256':digest(p)}
        for p in sorted(_plain_files(root)) if p.name!='upgrade-backup.json']

def _atomic_file(path, content):
    with path.open('xb') as stream:
        stream.write(content);stream.flush();os.fsync(stream.fileno())
    path.chmod(0o600)

def _copy_file(source, destination):
    before=source.stat()
    with source.open('rb') as incoming,destination.open('xb') as outgoing:
        shutil.copyfileobj(incoming,outgoing,1024*1024)
        outgoing.flush();os.fsync(outgoing.fileno())
    after=source.stat()
    if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
        raise ValueError('Backup input changed during copy')
    destination.chmod(0o600)

def backup_for_upgrade(data_dir, current_resources, backup_root, *, target_resources=None):
    from forge.engine.persistence import DirectoryLock, sync_directory, validate_data_directory
    current=verify_manifest(current_resources)
    target=verify_manifest(target_resources) if target_resources else current
    if target['database_schema']<current['database_schema']: raise ValueError('Target is older; restore its matching backup instead')
    data=validate_data_directory(Path(data_dir));data.resolve(strict=True)
    backup_root=Path(backup_root).absolute()
    if backup_root.is_relative_to(data) or data.is_relative_to(backup_root):
        raise ValueError('Backup must use an independent directory')
    backup_root.mkdir(parents=True,exist_ok=True,mode=0o700)
    if backup_root.resolve()!=backup_root: raise ValueError('Backup root must not redirect')
    stage=None
    with_lock=DirectoryLock(data)
    try:
        state=inspect_database(data,immutable=False)
        if state['schema_version']>current['database_schema']: raise ValueError('Database schema is newer; preserved read-only')
        source=sqlite3.connect((data/'engine.sqlite3').as_uri()+'?mode=ro',uri=True)
        try:
            if source.execute("SELECT 1 FROM work_items WHERE state!='finished' LIMIT 1").fetchone():
                raise ValueError('Tasks must drain and uncertain work must be reconciled before upgrade')
            if source.execute("SELECT 1 FROM sqlite_master WHERE name='turn_lifecycle'").fetchone() and source.execute("SELECT 1 FROM turn_lifecycle WHERE cleanup_state IN ('unknown','pending','running','residual') LIMIT 1").fetchone():
                raise ValueError('Task cleanup must drain and reconcile before upgrade')
            stage=Path(tempfile.mkdtemp(prefix='.upgrade-',dir=backup_root))
            with closing(sqlite3.connect(stage/'engine.sqlite3')) as destination: source.backup(destination)
        finally: source.close()
        for p in _plain_files(data):
            relative=p.relative_to(data)
            if p.name in ('engine.sqlite3','engine.sqlite3-wal','engine.sqlite3-shm','owner.lock'):continue
            output=stage/relative;output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            _copy_file(p,output)
        metadata={'schema_version':'forge.upgrade.backup.v1','backup_id':'backup-'+str(uuid4()),
            'created_at_utc':datetime.now(timezone.utc).isoformat(),'build_id':current['build_id'],
            'contract_manifest_hash':current['contract_manifest_hash'],'database_schema':state['schema_version'],
            'target_build_id':target['build_id'],'files':_inventory(stage)}
        _atomic_file(stage/'upgrade-backup.json',(json.dumps(metadata,indent=2)+'\n').encode())
        sync_directory(stage)
        final=backup_root/metadata['backup_id'];stage.rename(final);stage=None;sync_directory(backup_root)
        return final
    finally:
        with_lock.close()
        if stage is not None and stage.parent==backup_root and stage.name.startswith('.upgrade-'):
            shutil.rmtree(stage)

def restore_backup(backup, destination, resources):
    from forge.engine.persistence import sync_directory
    release=verify_manifest(resources);backup=Path(backup).resolve(strict=True)
    metadata=json.loads((backup/'upgrade-backup.json').read_bytes())
    if metadata.get('schema_version')!='forge.upgrade.backup.v1' or metadata.get('build_id')!=release['build_id'] or metadata.get('contract_manifest_hash')!=release['contract_manifest_hash'] or metadata.get('database_schema',99999)>release['database_schema']:
        raise ValueError('Rollback needs a backup matching the old release')
    if metadata.get('files')!=_inventory(backup): raise ValueError('Backup integrity mismatch')
    for a in metadata['files']:verify_asset(backup,a)
    if inspect_database(backup)['schema_version']!=metadata['database_schema']: raise ValueError('Backup database schema mismatch')
    destination=Path(destination).absolute()
    if destination.exists():raise ValueError('Restore destination must be new; existing data is preserved')
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    stage=Path(tempfile.mkdtemp(prefix='.restore-',dir=destination.parent))
    try:
        for a in metadata['files']:
            output=stage/a['path'];output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            _copy_file(backup/a['path'],output)
            verify_asset(stage,a)
        sync_directory(stage);stage.rename(destination);sync_directory(destination.parent)
        return destination
    finally:
        if stage.exists() and stage.parent==destination.parent and stage.name.startswith('.restore-'):shutil.rmtree(stage)

def uninstall_policy():
    return {'preserve':['workspaces','experiment_data','credentials','shared_srt'],
        'delete_data_requires_separate_confirmation':True,'automatic_data_deletion':False}
