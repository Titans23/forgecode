"""Read-only system diagnosis and explicitly invoked synthetic native acceptance."""
import argparse
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

from forge.application.models import ContractError
from forge.sandbox.capabilities import unavailable_report,windows_supported
from forge.sandbox.launcher import bridge_environment, verify_runtime
from forge.sandbox.path_policy import inspect_path


def linux_release(text):
    fields = {}
    duplicate = False
    for line in text.splitlines():
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key in fields:
            duplicate = True
        if value.startswith('"') != value.endswith('"') or value.startswith("'") != value.endswith("'"):
            duplicate = True
        fields[key] = value.strip('"').strip("'")
    return {'distribution': fields.get('ID'), 'version': fields.get('VERSION_ID'),
            'supported': not duplicate and fields.get('ID') == 'ubuntu' and fields.get('VERSION_ID') in ('22.04', '24.04')}


def windows_volume(path):
    if sys.platform != 'win32':
        return {'status': 'not_applicable'}
    import ctypes
    filesystem = ctypes.create_unicode_buffer(32)
    flags = ctypes.c_uint32()
    maximum = ctypes.c_uint32()
    success = ctypes.windll.kernel32.GetVolumeInformationW(str(Path(path).absolute().anchor), None, 0, None,
        ctypes.byref(maximum), ctypes.byref(flags), filesystem, len(filesystem))
    return {'status': 'pass' if success and filesystem.value == 'NTFS' else 'blocked',
        'filesystem': filesystem.value if success else 'not_observed'}


def windows_status_diagnosis(runtime):
    return {'status': 'not_applicable', 'reason': 'Strict runtime removed; workspace-write needs no SRT account or WFP setup',
            'shared_activity': 'not_observed', 'read_only': True}


def system_diagnosis():
    host = {'system': platform.system(), 'build': platform.platform(), 'architecture': platform.machine(),
            'supported_linux': False, 'supported_windows': windows_supported()}
    tools = {}
    system_policy = {}
    kernel_probe = {'status': 'not_run', 'reason': 'Supported native namespace probe unavailable'}
    if sys.platform == 'linux':
        try:
            host.update(linux_release(Path('/etc/os-release').read_text(encoding='utf-8')))
            host['supported_linux'] = host['supported'] and platform.machine() == 'x86_64'
        except OSError:
            host['distribution'] = 'unavailable'
        for name in ('bwrap', 'rg', 'git', 'bash', 'sh'):
            path = Path('/bin' if name in ('bash', 'sh') else '/usr/bin') / name
            try:
                actual = path.resolve(strict=True)
                info = actual.stat()
                trusted = info.st_uid == 0 and not info.st_mode & 0o022 and os.access(actual, os.X_OK)
                tools[name] = {'path': str(actual), 'status': 'pass' if trusted else 'blocked',
                               'sha256': sha256(actual.read_bytes()).hexdigest(), 'identity': f'{info.st_dev}:{info.st_ino}'}
            except OSError:
                tools[name] = {'path': str(path), 'status': 'blocked', 'reason': 'Fixed system executable unavailable'}
        for name, path in {'unprivileged_userns_clone': '/proc/sys/kernel/unprivileged_userns_clone',
            'max_user_namespaces': '/proc/sys/user/max_user_namespaces',
            'apparmor_restrict_unprivileged_userns': '/proc/sys/kernel/apparmor_restrict_unprivileged_userns',
            'apparmor_enabled': '/sys/module/apparmor/parameters/enabled'}.items():
            try:
                system_policy[name] = Path(path).read_text(encoding='ascii').strip()[:64]
            except OSError:
                system_policy[name] = 'not_observed'
        if host['supported_linux'] and tools['bwrap']['status'] == 'pass':
            try:
                with tempfile.TemporaryDirectory(prefix='forge-namespace-probe-') as directory:
                    probe = subprocess.run([tools['bwrap']['path'], '--unshare-user', '--ro-bind', '/', '/', '--', '/usr/bin/true'],
                        env=bridge_environment(Path(directory)), capture_output=True, timeout=5)
                kernel_probe = {'status': 'pass' if probe.returncode == 0 else 'blocked', 'exit_code': probe.returncode,
                                'stderr_bytes': len(probe.stderr), 'diagnostic_sha256': sha256(probe.stderr).hexdigest()}
            except (OSError, subprocess.TimeoutExpired):
                kernel_probe = {'status': 'blocked', 'reason': 'Fixed native user namespace probe failed'}
    try:
        runtime = verify_runtime()
        runtime_info = {'status': 'pass', 'node': str(runtime.node), 'node_sha256': sha256(runtime.node.read_bytes()).hexdigest(),
                        'manifest_hash': runtime.manifest_hash}
    except ContractError as error:
        runtime_info = {'status': 'blocked', 'reason': str(error)}
    windows_status = {'status': 'not_applicable'}
    if sys.platform == 'win32' and runtime_info['status'] == 'pass':
        windows_status = windows_status_diagnosis(runtime)
        with tempfile.TemporaryDirectory(prefix='forge-win-tools-') as directory:
            environment = bridge_environment(Path(directory))
        for name, key in [('pwsh', 'powershell'), ('git', 'git'), ('rg', 'ripgrep')]:
            tool = runtime.tools.get(key)
            tools[name] = ({'status': 'pass', 'path': str(tool), 'sha256': sha256(tool.read_bytes()).hexdigest()}
                           if tool else {'status': 'blocked', 'reason': 'Bundled application tool is unavailable'})
        host['system_volume'] = windows_volume(Path(environment['SystemRoot']))
    from forge.release.toolchains import discover_toolchains
    project_tools=discover_toolchains()
    capabilities = unavailable_report(backend_version='unprobed', reason='Workspace boundary verification runs at session initialization').value
    capabilities['backend'] = 'dsh-windows-acl' if sys.platform == 'win32' else 'bubblewrap' if sys.platform == 'linux' else 'unsupported'
    return {'schema_version': 'forge.sandbox.diagnosis.v1', 'status': 'blocked', 'read_only': True,
        'host': host, 'tools': tools, 'project_toolchains':project_tools, 'system_policy': system_policy, 'kernel_probe': kernel_probe, 'runtime': runtime_info,
        'windows_status': windows_status,
        'capabilities': capabilities, 'execution_mode': 'workspace-write', 'strict_execution': 'unavailable',
        'active_sessions': {'state': 'not_observed', 'count': None}, 'cleanup': {'state': 'not_observed'},
        'workspace_tools_executed': False, 'automatic_repair': False,
        'repair_steps': (['Select workspace-write using Main native confirmation; strict execution is unavailable in this build.',
            'Bundled PowerShell/Git/ripgrep and NTFS are required. Use the ForgeCode installer to restore missing application assets; DNS isolation is unavailable.']
            if sys.platform == 'win32' else ['Use the ForgeCode deb installation flow to resolve missing system dependencies.',
            'The installer owns application-specific namespace policy; do not disable AppArmor or change global sysctls.'])}


async def workspace_diagnosis(path):
    token = inspect_path(str(Path(path).absolute()), absolute=True)
    info = token.path.stat()
    if not token.path.is_dir():
        raise ContractError('Workspace must be a directory')
    report = system_diagnosis()
    report['workspace'] = {'path': str(token.path), 'file_identity': f'{info.st_dev}:{info.st_ino}',
        'mode': 'inspect-only', 'filesystem_device': info.st_dev}
    return report


async def verify_linux(output: Path, *, allowed_endpoint=None):
    report = system_diagnosis()
    return {'schema_version': 'forge.native.acceptance.v1', 'status': 'blocked', 'checks': [],
        'reason': 'Strict runtime removed; use workspace-write acceptance' if report['host']['supported_linux']
                  else 'Supported Ubuntu native runner is unavailable',
        'eligible_for_native_pass': False, 'diagnosis': report}


async def verify_windows(output: Path, *, allowed_endpoint=None):
    return {'schema_version': 'forge.native.acceptance.v1', 'status': 'blocked', 'checks': [],
        'reason': 'Strict runtime removed; use workspace-write acceptance',
        'eligible_for_native_pass': False, 'diagnosis': system_diagnosis()}


from forge.sandbox.windows_worker import grant_controller_access as prepare_windows_fixture


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json',action='store_true',help='Emit the read-only diagnosis as JSON (also the default)')
    parser.add_argument('--system', action='store_true')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--native-linux', action='store_true')
    parser.add_argument('--native-windows', action='store_true')
    parser.add_argument('--allowed-endpoint', help='Explicitly authorized HTTP canary URL; no default public traffic')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.native_linux and args.native_windows:
        parser.error('Choose exactly one native platform')
    if (args.native_linux or args.native_windows) and not args.output:
        parser.error('Native verification requires --output for retained synthetic evidence')
    if args.allowed_endpoint and not (args.native_linux or args.native_windows):
        parser.error('--allowed-endpoint requires native verification')
    if args.allowed_endpoint:
        from urllib.parse import urlsplit
        from forge.sandbox.policy import normalize_domain
        try:
            url = urlsplit(args.allowed_endpoint)
            normalize_domain(url.hostname or '')
            if url.scheme != 'http' or url.username or url.password or url.query or url.fragment:
                raise ValueError('Expected explicit credential-free controlled HTTP canary URL')
        except (ValueError, ContractError):
            parser.error('Invalid authorized canary endpoint')
    if args.native_linux:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        result = asyncio.run(verify_linux(args.output, allowed_endpoint=args.allowed_endpoint))
    elif args.native_windows:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        result = asyncio.run(verify_windows(args.output, allowed_endpoint=args.allowed_endpoint))
    elif args.workspace:
        result = asyncio.run(workspace_diagnosis(args.workspace))
    else:
        result = system_diagnosis()
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'pass' else 2 if result['status'] == 'blocked' else 1


if __name__ == '__main__':
    raise SystemExit(main())
