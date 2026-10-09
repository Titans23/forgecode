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
from forge.engine.persistence import new_id
from forge.sandbox.capabilities import unavailable_report,windows_supported
from forge.sandbox.launcher import bridge_environment, verify_runtime
from forge.sandbox.path_policy import inspect_path
from forge.sandbox.srt_backend import SrtBackend
from forge.application.models import canonical_hash, strict_loads


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


def normalize_windows_status(status):
    # Audited raw srt-win ABI; mirrors locked SDK mapUserStatus/mapWfpStatus without exposing PEM/credentials.
    wrapper = status['user']
    raw = wrapper['user']
    user = {key: raw[source] for key, source in {'provisioned': 'exists', 'sid': 'sid', 'groupExists': 'group_exists',
        'groupSid': 'group_sid', 'inBuiltinUsers': 'in_builtin_users', 'inSandboxGroup': 'in_sandbox_group',
        'hiddenFromLogon': 'hidden_from_logon'}.items() if source in raw and raw[source] is not None}
    user.update({key: wrapper[source] for key, source in {'credPresent': 'cred_present', 'markerVersion': 'marker_version',
        'realUserSid': 'real_user_sid'}.items() if source in wrapper and wrapper[source] is not None})
    if not isinstance(user.get('provisioned'), bool) or not isinstance(user.get('credPresent'), bool):
        raise ValueError('Native account status fields unavailable')
    wfp = {key: status['wfp'][source] for key, source in {'state': 'state', 'filters': 'filters',
        'portRange': 'port_range', 'userSid': 'user_sid'}.items() if source in status['wfp'] and status['wfp'][source] is not None}
    return user, wfp


def windows_status_diagnosis(runtime):
    try:
        from forge.release.processes import external_argv
        if runtime.installed:
            from forge.release.runtime import verify_manifest,verify_asset
            manifest=verify_manifest(runtime.root)
            helper=verify_asset(runtime.root,next(a for a in manifest['native_helpers'] if a['name']=='srt-win'))
        else:
            lock = json.loads((runtime.root / 'release-lock.json').read_text(encoding='utf-8'))
            helper = runtime.root / next(item['path'] for item in lock['assets'] if item['name'] == 'srt-win' and item['platform'] == 'win32-x64')
        with tempfile.TemporaryDirectory(prefix='forge-win-status-') as directory:
            process = subprocess.run(external_argv([str(helper), '--srt-win', 'status']), env=bridge_environment(Path(directory)),
                capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if process.returncode or len(process.stdout) > 65536:
            raise ValueError('Status failed')
        status = strict_loads(process.stdout)
        user, wfp = normalize_windows_status(status)
        return {'status': 'observed', 'helper': str(helper), 'helper_sha256': sha256(helper.read_bytes()).hexdigest(),
            'user': user, 'wfp': wfp, 'shared_activity': 'not_observed', 'read_only': True}
    except (OSError, ValueError, KeyError, TypeError, ContractError, subprocess.TimeoutExpired):
        return {'status': 'blocked', 'reason': 'Read-only native status unavailable', 'shared_activity': 'not_observed'}


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
        for name in ('bwrap', 'socat', 'rg', 'git', 'bash', 'sh'):
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
    return {'schema_version': 'forge.sandbox.diagnosis.v1', 'status': 'blocked', 'read_only': True,
        'host': host, 'tools': tools, 'project_toolchains':project_tools, 'system_policy': system_policy, 'kernel_probe': kernel_probe, 'runtime': runtime_info,
        'windows_status': windows_status,
        'capabilities': unavailable_report(backend_version='0.0.78', reason='Native boundary verification has not run').value,
        'active_sessions': {'state': 'not_observed', 'count': None}, 'cleanup': {'state': 'not_observed'},
        'workspace_tools_executed': False, 'automatic_repair': False,
        'repair_steps': (['Use Main native setup confirmation on Windows 10 x64 build 19045 or later; do not automatically refresh existing shared credentials or uninstall shared SRT.',
            'Bundled PowerShell/Git/ripgrep and NTFS are required. Use the ForgeCode installer to restore missing application assets; DNS isolation is unavailable.']
            if sys.platform == 'win32' else ['Use the ForgeCode deb installation flow to resolve missing system dependencies.',
            'The installer owns application-specific namespace policy; do not disable AppArmor or change global sysctls.'])}


async def workspace_diagnosis(path):
    token = inspect_path(str(Path(path).absolute()), absolute=True)
    info = token.path.stat()
    if not token.path.is_dir():
        raise ContractError('Workspace must be a directory')
    workspace = {'id': new_id('ws'), 'canonical_path': str(token.path), 'file_identity': f'{info.st_dev}:{info.st_ino}'}
    report = system_diagnosis()
    with tempfile.TemporaryDirectory(prefix='forge-doctor-') as directory:
        backend = SrtBackend(workspace, {'engine_epoch': new_id('epoch'), 'sandbox_session_id': new_id('sandbox'),
            'execution_id': None}, Path(directory) / 'bridge')
        try:
            report['capabilities'] = (await backend.probe()).value
            report['workspace'] = {'path': str(token.path), 'file_identity': workspace['file_identity'],
                'mode': 'inspect-only', 'filesystem_device': info.st_dev}
        finally:
            await backend.aclose()
    return report


async def verify_linux(output: Path, *, allowed_endpoint=None):
    report = system_diagnosis()
    if not report['host']['supported_linux']:
        return {'schema_version': 'forge.native.acceptance.v1', 'status': 'blocked',
            'reason': 'Supported Ubuntu native runner is unavailable', 'checks': [],
            'eligible_for_native_pass': False, 'diagnosis': report}
    if report['runtime']['status'] != 'pass' or any(item['status'] != 'pass' for item in report['tools'].values()):
        return {'schema_version': 'forge.native.acceptance.v1', 'status': 'blocked',
            'reason': 'Fixed trusted native dependencies are unavailable', 'checks': [],
            'eligible_for_native_pass': False, 'diagnosis': report}
    if report['kernel_probe']['status'] != 'pass':
        return {'schema_version': 'forge.native.acceptance.v1', 'status': 'blocked', 'checks': [],
            'reason': 'Kernel/system policy blocked the actual fixed bwrap user namespace probe',
            'eligible_for_native_pass': False, 'diagnosis': report}
    return await run_native_fixture(output, report, 'linux', allowed_endpoint=allowed_endpoint)


async def verify_windows(output: Path, *, allowed_endpoint=None):
    report = system_diagnosis()
    if not report['host']['supported_windows']:
        return {'schema_version': 'forge.native.acceptance.v1', 'status': 'blocked',
            'reason': 'Supported Windows 10 x64 build 19045 or later native runner is unavailable', 'checks': [],
            'eligible_for_native_pass': False, 'diagnosis': report}
    if report['runtime']['status'] != 'pass' or report['tools'].get('pwsh', {}).get('status') != 'pass' or windows_volume(output.parent)['status'] != 'pass':
        return {'schema_version': 'forge.native.acceptance.v1', 'status': 'blocked',
            'reason': 'Fixed runtime, PowerShell 7 or native NTFS evidence directory unavailable', 'checks': [],
            'eligible_for_native_pass': False, 'diagnosis': report}
    status = report['windows_status']
    if status.get('status') != 'observed' or not status.get('user', {}).get('provisioned') or not status['user'].get('credPresent'):
        return {'schema_version': 'forge.native.acceptance.v1', 'status': 'blocked',
            'reason': 'SRT native setup unavailable; administrator installation has not been invoked', 'checks': [],
            'eligible_for_native_pass': False, 'diagnosis': report}
    return await run_native_fixture(output, report, 'windows', allowed_endpoint=allowed_endpoint)


# Kept as the native acceptance helper's public name.
from forge.sandbox.windows_worker import grant_controller_access as prepare_windows_fixture


async def run_native_fixture(output, report, target, *, allowed_endpoint=None):
    runtime = verify_runtime()
    # Retain this synthetic fixture for evidence/residual inspection, including failed initialization.
    # Evidence commonly lives beneath the development installation. The actual
    # workspace/control fixture must not: production rejects overlapping roots.
    directory = Path(tempfile.mkdtemp(prefix=f'forge-native-{target}-'))
    owner = {'engine_epoch': new_id('epoch'), 'sandbox_session_id': new_id('sandbox'), 'execution_id': None}
    lease = None
    options = {'allowed_endpoint': allowed_endpoint}
    if target == 'windows':
        prepare_windows_fixture(directory)
        from forge.sandbox.windows_worker import WindowsWorkerLease, windows_worker_root
        worker_root = windows_worker_root()
        options['worker_root'] = str(worker_root)
        binding = canonical_hash({'owner': owner, 'fixture': str(directory), 'manifest_hash': runtime.manifest_hash, 'options': options})
        try:
            lease = WindowsWorkerLease(worker_root, owner, binding)
        except ContractError as error:
            return {'schema_version': 'forge.native.acceptance.v1', 'status': 'blocked', 'checks': [],
                'reason': str(error), 'eligible_for_native_pass': False, 'diagnosis': report}
    try:
        return await run_native_process(runtime, directory, owner, report, target, options, lease)
    finally:
        if lease:
            lease.close()  # Unknown/abnormal completion preserves marker; only measured clean can release it.


async def run_native_process(runtime, directory, owner, report, target, options, lease):
    from forge.release.processes import external_argv, external_options
    if runtime.installed:
        from forge.release.runtime import verify_manifest,verify_asset
        manifest=verify_manifest(runtime.root)
        entry=verify_asset(runtime.root,next(a for a in manifest['bridge_dependencies'] if a['path'].endswith(f'/sandbox_bridge/dist/verify-{target}.js')))
    else:entry=runtime.root/f'sandbox_bridge/dist/verify-{target}.js'
    process = await asyncio.create_subprocess_exec(*external_argv([str(runtime.node), str(entry),
        '--fixture', str(directory), '--owner', json.dumps(owner)]), cwd=runtime.root,
        env=bridge_environment(directory / 'control'), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=1048577,**external_options())
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(options).encode()), 120)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return {'schema_version': 'forge.native.acceptance.v1', 'status': 'blocked', 'checks': [],
            'reason': 'Native verifier timed out; exact owned verifier stopped, descendant cleanup requires reconciliation',
            'eligible_for_native_pass': False, 'fixture': str(directory), 'owner': owner, 'diagnosis': report}
    try:
        result = json.loads(stdout)
        if result['schema_version'] != 'forge.native.acceptance.v1' or result['status'] not in ('pass', 'blocked', 'fail') or not isinstance(result['checks'], list):
            raise ValueError('Invalid native report')
        if result['status'] == 'pass' and (process.returncode or not result['checks'] or any(item['status'] != 'pass' for item in result['checks'])):
            raise ValueError('Unproven native pass')
    except (ValueError, KeyError):
        return {'schema_version': 'forge.native.acceptance.v1', 'status': 'fail', 'checks': [],
                'reason': 'Native verifier did not return a valid nonempty acceptance report', 'eligible_for_native_pass': False,
                'diagnostic_stderr_bytes': len(stderr), 'diagnosis': report}
    result.update(diagnosis=report, fixture=str(directory), diagnostic_stderr_bytes=len(stderr), runtime_manifest_hash=runtime.manifest_hash)
    if lease:
        lease.close(result.get('cleanup'))
    return result


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
