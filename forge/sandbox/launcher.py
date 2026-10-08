"""Verify the installation before creating a Node process; never resolve it from PATH."""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

from forge.application.models import ContractError


ROOT = Path(sys.executable).resolve().parents[2] if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]


def unavailable(message):
    raise ContractError(message, kind='SANDBOX_UNAVAILABLE', code=-32010)


def verify_asset(root: Path, asset: dict) -> Path:
    root = root.resolve(strict=True)
    value = asset.get('path')
    if not isinstance(value, str) or not value or '\\' in value or Path(value).is_absolute():
        unavailable('Asset path is outside trusted root')
    lexical = root / value
    if '..' in Path(value).parts:
        unavailable('Asset path is outside trusted root')
    try:
        resolved = lexical.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            unavailable('Asset path is outside trusted root')
        if sha256(resolved.read_bytes()).hexdigest() != asset.get('sha256'):
            unavailable('Asset integrity mismatch')
    except OSError:
        unavailable('Required installed asset is unavailable')
    return resolved


@dataclass(frozen=True)
class TrustedRuntime:
    root: Path
    node: Path
    entry: Path
    manifest_hash: str
    installed: bool = False


def verify_runtime(root: Path = ROOT) -> TrustedRuntime:
    root = root.resolve(strict=True)
    if (root/'release-manifest.json').is_file() and (root/'engine').is_dir():
        from forge.release.runtime import verify_manifest, verify_asset as installed_asset
        try:
            manifest=verify_manifest(root)
            return TrustedRuntime(root,installed_asset(root,manifest['node']),installed_asset(root,manifest['bridge']),
                sha256((root/'release-manifest.json').read_bytes()).hexdigest(), installed=True)
        except (OSError,ValueError,KeyError,TypeError):
            unavailable('Installed runtime group is unavailable or invalid')
    if getattr(sys,'frozen',False): unavailable('Installed runtime group is required')
    try:
        lock = json.loads((root / 'release-lock.json').read_text(encoding='utf-8'))
        if lock.get('resolution_status') != 'resolved':
            unavailable('Release dependencies are unresolved')
        target = 'win32-x64' if sys.platform == 'win32' else 'linux-x64' if sys.platform == 'linux' else None
        if target is None:
            unavailable('Unsupported native runtime target')
        assets = {entry['name']: verify_asset(root, entry) for entry in lock['assets']
                  if entry['platform'] in (target, 'all')}
        for name, hash_value in lock['lockfile_hashes'].items():
            verify_asset(root, {'path': name, 'sha256': hash_value})
        manifest = assets['bridge-runtime-manifest']
        inventory = json.loads(manifest.read_text(encoding='utf-8'))
        if inventory.get('schema_version') != 'forge.bridge.runtime.v1' or not inventory.get('files'):
            unavailable('Installed Bridge code inventory is incomplete')
        paths = {entry['path']: verify_asset(root, entry) for entry in inventory['files']}
        entry = paths['sandbox_bridge/dist/main.js']
        # Every JS dependency is inventoried, not just the top-level entry script.
        if 'sandbox_bridge/dist/dispatcher.js' not in paths or 'packages/contracts/dist/index.js' not in paths:
            unavailable('Installed Bridge code inventory is incomplete')
        return TrustedRuntime(root, assets['node'], entry, sha256(manifest.read_bytes()).hexdigest())
    except (OSError, ValueError, KeyError, TypeError):
        unavailable('Trusted runtime manifest is unavailable or invalid')


def bridge_environment(control: Path, *, source=None) -> dict:
    """Rebuild, rather than filter, the management environment. No proxy/key inheritance."""
    environment = {'HOME': str(control), 'TMPDIR': str(control), 'TMP': str(control), 'TEMP': str(control),
                   'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
    if sys.platform == 'win32':
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer)):
            unavailable('Cannot identify Windows system directory')
        windows = Path(buffer.value)
        program_files = Path(windows.anchor) / 'Program Files'
        environment.update(SystemRoot=str(windows), WINDIR=str(windows), ProgramFiles=str(program_files),
                           USERPROFILE=str(control), PATH=str(windows / 'System32'))
    else:
        environment['PATH'] = '/usr/bin:/bin'
    return environment
