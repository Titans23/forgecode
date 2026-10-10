"""Verify the complete installed group before loading any private component."""
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

SCHEMA='forge.release.manifest.v1'
PROTOCOL='forge.engine.v1'

def relative_tool_path(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value or '\0' in value
            or PurePosixPath(value).is_absolute() or any(part in ('', '.', '..') or part.endswith((' ', '.'))
            or re.fullmatch(r'(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part)
            for part in value.split('/'))):
        raise ValueError('Invalid tool inventory path')
    return value


def tool_inventory(root, bundle):
    path = relative_tool_path(bundle['inventory'])
    inventory_path = Path(root) / path
    if not inventory_path.resolve(strict=True).is_relative_to(Path(root).resolve()):
        raise ValueError('Tool inventory escapes installation')
    raw = inventory_path.read_bytes()
    if len(raw) > 4 * 1024 * 1024 or sha256(raw).hexdigest() != bundle['inventory_sha256']:
        raise ValueError('Tool inventory integrity mismatch')
    inventory = json.loads(raw)
    if inventory.get('schema_version') != 'forge.tool.inventory.v1':
        raise ValueError('Invalid tool inventory')
    entries = inventory.get('files')
    if not isinstance(entries, list) or not entries or len(entries) > 10000:
        raise ValueError('Invalid tool inventory size')
    names = [relative_tool_path(entry['path']).casefold() for entry in entries]
    if len(set(names)) != len(names) or bundle['entry'] not in {entry['path'] for entry in entries}:
        raise ValueError('Tool inventory conflict or missing entry')
    return entries


def tool_directory(root, bundle):
    name, version = bundle['name'], bundle['version']
    if name not in ('powershell', 'git', 'ripgrep') or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){2,3}', version) or bundle['platform'] != 'win32-x64':
        raise ValueError('Unsupported private tool identity')
    expected = '.local/release-runtime/' + name + '-' + version + '-win32-x64'
    if bundle['path'] != expected:
        raise ValueError('Tool destination differs from owned layout')
    root = Path(root).resolve(strict=True)
    path = root / expected
    if not path.resolve().is_relative_to(root / '.local/release-runtime') or path.is_symlink() or path.is_junction():
        raise ValueError('Tool directory escapes private assets')
    return path


def _verify_tool_tree(directory, entries):
    directory = Path(directory)
    if directory.is_symlink() or directory.is_junction():
        raise ValueError('Tool inventory cannot contain links')
    actual = set()
    for current, directories, names in os.walk(directory, followlinks=False):
        for name in [*directories, *names]:
            path = Path(current) / name
            if path.is_symlink() or path.is_junction():
                raise ValueError('Tool inventory cannot contain links')
        actual.update((Path(current) / name).relative_to(directory).as_posix() for name in names)
    if actual != {entry['path'] for entry in entries}:
        raise ValueError('Tool directory inventory mismatch')


def verify_tool_files(directory, entries):
    _verify_tool_tree(directory, entries)
    root = Path(directory).resolve(strict=True)
    for entry in entries:
        _verify_asset(root, entry)


def verify_tool_bundle(root, bundle):
    directory = tool_directory(root, bundle)
    entries = tool_inventory(root, bundle)
    verify_tool_files(directory, entries)
    return directory / bundle['entry']


def _installed_tool_group(root, manifest, name):
    if manifest.get('schema_version') != SCHEMA or manifest.get('platform') != sys.platform+'-x64' or manifest.get('protocol') != PROTOCOL:
        raise ValueError('Installed tool platform/protocol mismatch')
    files = manifest.get('files')
    if not isinstance(files, list) or not files or len(files) > 30000:
        raise ValueError('Installed tool inventory is incomplete')
    tool = manifest.get('tools', {}).get(name, {})
    prefix = 'runtimes/powershell/' if name == 'powershell' else 'tools/' + name + '/'
    if name not in ('powershell', 'git', 'ripgrep') or not relative_tool_path(tool.get('root')).startswith(prefix):
        raise ValueError('Invalid installed tool group')
    directory = tool['root'] + '/'
    entry = tool.get('entry', {})
    if not entry.get('path', '').startswith(directory) or entry not in files:
        raise ValueError('Installed tool entry is absent from inventory')
    entries = [{**a, 'path': a['path'][len(directory):]} for a in files if a['path'].startswith(directory)]
    return Path(root) / tool['root'], entries, entry


def verify_installed_tool(root, manifest, name):
    """Verify just this executable's full loadable closure before a core-tool call."""
    directory, entries, entry = _installed_tool_group(root, manifest, name)
    verify_tool_files(directory, entries)
    return verify_asset(root, entry)

def digest(path):
    h=sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(1024*1024): h.update(chunk)
    return h.hexdigest()

def verify_asset(root,asset):
    root=Path(root).resolve(strict=True)
    return _verify_asset(root,asset)

def _verify_asset(root,asset):
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

def verify_manifest(root, *, production=False, target=None, expected_contract=None, engine_worker=False):
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
        # A fixed worker loads the frozen Engine closure, not Electron, Bridge,
        # private Node or tool bundles. Service startup still verifies everything.
        if not engine_worker or path.startswith('engine/') or path == 'contracts/manifest.json':
            _verify_asset(root,asset)
        else:
            relative_tool_path(path)
    for name,prefix in [('engine','engine/'+build+'/'),('bridge','bridge/'+build+'/'),('node','runtimes/node/')]:
        a=m.get(name,{})
        if not a.get('path','').startswith(prefix) or known.get(a['path'].casefold())!=a:
            raise ValueError('Grouped component version/inventory mismatch')
    for name in ('engine_dependencies','bridge_dependencies','native_helpers','ui_assets'):
        for a in m.get(name,[]):
            if known.get(a['path'].casefold())!=a: raise ValueError('Grouped dependency is absent from inventory')
    for name in (() if engine_worker else m.get('tools', {})):
        directory, entries, _ = _installed_tool_group(root, m, name)
        # Every listed byte was verified above. Still reject extra DLLs/links,
        # but do not hash the same complete tool bundle twice per worker launch.
        _verify_tool_tree(directory, entries)
    contract=known.get('contracts/manifest.json')
    if not contract or contract['sha256']!=m['contract_manifest_hash']:
        raise ValueError('Grouped contract manifest mismatch')
    return m

def installed_root():
    if not getattr(sys,'frozen',False): return None
    root=Path(sys.executable).resolve().parents[2]
    verify_manifest(root)
    return root
