"""Explicit workspace-write through locked DSH/SRT and existing owned process trees."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

from forge.application.models import ContractError, canonical_hash, validate
from forge.sandbox.capabilities import CapabilityReport, FEATURES, windows_supported
from forge.sandbox.launcher import ROOT, bridge_environment, verify_runtime
from forge.sandbox.path_policy import inspect_path
from forge.sandbox.policy import compile_policy
from forge.sandbox.process_owner import LocalProcessOwner
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

    def __init__(self, workspace, owner, control_root, *, trusted_root=ROOT):
        validate('cleanup-report', {'owner': owner, 'state': 'unknown', 'remaining_processes': 0,
                                   'diagnostic_refs': [], 'completed_at_utc': None})
        self.workspace, self.owner = dict(workspace), dict(owner)
        self.control_root, self.trusted_root = Path(control_root).absolute(), Path(trusted_root)
        self.outputs = asyncio.Queue(maxsize=8)
        self._prepared = None
        self._preparing = None
        self._closing = None
        self._executions = {}
        self._retained = 0

    async def prepare(self, policy):
        if self._closing or self._preparing or self._prepared:
            raise ContractError('Native session initialization is single-use', kind='SANDBOX_UNAVAILABLE', code=-32010)
        require_workspace_policy(policy)
        self._preparing = asyncio.create_task(self._prepare(policy))
        return await asyncio.shield(self._preparing)

    async def _prepare(self, policy):
        if sys.platform not in ('win32', 'linux') or (sys.platform == 'win32' and not windows_supported()):
            raise ContractError('Workspace-write requires Windows or Linux', kind='SANDBOX_UNAVAILABLE', code=-32010)
        snapshot = compile_policy(policy, self.workspace, control_roots=(self.control_root,))
        self.runtime = verify_runtime(self.trusted_root)
        self.bridge_root = self.runtime.entry.parent if self.runtime.installed else self.runtime.root
        self.backend_name = 'dsh-windows-acl' if sys.platform == 'win32' else 'srt-bubblewrap'
        self.backend_version = '0.2.1-alpha.1' if sys.platform == 'win32' else '0.0.78'
        self.runner = self.bridge_root / 'sandbox_bridge/dist/workspace-runner.js'
        root = Path(self.workspace['canonical_path'])
        for protected in (self.runtime.root, self.control_root):
            if root == protected or root.is_relative_to(protected) or protected.is_relative_to(root):
                raise ContractError('Installation and control must be separate from workspace', kind='POLICY_DENIED', code=-32010)
        if snapshot.value['filesystem']['write_roots'] != [str(root)]:
            raise ContractError('Workspace-write requires exactly the selected workspace', kind='CAPABILITY_UNSATISFIED', code=-32010)
        inspect_path(str(self.control_root), absolute=True).assert_current()
        self.control_root.mkdir(parents=True, exist_ok=False, mode=0o700)
        if sys.platform == 'win32':
            from forge.sandbox.windows_worker import grant_controller_access
            grant_controller_access(self.control_root)
        self._policy = snapshot
        identity = 'exec-' + str(uuid4())
        inside = root / ('.forge-canary-' + identity)
        outside = self.control_root / 'denied-canary'
        script = "const fs=require('node:fs');const [a,b]=process.argv.slice(1);fs.writeFileSync(a,'probe',{flag:'wx'});let denied=false;try{fs.writeFileSync(b,'escape',{flag:'wx'});}catch{denied=true;}fs.writeFileSync(require('node:path').join(process.env.TEMP || process.env.TMPDIR,'probe-temp'),'temp');console.log(JSON.stringify({inside:fs.readFileSync(a,'utf8')==='probe',outsideDenied:denied,stdin:fs.readFileSync(0,'utf8')}));"
        command = {'mode': 'argv', 'argv': [str(self.runtime.node), '-e', script, str(inside), str(outside)],
            'cwd': str(root), 'environment': {}, 'deadline_utc': (datetime.now(timezone.utc)+timedelta(seconds=min(25, policy['limits']['wall_time_seconds']))).isoformat().replace('+00:00','Z'),
            'output_limit_bytes': min(65536, policy['limits']['command_output_bytes']), 'stdin_base64': base64.b64encode(b'forge-probe').decode()}
        if self._closing: raise ContractError('Session closed before native probe',kind='SANDBOX_UNAVAILABLE',code=-32010)
        execution = self._accept(identity, command, publish=False)
        try:
            await asyncio.shield(execution['task'])
            proof = json.loads(execution['captured']['stdout'])
            if execution['exit_code'] != 0 or proof != {'inside': True, 'outsideDenied': True, 'stdin': 'forge-probe'} or execution['cleanup']['state'] != 'clean':
                raise ValueError('Native write/stdin/cleanup boundary probe failed')
            evidence = {'backend': self.backend_name, 'version': self.backend_version, 'probe': proof, 'cleanup': execution['cleanup'],
                'workspace_grant': 'standing; intentionally retained across sessions' if sys.platform == 'win32' else 'per-process mount namespace', 'read_network_isolation': False}
            (self.control_root/'boundary.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        except (ValueError, KeyError) as error:
            raise ContractError('Native workspace-write probe failed; no task admitted', kind='SANDBOX_UNAVAILABLE', code=-32010) from error
        finally:
            if execution.get('cleanup',{}).get('state') == 'clean':
                inside.unlink(missing_ok=True)
                outside.unlink(missing_ok=True)
        if self._closing:
            raise ContractError('Native session closed during initialization', kind='SANDBOX_UNAVAILABLE', code=-32010)
        verification = {name: {'status': 'unsupported', 'evidence_refs': []} for name in FEATURES}
        verification['write_isolation'] = {'status': 'partial', 'evidence_refs': ['owned-native-write-canary']}
        verification['process_cleanup'] = {'status': 'verified', 'evidence_refs': ['owned-job-zero-active-and-temp-removed']}
        report = CapabilityReport({'platform': 'windows-native' if sys.platform == 'win32' else 'linux-native', 'backend': self.backend_name, 'backend_version': self.backend_version,
            'read_isolation': 'unavailable', 'write_isolation': False, 'direct_network_isolation': False,
            'dns_isolation': False, 'socket_isolation': False, 'process_cleanup': True,
            'resource_enforcement': {name:'unavailable' for name in ('memory','disk','pids')}, 'readiness': 'ready',
            'issues': ['Partial write restriction; reads and network inherit host access',
                       'Ambient ACL and hard-link limitations remain' if sys.platform == 'win32' else 'Write allowlist uses an owned bubblewrap mount namespace'],
            'measured_at_utc': datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), 'verification': verification})
        self._prepared = snapshot
        return {'sandbox_session_id': self.owner['sandbox_session_id'], 'policy_hash': snapshot.sha256,
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
        if not cwd.is_relative_to(root):
            raise ContractError('Command cwd exceeds workspace', kind='POLICY_DENIED', code=-32010)
        from forge.sandbox.policy import UNSAFE_ENV
        if any(UNSAFE_ENV.search(key) or key not in self._prepared.value['environment_keys'] for key in command['environment']):
            raise ContractError('Command environment exceeds policy', kind='POLICY_DENIED', code=-32010)
        seconds = (datetime.fromisoformat(command['deadline_utc'].replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds()
        if not 0 < seconds <= self._prepared.value['limits']['wall_time_seconds'] or command['output_limit_bytes'] > self._prepared.value['limits']['command_output_bytes']:
            raise ContractError('Command exceeds frozen limits', kind='POLICY_DENIED', code=-32010)
        entry = self._accept(execution_id, command)
        return {key:entry[key] for key in ('execution_id','owner','state')} | {'reused_existing_execution': False}

    async def _read(self, entry, stream, reader):
        while raw := await reader.read(8192):
            entry[stream+'_bytes'] += len(raw)
            allowed = max(0,min(entry['command']['output_limit_bytes']-entry['retained'], self._policy.value['limits']['session_artifact_bytes']-self._retained))
            retained = raw[:allowed]
            entry['discarded_bytes'] += len(raw)-len(retained)
            entry['retained'] += len(retained); self._retained += len(retained)
            if not entry['publish']: entry['captured'][stream] += retained
            elif retained:
                entry['sequence'] += 1
                frame = {'execution_id':entry['execution_id'],'owner':entry['owner'],'stream':stream,
                    'sequence':str(entry['sequence']),'raw_base64':base64.b64encode(retained).decode(),
                    'text':retained.decode('utf-8',errors='replace'),'encoding':'utf-8','final':False}
                try: self.outputs.put_nowait(frame)
                except asyncio.QueueFull: entry['discarded_bytes'] += len(retained)

    async def _run(self, entry):
        identity, command = entry['execution_id'], entry['command']
        temp, payload = self.control_root/('temp-'+identity), self.control_root/(identity+'.json')
        owner = None; readers = []
        try:
            temp.mkdir(mode=0o700)
            if sys.platform == 'win32':
                from forge.sandbox.windows_worker import grant_controller_access
                grant_controller_access(temp)
            raw = json.dumps({'command':command,'shells':{'pwsh':str(self.runtime.tools['powershell'])} if sys.platform == 'win32' else {'bash':'/bin/bash','sh':'/bin/sh'},
                'tools':{'git':str(self.runtime.tools['git']),'rg':str(self.runtime.tools['ripgrep'])} if sys.platform == 'win32' else {'git':'/usr/bin/git','rg':'/usr/bin/rg'}}).encode()
            if len(raw)>1048576: raise ValueError('Launch payload exceeds quota')
            with payload.open('xb') as output: output.write(raw)
            argv = [str(self.runtime.node),str(self.runner),'--workspace',self.workspace['canonical_path'],'--temp',str(temp),
                '--payload-file',str(payload),'--payload-sha256',sha256(raw).hexdigest()]
            remaining = (datetime.fromisoformat(command['deadline_utc'].replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds()
            async with asyncio.timeout(max(0,remaining)):
                owner = await LocalProcessOwner.start(argv,cwd=self.workspace['canonical_path'],environment=bridge_environment(self.control_root),output=True,capture_stderr=True)
                entry['state'] = 'running'
                if sys.platform == 'win32' and command.get('stdin_base64'):
                    owner.process.stdin.write(base64.b64decode(command['stdin_base64'], validate=True))
                    await owner.process.stdin.drain()
                owner.process.stdin.close()
                readers = [asyncio.create_task(self._read(entry,stream,getattr(owner.process,stream))) for stream in ('stdout','stderr')]
                entry['exit_code'] = await owner.process.wait()
                await asyncio.gather(*readers)
                entry['state'] = 'finished'
        except BaseException as error:
            entry['state'] = 'indeterminate'
            entry['error'] = type(error).__name__
        finally:
            processes = await owner.close() if owner else {'state':'clean','active_processes':0}
            entry['process_cleanup'] = processes
            for task in readers:
                if not task.done(): task.cancel()
            await asyncio.gather(*readers,return_exceptions=True)
            clean = processes['state'] == 'clean'
            if clean and temp.exists():
                try:
                    if sys.platform == 'win32': await self._revoke_temp(temp)
                    if temp.parent != self.control_root or temp.resolve() != temp: raise ValueError('Owned temp identity changed')
                    shutil.rmtree(private_storage_path(temp))
                    clean = not temp.exists()
                except Exception as error:
                    clean = False; entry['cleanup_error'] = type(error).__name__ + ': ' + str(error)
            if clean: payload.unlink(missing_ok=True)
            entry['cleanup'] = self._report('clean' if clean else 'unknown',entry['owner'])
            if entry['publish']:
                for stream in ('stdout','stderr'):
                    entry['sequence'] += 1
                    try:
                        await asyncio.wait_for(self.outputs.put({'execution_id':identity,'owner':entry['owner'],'stream':stream,
                            'sequence':str(entry['sequence']),'raw_base64':'','text':'','encoding':'utf-8','final':True}),1)
                    except TimeoutError: entry['discarded_bytes'] += 1

    async def _revoke_temp(self, temp):
        owner = await LocalProcessOwner.start([str(self.runtime.node),str(self.runner),'--cleanup-temp',str(temp)],
            cwd=str(self.control_root),environment=bridge_environment(self.control_root),output=True,capture_stderr=True)
        try:
            async with asyncio.timeout(5):
                owner.process.stdin.close()
                raw, diagnostic = await asyncio.gather(owner.process.stdout.read(4097), owner.process.stderr.read(4097))
                await owner.process.wait()
                if owner.process.returncode != 0 or json.loads(raw) != {'temp_grant_revoked': True}:
                    raise ValueError('Temporary ACL cleanup failed: ' + diagnostic.decode('utf-8',errors='replace')[:1024])
        finally:
            if (await owner.close())['state'] != 'clean': raise ValueError('ACL cleanup process remains unconfirmed')

    async def status(self, execution_id):
        entry = self._executions[execution_id]
        # Completion includes final output frames and resource reconciliation.
        state = entry['state'] if entry['task'].done() else 'running'
        return {key:entry[key] for key in ('execution_id','owner','exit_code','stdout_bytes','stderr_bytes','discarded_bytes')} | {'state':state}

    async def cancel(self, execution_id, reason, deadline):
        entry = self._executions[execution_id]
        if not entry.get('cancel_requested'):
            entry['cancel_requested'] = True
            entry['task'].cancel()
        await asyncio.wait([entry['task']],timeout=3)
        report = entry['cleanup'] if entry['task'].done() else self._report('unknown',entry['owner'])
        return {'execution_id':execution_id,'confirmed':report['state']=='clean','cleanup':report}

    def _report(self, state, owner=None):
        return {'owner':owner or self.owner,'state':state,'remaining_processes':0,
            'diagnostic_refs':['workspace-grant-standing-by-design' if sys.platform == 'win32' else 'owned-mount-namespace-and-temp'],
            'completed_at_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z') if state=='clean' else None}

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
        return self._report('clean' if all(e['cleanup']['state']=='clean' for e in self._executions.values()) else 'unknown')

    async def aclose(self):
        await self.close()
