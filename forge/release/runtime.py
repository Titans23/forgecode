"""Verify the complete installed group before loading any private component."""
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

SCHEMA='forge.release.manifest.v1'
PROTOCOL='forge.engine.v1'

def digest(path):
    h=sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(1024*1024): h.update(chunk)
    return h.hexdigest()

def verify_asset(root,asset):
    root=Path(root).resolve(strict=True)
    value=asset.get('path')
    if not isinstance(value,str) or not value or '\\' in value or ':' in value or PurePosixPath(value).is_absolute() or '..' in PurePosixPath(value).parts:
        raise ValueError('Resource path is outside trusted root')
    path=root/value
    try:
        actual=path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError('Resource is unavailable or has an invalid link: '+value) from error
    if not actual.is_relative_to(root) or not actual.is_file():
        raise ValueError('Resource link is outside trusted root')
    link=asset.get('symlink')
    if (link is not None and (not path.is_symlink() or os.readlink(path)!=link)) or (link is None and path.is_symlink()):
        raise ValueError('Resource link inventory differs')
    if digest(actual)!=asset.get('sha256') or actual.stat().st_size!=asset.get('size_bytes'):
        raise ValueError('Resource integrity mismatch: '+value)
    return actual

def verify_manifest(root, *, production=False, target=None, expected_contract=None):
    root=Path(root).resolve(strict=True)
    raw=(root/'release-manifest.json').read_bytes()
    if len(raw)>16*1024*1024: raise ValueError('Release manifest exceeds quota')
    m=json.loads(raw)
    target=target or sys.platform+'-x64'
    if m.get('schema_version')!=SCHEMA or m.get('platform')!=target or target not in ('win32-x64','linux-x64') or m.get('protocol')!=PROTOCOL:
        raise ValueError('Release platform/protocol mismatch')
    build=m.get('build_id')
    if not isinstance(build,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}',build):
        raise ValueError('Release build ID is invalid')
    if type(m.get('database_schema')) is not int or m['database_schema']<1 or not re.fullmatch('[0-9a-f]{64}',m.get('contract_manifest_hash','')):
        raise ValueError('Release schema/contract is invalid')
    if expected_contract and m['contract_manifest_hash']!=expected_contract: raise ValueError('Release contract mismatch')
    if production:
        raise ValueError('Production acceptance requires external signing/security evidence; preview metadata cannot establish trust')
    files=m.get('files')
    if not isinstance(files,list) or not files or len(files)>30000: raise ValueError('Release inventory is incomplete')
    known={}
    for asset in files:
        path=asset['path']
        if path.casefold() in known: raise ValueError('Duplicate release resource')
        known[path.casefold()]=asset
        verify_asset(root,asset)
    for name,prefix in [('engine','engine/'+build+'/'),('bridge','bridge/'+build+'/'),('node','runtimes/node/')]:
        a=m.get(name,{})
        if not a.get('path','').startswith(prefix) or known.get(a['path'].casefold())!=a:
            raise ValueError('Grouped component version/inventory mismatch')
    for name in ('engine_dependencies','bridge_dependencies','native_helpers','ui_assets'):
        for a in m.get(name,[]):
            if known.get(a['path'].casefold())!=a: raise ValueError('Grouped dependency is absent from inventory')
    contract=known.get('contracts/manifest.json')
    if not contract or contract['sha256']!=m['contract_manifest_hash']:
        raise ValueError('Grouped contract manifest mismatch')
    return m

def installed_root():
    if not getattr(sys,'frozen',False): return None
    root=Path(sys.executable).resolve().parents[2]
    verify_manifest(root)
    return root
