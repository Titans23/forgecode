"""Verify sandbox code and resolve installed tool paths once before task admission."""
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

from forge.application.models import ContractError


_SOURCE = Path(__file__).resolve().parents[2]
ROOT = _SOURCE if (_SOURCE / 'sandbox_bridge/package.json').is_file() else Path(__file__).resolve().parents[1] / '_sandbox'


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
    tools: dict[str, Path] = field(default_factory=dict)


def verify_code_inventory(root: Path = ROOT) -> tuple[Path, str]:
    """Check shipped code bytes without claiming host tools or OS acceptance."""
    root = root.resolve(strict=True)
    manifest_path = root / 'sandbox_bridge/runtime-manifest.json'
    try:
        inventory = json.loads(manifest_path.read_text(encoding='utf-8'))
        if inventory.get('schema_version') != 'forge.bridge.runtime.v1' or not inventory.get('files'):
            unavailable('Sandbox code inventory is incomplete; run scripts/build_bridge.py')
        if sha256((root / 'package-lock.json').read_bytes()).hexdigest() != inventory.get('package_lock_sha256'):
            unavailable('Sandbox dependency lock differs; rebuild the sandbox')
        paths = {entry['path']: verify_asset(root, entry) for entry in inventory['files']}
        entry = paths['sandbox_bridge/dist/workspace-runner.js']
        if 'sandbox_bridge/dist/dispatcher.js' not in paths or 'packages/contracts/dist/index.js' not in paths:
            unavailable('Sandbox code inventory is incomplete')
        return entry, sha256(manifest_path.read_bytes()).hexdigest()
    except (OSError, ValueError, KeyError, TypeError):
        unavailable('Native sandbox code unavailable; run npm ci and scripts/build_bridge.py')


def verify_runtime(root: Path = ROOT) -> TrustedRuntime:
    """Require verified code and actual installed host tools before admission."""
    import shutil
    import subprocess
    root = root.resolve(strict=True)
    entry, manifest_hash = verify_code_inventory(root)
    try:
        node_path = shutil.which('node')
        if not node_path:
            unavailable('Node 22.13 or newer is required for the native sandbox')
        node = Path(node_path).resolve(strict=True)
        version = subprocess.run([str(node), '--version'], capture_output=True, text=True, timeout=5,
            **({'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}))
        if version.returncode or tuple(map(int, version.stdout.strip().lstrip('v').split('.'))) < (22, 13, 0):
            unavailable('Node 22.13 or newer is required for the native sandbox')
        from forge.release.toolchains import core_tool
        tools = {'git': core_tool('git'), 'ripgrep': core_tool('rg')}
        if sys.platform == 'win32':
            shell = shutil.which('pwsh')
            if not shell:
                unavailable('PowerShell 7 is required for Windows commands')
            tools['powershell'] = Path(shell).resolve(strict=True)
        elif sys.platform == 'linux':
            binary = Path('/usr/bin/bwrap')
            try:
                binary = binary.resolve(strict=True)
                info = binary.stat()
            except OSError:
                unavailable('Ubuntu native sandbox requires the system bubblewrap package (/usr/bin/bwrap)')
            if not binary.is_file() or info.st_uid != 0 or info.st_mode & 0o022:
                unavailable('The system bubblewrap executable must be root-owned and not writable by other users')
            tools['bubblewrap'] = binary
        return TrustedRuntime(root, node, entry, manifest_hash, tools=tools)
    except ContractError:
        raise
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        unavailable('Native sandbox runtime unavailable; run npm ci and scripts/build_bridge.py')


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
        from forge.sandbox.windows_worker import windows_local_app_data
        environment.update(SystemRoot=str(windows), WINDIR=str(windows), ProgramFiles=str(program_files),
                           LOCALAPPDATA=str(windows_local_app_data()), USERPROFILE=str(control),
                           PATH=str(windows / 'System32'), PATHEXT='.COM;.EXE;.BAT;.CMD')
    else:
        environment['PATH'] = '/usr/bin:/bin'
    return environment
