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
from forge.sandbox.capabilities import unavailable_report
from forge.sandbox.launcher import bridge_environment, verify_runtime
from forge.sandbox.path_policy import inspect_path
from forge.sandbox.srt_backend import SrtBackend


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


def system_diagnosis():
    host = {'system': platform.system(), 'build': platform.platform(), 'architecture': platform.machine(),
            'supported_linux': False}
    tools = {}
    system_policy = {}
    kernel_probe = {'status': 'not_run', 'reason': 'Supported native namespace probe unavailable'}
    if sys.platform == 'linux':
        try:
            host.update(linux_release(Path('/etc/os-release').read_text(encoding='utf-8')))
            host['supported_linux'] = host['supported'] and platform.machine() == 'x86_64'
        except OSError:
            host['distribution'] = 'unavailable'
        for name in ('bwrap', 'socat', 'rg', 'bash', 'sh'):
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
    return {'schema_version': 'forge.sandbox.diagnosis.v1', 'status': 'blocked', 'read_only': True,
        'host': host, 'tools': tools, 'system_policy': system_policy, 'kernel_probe': kernel_probe, 'runtime': runtime_info,
        'capabilities': unavailable_report(backend_version='0.0.78', reason='Native boundary verification has not run').value,
        'active_sessions': {'state': 'not_observed', 'count': None}, 'cleanup': {'state': 'not_observed'},
        'workspace_tools_executed': False, 'automatic_repair': False,
        'repair_steps': ['Install audited bubblewrap/socat/ripgrep packages if missing on supported Ubuntu.',
            'Have an administrator review the minimum application-specific namespace policy if blocked; do not disable AppArmor or change global sysctls.']}


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
    runtime = verify_runtime()
    # Retain this synthetic fixture for evidence/residual inspection, including failed initialization.
    directory = output.parent / 'native-linux-fixture'
    directory.mkdir(exist_ok=False)
    owner = {'engine_epoch': new_id('epoch'), 'sandbox_session_id': new_id('sandbox'), 'execution_id': None}
    process = await asyncio.create_subprocess_exec(str(runtime.node), str(runtime.root / 'sandbox_bridge/dist/verify-linux.js'),
        '--fixture', str(directory), '--owner', json.dumps(owner), cwd=runtime.root,
        env=bridge_environment(directory / 'control'), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=1048577)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps({'allowed_endpoint': allowed_endpoint}).encode()), 120)
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
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--system', action='store_true')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--native-linux', action='store_true')
    parser.add_argument('--allowed-endpoint', help='Explicitly authorized HTTP canary URL; no default public traffic')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.native_linux and not args.output:
        parser.error('--native-linux requires --output for retained synthetic evidence')
    if args.allowed_endpoint and not args.native_linux:
        parser.error('--allowed-endpoint requires --native-linux')
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
