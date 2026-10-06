"""Bounded ZIP interchange. Integrity is not a signature or execution permission."""
from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import unicodedata
import zipfile

from forge.application.models import ContractError, strict_loads, validate
from forge.engine.persistence import encoded, new_id, utc_now

MAX_TOTAL=1073741824
MAX_FILE=104857600
MAX_FILES=20000
MAX_RATIO=100
MAX_MANIFEST=16777216
RESERVED=re.compile(r'^(?:CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(?:\.|$)',re.I)
ARCHIVE_MAGIC=(b'PK\x03\x04',b'PK\x05\x06',b'\x1f\x8b',b'7z\xbc\xaf\x27\x1c',b'Rar!',b'BZh',b'\xfd7zXZ')


def safe_name(name):
    if not isinstance(name,str) or not 1<=len(name)<=4096 or unicodedata.normalize('NFC',name)!=name:
        raise ContractError('Invalid archive path')
    parts=name.split('/')
    if any(part in ('','.','..') or ':' in part or '\\' in part or part[-1:] in (' ','.')
           or RESERVED.match(part) or any(ord(char)<32 for char in part) for part in parts):
        raise ContractError('Unsafe archive path')
    if PurePosixPath(name).is_absolute(): raise ContractError('Absolute archive path')
    return name


def file_identity(path):
    """No link/reparse traversal, including ancestors; bind the selected real file."""
    path=Path(os.path.abspath(path))
    for parent in reversed((path,*path.parents)):
        info=parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ContractError('File selection contains a link or reparse point')
    info=path.stat()
    if not path.is_file() or info.st_nlink!=1: raise ContractError('Selection must be one regular, unlinked file')
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)


@contextmanager
def selected_file(path,expected=None):
    identity=file_identity(path)
    if expected is not None and tuple(expected)!=identity: raise ContractError('Selected file changed',kind='STALE_REVISION',code=-32010)
    with Path(path).open('rb') as file:
        info=os.fstat(file.fileno())
        if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=identity:
            raise ContractError('Selected file changed before opening',kind='STALE_REVISION',code=-32010)
        yield file
        if file_identity(path)!=identity: raise ContractError('Selected file changed while reading',kind='STALE_REVISION',code=-32010)


def write_bundle(path,files,*,task_ids,bundle_id=None,source_origin='trusted_engine'):
    if not files or len(files)+2>MAX_FILES: raise ContractError('Bundle entry quota exceeded',kind='ARTIFACT_LIMIT',code=-32010)
    names=set();contents=[];total=0
    for name,raw in sorted(files.items()):
        safe_name(name)
        if name.casefold() in names or name in ('manifest.json','checksums.sha256'): raise ContractError('Duplicate/reserved bundle name')
        names.add(name.casefold())
        if len(raw)>MAX_FILE: raise ContractError('Bundle file quota exceeded',kind='ARTIFACT_LIMIT',code=-32010)
        total+=len(raw)
        contents.append({'path':name,'size_bytes':len(raw),'sha256':sha256(raw).hexdigest(),
            'media_type':'application/json' if name.endswith(('.json','.jsonl')) else 'application/octet-stream',
            'redacted':name.startswith('artifacts/') or name=='events.jsonl'})
    manifest={'schema_version':'forge.bundle.v1','bundle_id':bundle_id or new_id('bundle'),'source_origin':source_origin,
        'producer_build':'forgecode-v4-development','created_at_utc':utc_now(),'capture_mode':'metadata',
        'redaction_version':'metadata-v1','task_ids':sorted(set(task_ids)),
        'supported_protocols':['forge.eval.runspec.v1','forge.events.v1'],'contents':contents,'total_size_bytes':total}
    validate('bundle-manifest',manifest)
    raw_manifest=encoded(manifest).encode()
    checksums=''.join(f"{item['sha256']}  {item['path']}\n" for item in contents).encode()
    if len(raw_manifest)>MAX_MANIFEST or total+len(raw_manifest)+len(checksums)>MAX_TOTAL:
        raise ContractError('Bundle total quota exceeded',kind='ARTIFACT_LIMIT',code=-32010)
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('manifest.json',raw_manifest)
        archive.writestr('checksums.sha256',checksums)
        for name,raw in sorted(files.items()): archive.writestr(name,raw)
    return manifest


@contextmanager
def staged_bundle(path,*,staging_parent=None,expected=None):
    """Freeze the selected archive, scan, stage and verify before publication."""
    try:
        with tempfile.TemporaryDirectory(prefix='bundle-',dir=staging_parent) as directory:
            staging=Path(directory);os.chmod(staging,0o700)
            root=staging/'contents';root.mkdir(mode=0o700)
            frozen=staging/'source.zip';digest=sha256()
            with selected_file(path,expected) as source,frozen.open('xb') as output:
                if os.fstat(source.fileno()).st_size>MAX_TOTAL: raise ContractError('Compressed bundle quota exceeded')
                size=0
                while chunk:=source.read(1048576):
                    size+=len(chunk)
                    if size>MAX_TOTAL: raise ContractError('Compressed bundle grew beyond quota')
                    digest.update(chunk);output.write(chunk)
                output.flush();os.fsync(output.fileno())
            scan_zip_index(frozen)
            with zipfile.ZipFile(frozen) as archive:
                entries=archive.infolist()
                if len(entries)>MAX_FILES or not entries: raise ContractError('Bundle entry quota exceeded')
                names={};total=0
                for item in entries:
                    name=safe_name(item.filename);folded=name.casefold()
                    mode=item.external_attr>>16
                    if folded in names or item.is_dir() or stat.S_IFMT(mode) not in (0,stat.S_IFREG) or item.extra or item.flag_bits&1:
                        raise ContractError('Duplicate path, link, extended link metadata or encrypted entry')
                    if item.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED): raise ContractError('Unsupported ZIP compression')
                    if item.file_size>MAX_FILE or item.file_size>MAX_RATIO*max(1,item.compress_size): raise ContractError('Bundle file or compression-ratio quota exceeded')
                    total+=item.file_size;names[folded]=item
                if total>MAX_TOTAL: raise ContractError('Bundle expanded total quota exceeded')
                if 'manifest.json' not in names or 'checksums.sha256' not in names: raise ContractError('Bundle index missing')
                if names['manifest.json'].file_size>MAX_MANIFEST: raise ContractError('Manifest quota exceeded')
                manifest=strict_loads(archive.read(names['manifest.json']),max_bytes=MAX_MANIFEST)
                validate('bundle-manifest',manifest)
                indexed={}
                for item in manifest['contents']:
                    name=safe_name(item['path'])
                    if name.casefold() in indexed or name in ('manifest.json','checksums.sha256'): raise ContractError('Duplicate manifest content')
                    indexed[name.casefold()]=item
                if set(names)!=set(indexed)|{'manifest.json','checksums.sha256'}: raise ContractError('Manifest and ZIP index disagree')
                if manifest['total_size_bytes']!=sum(item['size_bytes'] for item in indexed.values()): raise ContractError('Manifest total mismatch')
                checksums=''.join(f"{item['sha256']}  {item['path']}\n" for item in manifest['contents']).encode()
                if archive.read(names['checksums.sha256'])!=checksums: raise ContractError('Checksum index mismatch')
                for folded,item in indexed.items():
                    info=names[folded]
                    if info.filename!=item['path'] or info.file_size!=item['size_bytes']: raise ContractError('Manifest file identity mismatch')
                    destination=root/item['path'];destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    actual=sha256();size=0;head=b''
                    with archive.open(info) as input_file,destination.open('xb') as output:
                        while chunk:=input_file.read(1048576):
                            if not head: head=chunk[:512]
                            size+=len(chunk)
                            if size>item['size_bytes']: raise ContractError('Expanded file exceeds declared size')
                            actual.update(chunk);output.write(chunk)
                    if head.startswith(ARCHIVE_MAGIC) or head[257:262]==b'ustar': raise ContractError('Nested archives are unsupported')
                    if size!=item['size_bytes'] or actual.hexdigest()!=item['sha256']: raise ContractError('Bundle checksum mismatch',kind='MANIFEST_MISMATCH',code=-32010)
            yield root,manifest,digest.hexdigest()
    except (zipfile.BadZipFile,UnicodeError) as error:
        raise ContractError('Invalid ZIP bundle') from error


def scan_zip_index(path):
    """Bound the central directory before ZipFile allocates entry objects.
    ZIP64/split archives are unnecessary for the fixed <=1 GiB / 20000 format."""
    size=path.stat().st_size
    with path.open('rb') as file:
        file.seek(max(0,size-65557));tail=file.read(65557)
        offset=tail.rfind(b'PK\x05\x06')
        if offset<0 or offset+22>len(tail): raise ContractError('ZIP index missing')
        end=tail[offset:offset+22]
        number=lambda raw:int.from_bytes(raw,'little')
        count=number(end[10:12]);directory_size=number(end[12:16]);directory_offset=number(end[16:20])
        end_offset=size-len(tail)+offset
        if number(end[4:6]) or number(end[6:8]) or number(end[8:10])!=count or not 1<=count<=MAX_FILES:
            raise ContractError('Split/ZIP64 or oversized ZIP index')
        if directory_size>33554432 or directory_offset+directory_size!=end_offset or offset+22+number(end[20:22])!=len(tail):
            raise ContractError('Unbounded or inconsistent ZIP index')
        file.seek(directory_offset);scanned=0;consumed=0
        while consumed<directory_size:
            header=file.read(46)
            if len(header)!=46 or header[:4]!=b'PK\x01\x02': raise ContractError('Invalid central ZIP record')
            name_size=number(header[28:30]);extra_size=number(header[30:32]);comment_size=number(header[32:34])
            scanned+=1
            if scanned>MAX_FILES or not 1<=name_size<=16384 or extra_size or number(header[34:36]): raise ContractError('ZIP entry count, name or extended metadata invalid')
            skip=name_size+extra_size+comment_size;consumed+=46+skip
            if consumed>directory_size: raise ContractError('ZIP index record exceeds boundary')
            file.seek(skip,os.SEEK_CUR)
        if scanned!=count: raise ContractError('ZIP declared/actual entry count differs')
