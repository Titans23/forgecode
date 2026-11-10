"""DSH session adapter with Forge protocol, budget and cleanup accounting."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

from forge.protocol import ContractError, canonical_hash, validate
from forge.sandbox.capabilities import CapabilityReport, FEATURES
from forge.sandbox.launcher import ROOT, verify_runtime
from forge.sandbox.path_policy import inspect_path
from forge.sandbox.policy import compile_policy
from forge.storage_paths import private_storage_path


def require_workspace_policy(policy):
    validate('sandbox-policy', policy)
    if (policy['filesystem']['read_mode'] != 'host_default' or policy['network']['mode'] != 'inherit'
            or policy['network']['allowed_domains'] or policy['network']['dns_isolation_required']
            or any(policy['limits'][key] and policy['limits'][key]['enforcement'] == 'hard_required'
                   for key in ('memory_bytes', 'disk_bytes', 'pids'))):
        raise ContractError('Workspace-write cannot satisfy requested read/network/hard-resource isolation',
                            kind='CAPABILITY_UNSATISFIED', code=-32010)


class WorkspaceWriteBackend:
    mode = 'workspace-write'

    def __init__(self, workspace, owner, control_root, *, trusted_root=ROOT, mode='workspace-write', diagnostic_root=None):
        if mode not in ('read-only', 'workspace-write'):
            raise ValueError('Invalid native mode')
        self.mode = mode
        validate('cleanup-report', {'owner': owner, 'state': 'unknown', 'remaining_processes': 0,
                                   'diagnostic_refs': [], 'completed_at_utc': None})
        self.workspace, self.owner = dict(workspace), dict(owner)
        self.control_root, self.trusted_root = Path(control_root).absolute(), Path(trusted_root)
        self.diagnostic_root = Path(diagnostic_root).absolute() if diagnostic_root is not None else None
        self.outputs = asyncio.Queue(maxsize=8)
        self._prepared = None
        self._preparing = None
        self._closing = None
        self._executions = {}
        self._retained = 0

    @property
    def policy_hash(self):
        return self._prepared.sha256 if self._prepared is not None else None

    async def prepare(self, policy):
        if self._closing or self._preparing or self._prepared:
            raise ContractError('Native session initialization is single-use', kind='SANDBOX_UNAVAILABLE', code=-32010)
        require_workspace_policy(policy)
        self._preparing = asyncio.create_task(self._prepare(policy))
        return await asyncio.shield(self._preparing)

    async def _prepare(self, policy):
        if policy.get('execution_mode', self.mode) != self.mode:
            raise ContractError('Native execution mode differs from the requested policy',
                                kind='CAPABILITY_UNSATISFIED', code=-32010)
        if sys.platform not in ('win32', 'linux'):
            raise ContractError('Workspace-write requires Windows or Linux', kind='SANDBOX_UNAVAILABLE', code=-32010)
        snapshot = compile_policy(policy, self.workspace, control_roots=(self.control_root,))
        self.runtime = verify_runtime(self.trusted_root)
        self.backend_name = 'dsh-local'
        self.backend_version = '0.2.1-alpha.1'
        if self.mode == 'read-only' and snapshot.value['filesystem']['write_roots']:
            raise ContractError('Read-only cannot grant writable roots', kind='CAPABILITY_UNSATISFIED', code=-32010)
        inspect_path(str(self.control_root), absolute=True).assert_current()
        if self.diagnostic_root is not None:
            self.control_root.parent.mkdir(parents=True, exist_ok=True)
            inspect_path(self.diagnostic_root, absolute=True).assert_current()
            self.diagnostic_root.mkdir(parents=True, exist_ok=False)
            self._diagnostic_anchor = inspect_path(self.diagnostic_root, absolute=True)
            (self.diagnostic_root / 'control-location.json').write_text(json.dumps({
                'control_root': str(self.control_root), 'owner': self.owner,
                'policy_hash': snapshot.sha256, 'state': 'preparing'}, indent=2), encoding='utf-8')
        self.control_root.mkdir(parents=True, exist_ok=False, mode=0o700)
        self._control_anchor = inspect_path(self.control_root, absolute=True)
        if sys.platform == 'win32':
            from forge.sandbox.windows_worker import grant_controller_access
            grant_controller_access(self.control_root)
        self._policy = snapshot
        from forge.sandbox.dsh_session import DshSession
        self._controller = DshSession(self.runtime, self.workspace, self.mode, self.owner['sandbox_session_id'],
            snapshot.value['filesystem']['write_roots'], self.control_root)
        await self._controller.start()
        self._prepared = snapshot
        return self._admission_result(verified=False)

    async def probe(self):
        """Explicit evaluator/diagnostic check; ordinary coding commands skip it."""
        if self._prepared is None or self._closing is not None:
            raise ContractError('No open DSH session to probe', kind='SANDBOX_UNAVAILABLE')
        snapshot = self._prepared
        policy = snapshot.value
        root = Path(self.workspace['canonical_path'])
        identity = 'exec-' + str(uuid4())
        write_roots = snapshot.value['filesystem']['write_roots']
        inside = Path(write_roots[0]) / ('.forge-canary-' + identity) if write_roots else root / ('.forge-canary-' + identity)
        outside = self.control_root / 'denied-canary'
        script = "const fs=require('node:fs');const [a,b]=process.argv.slice(1);fs.writeFileSync(a,'probe',{flag:'wx'});let denied=false;try{fs.writeFileSync(b,'escape',{flag:'wx'});}catch{denied=true;}fs.writeFileSync(require('node:path').join(process.env.TEMP || process.env.TMPDIR,'probe-temp'),'temp');console.log(JSON.stringify({inside:fs.readFileSync(a,'utf8')==='probe',outsideDenied:denied,stdin:fs.readFileSync(0,'utf8')}));"
        expected = {'inside': True, 'outsideDenied': True, 'stdin': 'forge-probe'}
        if self.mode == 'read-only' or not write_roots:
            inside.write_text('probe', encoding='utf-8')
            script = "const fs=require('node:fs');const [a,b]=process.argv.slice(1);const denied=f=>{try{f();return false;}catch{return true;}};console.log(JSON.stringify({insideRead:fs.readFileSync(a,'utf8')==='probe',insideDenied:denied(()=>fs.writeFileSync(a,'escape')),deleteDenied:denied(()=>fs.unlinkSync(a)),outsideDenied:denied(()=>fs.writeFileSync(b,'escape',{flag:'wx'})),stdin:fs.readFileSync(0,'utf8')}));"
            expected = {'insideRead': True, 'insideDenied': True, 'deleteDenied': True, 'outsideDenied': True, 'stdin': 'forge-probe'}
        command = {'mode': 'argv', 'argv': [str(self.runtime.node), '-e', script, str(inside), str(outside)],
            'cwd': str(root), 'environment': {}, 'deadline_utc': (datetime.now(timezone.utc)+timedelta(seconds=min(25, policy['limits']['wall_time_seconds']))).isoformat().replace('+00:00','Z'),
            'output_limit_bytes': min(65536, policy['limits']['command_output_bytes']), 'stdin_base64': base64.b64encode(b'forge-probe').decode()}
        if self._closing: raise ContractError('Session closed before native probe',kind='SANDBOX_UNAVAILABLE',code=-32010)
        execution = self._accept(identity, command, publish=False)
        try:
            await asyncio.shield(execution['task'])
            proof = json.loads(execution['captured']['stdout'])
            if execution['exit_code'] != 0 or proof != expected or execution['cleanup']['state'] != 'clean':
                raise ValueError('Native write/stdin/cleanup boundary probe failed')
            evidence = {'backend': self.backend_name, 'version': self.backend_version, 'probe': proof, 'cleanup': execution['cleanup'],
                'workspace_grant': 'none' if self.mode == 'read-only' else 'standing; intentionally retained across sessions' if sys.platform == 'win32' else 'DSH selected per-process boundary', 'mode': self.mode, 'read_network_isolation': False}
            (self.control_root/'boundary.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        except (ValueError, KeyError) as error:
            (self.control_root/'boundary-failure.json').write_text(json.dumps({
                'mode': self.mode, 'exit_code': execution['exit_code'], 'error': execution.get('error'),
                'stdout': execution['captured']['stdout'].decode('utf-8', errors='replace'),
                'stderr': execution['captured']['stderr'].decode('utf-8', errors='replace'),
                'cleanup': execution['cleanup']}, indent=2), encoding='utf-8')
            detail = execution['captured']['stderr'].decode('utf-8', errors='replace').strip()
            detail = detail or execution.get('error') or str(error)
            detail = ''.join(char if char.isprintable() else ' ' for char in detail)[:1024]
            message = f'Native {self.mode} probe failed; command not started.\nBackend: {detail}'
            if 'SetNamedSecurityInfoW failed (Win32 5)' in detail and 'grantWrite(' in detail:
                message += ('\nWindows denied sandbox ACL/low-integrity label setup. DSH requires directory '
                            'ownership and WRITE_OWNER; permission to edit files alone is insufficient. '
                            'Check the reported directory permissions, or explicitly use '
                            'forge --sandbox full-access --approval supervised for host execution '
                            'without OS write confinement.')
            message += f'\nDiagnostics: {self.diagnostic_root or self.control_root}'
            raise ContractError(message, kind='SANDBOX_UNAVAILABLE', code=-32010) from error
        finally:
            if execution.get('cleanup',{}).get('state') == 'clean':
                inside.unlink(missing_ok=True)
                outside.unlink(missing_ok=True)
        if self._closing:
            raise ContractError('Native session closed during initialization', kind='SANDBOX_UNAVAILABLE', code=-32010)
        return self._admission_result(verified=True)

    def _admission_result(self, *, verified):
        verification = {name: {'status': 'unsupported', 'evidence_refs': []} for name in FEATURES}
        if verified:
            verification['write_isolation'] = {'status': 'partial', 'evidence_refs': ['owned-native-write-canary']}
            verification['process_cleanup'] = {'status': 'verified', 'evidence_refs': ['dsh-managed-range-empty']}
        report = CapabilityReport({'platform': 'windows-native' if sys.platform == 'win32' else 'linux-native', 'backend': self.backend_name, 'backend_version': self.backend_version,
            'read_isolation': 'unavailable', 'write_isolation': False, 'direct_network_isolation': False,
            'dns_isolation': False, 'socket_isolation': False, 'process_cleanup': verified,
            'resource_enforcement': {name:'unavailable' for name in ('memory','disk','pids')}, 'readiness': 'ready',
            'issues': [('Read-only project write restriction; reads and network inherit host access' if self.mode == 'read-only' else 'Partial write restriction; reads and network inherit host access'),
                       'Ambient ACL and hard-link limitations remain; Node/libuv piped grandchildren are unsupported (use inherited or file stdio)' if sys.platform == 'win32' else 'DSH selects bubblewrap or Landlock; enforcement depends on host capability'],
            'measured_at_utc': datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), 'verification': verification})
        return {'sandbox_session_id': self.owner['sandbox_session_id'], 'policy_hash': self._prepared.sha256,
                'owner': self.owner, 'capabilities': report.value, 'state': 'ready'}

    def _accept(self, identity, command, *, publish=True):
        value = {'execution_id': identity, 'owner': {**self.owner,'execution_id': identity}, 'command': command,
            'hash': canonical_hash(command), 'state': 'accepted', 'exit_code': None, 'stdout_bytes': 0, 'stderr_bytes': 0,
            'discarded_bytes': 0, 'retained': 0, 'sequence': 0, 'captured': {'stdout':b'', 'stderr':b''}, 'publish': publish,
            'cleanup': self._report('unknown')}
        self._executions[identity] = value
        value['task'] = asyncio.create_task(self._run(value))
        return value

    async def execute(self, execution_id, command):
        validate('bridge.execute.request', {'sandbox_session_id':self.owner['sandbox_session_id'],'execution_id':execution_id,
            'command':command,'command_hash':canonical_hash(command)})
        if not self._prepared or self._closing:
            raise ContractError('No admitted native workspace session', kind='SANDBOX_UNAVAILABLE', code=-32010)
        if execution_id in self._executions:
            entry = self._executions[execution_id]
            if entry['hash'] != canonical_hash(command):
                raise ContractError('Execution identity conflicts',kind='POLICY_DENIED',code=-32010)
            return {key:entry[key] for key in ('execution_id','owner','state')} | {'reused_existing_execution': True}
        if len(self._executions) >= 4096:
            raise ContractError('Execution quota exhausted', kind='ARTIFACT_LIMIT', code=-32010)
        cwd = self._prepared.paths.authorize(command['cwd']).path
        root = Path(self.workspace['canonical_path'])
        if self._prepared.value.get('execution_mode') is None and not cwd.is_relative_to(root):
            raise ContractError('Command cwd exceeds workspace', kind='POLICY_DENIED', code=-32010)
        from forge.sandbox.policy import UNSAFE_ENV
        if any(UNSAFE_ENV.search(key) or key not in self._prepared.value['environment_keys'] for key in command['environment']):
            raise ContractError('Command environment exceeds policy', kind='POLICY_DENIED', code=-32010)
        seconds = (datetime.fromisoformat(command['deadline_utc'].replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds()
        if not 0 < seconds <= self._prepared.value['limits']['wall_time_seconds'] or command['output_limit_bytes'] > self._prepared.value['limits']['command_output_bytes']:
            raise ContractError('Command exceeds frozen limits', kind='POLICY_DENIED', code=-32010)
        entry = self._accept(execution_id, command)
        return {key:entry[key] for key in ('execution_id','owner','state')} | {'reused_existing_execution': False}

    async def _output(self, entry, stream, raw):
        entry[stream + '_bytes'] += len(raw)
        allowed = max(0, min(entry['command']['output_limit_bytes'] - entry['retained'],
                            self._policy.value['limits']['session_artifact_bytes'] - self._retained))
        retained = raw[:allowed]
        entry['discarded_bytes'] += len(raw) - len(retained)
        entry['retained'] += len(retained)
        self._retained += len(retained)
        if not entry['publish']:
            entry['captured'][stream] += retained
        elif retained:
            entry['sequence'] += 1
            frame = {'execution_id': entry['execution_id'], 'owner': entry['owner'], 'stream': stream,
                'sequence': str(entry['sequence']), 'raw_base64': base64.b64encode(retained).decode(),
                'text': retained.decode('utf-8', errors='replace'), 'encoding': 'utf-8', 'final': False}
            try:
                self.outputs.put_nowait(frame)
            except asyncio.QueueFull:
                entry['discarded_bytes'] += len(retained)
            await asyncio.sleep(0)

    async def _run(self, entry):
        identity, command = entry['execution_id'], entry['command']
        try:
            remaining = (datetime.fromisoformat(command['deadline_utc'].replace('Z', '+00:00'))
                         - datetime.now(timezone.utc)).total_seconds()
            async with asyncio.timeout(max(0, remaining)):
                entry['state'] = 'running'
                result = await self._controller.execute(identity, command,
                    lambda stream, raw: self._output(entry, stream, raw))
                entry['exit_code'] = result['exit_code']
                entry['runner_failed'] = result.get('runner_failed', False)
                entry['error'] = result.get('error')
                entry['enforcement'] = result.get('enforcement')
                entry['runner'] = result.get('runner')
                entry['state'] = 'indeterminate' if result.get('cancelled') else 'finished'
                entry['cleanup'] = self._report(result['cleanup']['state'], entry['owner'])
        except BaseException as error:
            entry['state'] = 'indeterminate'
            entry['error'] = str(error) or type(error).__name__
        finally:
            processes = await self._controller.cancel(identity)
            entry['process_cleanup'] = processes
            entry['cleanup'] = self._report(processes.get('state', 'unknown'), entry['owner'])
            if entry.get('runner_failed'):
                (self.control_root / 'boundary-failure.json').write_text(json.dumps({
                    'mode': self.mode, 'exit_code': entry['exit_code'], 'error': entry.get('error'),
                    'command_started': False, 'cleanup': entry['cleanup']}, indent=2), encoding='utf-8')
            if entry['publish']:
                for stream in ('stdout', 'stderr'):
                    entry['sequence'] += 1
                    try:
                        await asyncio.wait_for(self.outputs.put({'execution_id': identity, 'owner': entry['owner'],
                            'stream': stream, 'sequence': str(entry['sequence']), 'raw_base64': '',
                            'text': '', 'encoding': 'utf-8', 'final': True}), 1)
                    except TimeoutError:
                        entry['discarded_bytes'] += 1

    async def status(self, execution_id):
        entry = self._executions[execution_id]
        # Completion includes final output frames and resource reconciliation.
        state = entry['state'] if entry['task'].done() else 'running'
        return {key:entry[key] for key in ('execution_id','owner','exit_code','stdout_bytes','stderr_bytes','discarded_bytes')} | {
            'state':state, 'runner_failed':entry.get('runner_failed', False), 'error':entry.get('error'),
            'enforcement':entry.get('enforcement'), 'runner':entry.get('runner')}

    async def cancel(self, execution_id, reason, deadline):
        entry = self._executions[execution_id]
        if not entry.get('cancel_requested'):
            entry['cancel_requested'] = True
            entry['task'].cancel()
        await asyncio.wait([entry['task']],timeout=3)
        report = entry['cleanup'] if entry['task'].done() else self._report('unknown',entry['owner'])
        return {'execution_id':execution_id,'confirmed':report['state']=='clean','cleanup':report}

    def _report(self, state, owner=None):
        report = {'owner':owner or self.owner,'state':state,'remaining_processes':0,
            'diagnostic_refs':[],
            'completed_at_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z') if state=='clean' else None}
        validate('cleanup-report', report)
        return report

    async def close(self):
        if self._closing is None: self._closing = asyncio.create_task(self._close())
        return await asyncio.shield(self._closing)

    async def _close(self):
        if self._preparing:
            _,pending = await asyncio.wait([self._preparing],timeout=30)
            if pending: return self._report('unknown')
        tasks = [entry['task'] for entry in self._executions.values()]
        for entry in self._executions.values():
            if not entry['task'].done() and not entry.get('cancel_requested'):
                entry['cancel_requested'] = True
                entry['task'].cancel()
        if tasks:
            _,pending = await asyncio.wait(tasks,timeout=8)
            if pending: return self._report('unknown')
        if not all(e['cleanup']['state']=='clean' for e in self._executions.values()):
            if hasattr(self, '_controller'):
                await self._controller.close()
            return self._report('unknown')
        if hasattr(self, '_controller') and (await self._controller.close())['state'] != 'clean':
            return self._report('unknown')
        if self.diagnostic_root is not None and hasattr(self, '_control_anchor'):
            try:
                self._control_anchor.assert_current()
                self._diagnostic_anchor.assert_current()
                for name in ('boundary.json', 'boundary-failure.json'):
                    source = self.control_root / name
                    if source.is_file():
                        (self.diagnostic_root / name).write_bytes(source.read_bytes())
                self._control_anchor.assert_current()
                shutil.rmtree(private_storage_path(self.control_root))
                (self.diagnostic_root / 'control-location.json').write_text(json.dumps({
                    'control_root': str(self.control_root), 'owner': self.owner,
                    'policy_hash': self.policy_hash, 'state': 'clean'}, indent=2), encoding='utf-8')
            except (OSError, ValueError):
                return self._report('unknown')
        return self._report('clean')

    async def aclose(self):
        await self.close()
