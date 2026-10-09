"""Single-writer application orchestration over the existing Python Harness.

These methods receive trusted dependencies from the composition root. Transport
principals and persistent approval decisions are bound by the following tasks.
"""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from forge.application.harness_adapter import HarnessAdapter
from forge.application.models import ContractError, canonical_hash, validate
from forge.application.sessions import outcome_for, session_view, turn_view
from forge.application.workspaces import WorkspaceService
from forge.application.session_views import TurnMessages
from forge.config import ForgeConfig
from forge.engine.journal_projection import JournalProjector
from forge.engine.lifecycle import Deadline
from forge.engine.persistence import encoded, new_id, utc_now
from forge.runtime.state import TurnCompleted
from forge.sessions.store import SessionStore
from forge.sandbox.policy import compile_policy


class CredentialProvider(Protocol):
    def resolve(self, connection_id: str) -> str | None: ...


class ApplicationServices:
    def __init__(self, store, *, profile_id, credentials: CredentialProvider, mode='strict', backend=None,
                 model_client_factory=None, recorder=None, approval_handler=None, task_relation=None, observation_options=None,
                 backend_factory=None):
        if mode not in ('strict', 'local-trusted', 'workspace-write'):
            raise ValueError('Execution mode must be strict, workspace-write or local-trusted')
        self.store = store
        self.profile_id = profile_id
        self.credentials = credentials
        self.mode = mode
        self.backend = backend
        if backend is not None and backend_factory is not None:
            raise ValueError('Choose a supplied backend or an owned backend factory')
        self.backend_factory = backend_factory
        self.model_client_factory = model_client_factory
        self.recorder = recorder
        self.approval_handler = approval_handler
        self.approvals = None
        self.task_relation = task_relation
        self.running = {}
        self.workspaces = WorkspaceService(self)
        from forge.observability.export_queue import ObservationOptions,ExportQueue
        self.observation_options=observation_options or ObservationOptions()
        self.store.observation_options=self.observation_options
        self.store.observation_secrets=()
        self.exporter=ExportQueue(store,self.observation_options)

    async def evidence_observation(self,workspace_id):
        revision,files,_,complete=await self.workspaces.refresh(workspace_id)
        # Content fingerprints exclude mtime; identical bytes remain the same observation.
        manifest={path:{key:item.get(key) for key in ('kind','classification','size_bytes','sha256')} for path,item in files.items()}
        complete=complete and all(item['kind']!='file' or item['sha256'] is not None for item in files.values())
        return {'revision':revision,'sha256':canonical_hash(manifest),'complete':complete}

    def open_workspace(self, selected_directory):
        return self.store.register_workspace(selected_directory)

    def authorize_workspace(self, workspace_id, *, expected_revision, allow):
        # Only a trusted host caller may expose this mutation through F05/F06.
        with self.store.transaction():
            updated = self.store.connection.execute('UPDATE workspaces SET trust=?,revision=revision+1 WHERE id=? AND revision=?',
                ('execution_allowed' if allow else 'inspect_only', workspace_id, expected_revision))
            if updated.rowcount != 1:
                raise ContractError('Workspace revision changed', kind='STALE_REVISION', code=-32010)
            return dict(self.store.connection.execute('SELECT * FROM workspaces WHERE id=?', (workspace_id,)).fetchone())

    def put_connection(self, config: ForgeConfig, *, connection_id=None):
        """Trusted host provisioning stores metadata; CredentialProvider owns the key."""
        parsed = urlsplit(config.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ContractError('Connection URL must not contain credentials, query or fragment')
        connection_id = connection_id or new_id('conn')
        metadata = {name: getattr(config, name) for name in ('model_id', 'base_url', 'max_tokens', 'context_window', 'provider', 'reasoning_effort')}
        metadata['request_timeout_seconds'] = str(config.request_timeout_seconds)
        validate('connection', {'connection_id': connection_id, 'revision': 1, 'provider': config.provider,
            'base_url': config.base_url, 'requested_model': config.model_id, 'credential_present': False})
        with self.store.transaction():
            self.store.connection.execute('INSERT INTO connections VALUES(?,1,?) ON CONFLICT(id) DO UPDATE SET '
                'revision=revision+1,configuration_json=excluded.configuration_json', (connection_id, encoded(metadata)))
        return connection_id

    def delete_connection(self, connection_id):
        with self.store.transaction():
            self.store.connection.execute('DELETE FROM connections WHERE id=?', (connection_id,))

    def put_policy(self, policy):
        validate('sandbox-policy', policy)
        workspace = self._workspace(policy['workspace_id'])
        roots = self.backend_factory.protected_roots if self.backend_factory else (self.store.data_dir,)
        snapshot = compile_policy(policy, workspace, control_roots=roots)
        with self.store.transaction():
            self.store.connection.execute('INSERT INTO policies VALUES(?,?,?,?)',
                (policy['policy_id'], snapshot.sha256, encoded(snapshot.value), self.mode))
        return policy['policy_id']

    def put_budget(self, budget):
        if set(budget) != {'max_model_calls', 'max_tool_calls', 'wall_seconds'} or any(
                type(value) is not int or not 1 <= value <= 2**53-1 for value in budget.values()):
            raise ContractError('Budget requires positive integer model, tool and wall limits')
        budget_id = new_id('budget')
        with self.store.transaction():
            self.store.connection.execute('INSERT INTO budget_profiles VALUES(?,?)', (budget_id, encoded(budget)))
        return budget_id

    def _workspace(self, workspace_id, *, expected_revision=None, execution=False):
        row = self.store.connection.execute('SELECT * FROM workspaces WHERE id=?', (workspace_id,)).fetchone()
        if row is None:
            raise ContractError('Workspace not found', kind='NOT_FOUND', code=-32010)
        if expected_revision is not None and row['revision'] != expected_revision:
            raise ContractError('Workspace revision changed', kind='STALE_REVISION', code=-32010)
        path = Path(row['canonical_path'])
        try:
            identity = path.stat()
        except OSError as error:
            raise ContractError('Workspace is unavailable', kind='NOT_FOUND', code=-32010) from error
        if not path.is_dir() or f'{identity.st_dev}:{identity.st_ino}' != row['file_identity'] or path.resolve() != path:
            raise ContractError('Workspace filesystem identity changed', kind='STALE_REVISION', code=-32010)
        if execution and row['trust'] != 'execution_allowed':
            raise ContractError('Workspace execution is not authorized', kind='UNAUTHORIZED', code=-32010)
        return dict(row)

    def _configuration(self, params, workspace_id, *, execution=False):
        workspace = self._workspace(workspace_id, expected_revision=params['expected_workspace_revision'], execution=execution)
        connection = self.store.connection.execute('SELECT * FROM connections WHERE id=?', (params['connection_id'],)).fetchone()
        policy = self.store.connection.execute('SELECT * FROM policies WHERE id=?', (params['policy_id'],)).fetchone()
        budget = self.store.connection.execute('SELECT * FROM budget_profiles WHERE id=?', (params['budget_profile_id'],)).fetchone()
        if connection is None or policy is None or budget is None:
            raise ContractError('Connection, policy or budget not found', kind='NOT_FOUND', code=-32010)
        policy_value = json.loads(policy['normalized_json'])
        if policy['capability_requirement'] != self.mode:
            raise ContractError('Policy execution mode changed', kind='STALE_REVISION', code=-32010)
        if policy_value['workspace_id'] != workspace_id:
            raise ContractError('Policy belongs to another workspace', kind='UNAUTHORIZED', code=-32010)
        roots = self.backend_factory.protected_roots if self.backend_factory else (self.store.data_dir,)
        compiled = compile_policy(policy_value, workspace, control_roots=roots)
        if compiled.sha256 != policy['hash']:
            raise ContractError('Policy normalization or path bindings changed; provision a new policy', kind='STALE_REVISION', code=-32010)
        credential = self.credentials.resolve(params['connection_id'])
        if not credential:
            raise ContractError('Connection credential is unavailable', kind='CONNECTION_UNAVAILABLE', code=-32010)
        if execution:
            if self.backend_factory is not None:
                self.backend_factory.check_request(policy_value, required_mode=self.mode)
            elif self.backend is None:
                raise ContractError('strict sandbox backend is not ready', kind='SANDBOX_UNAVAILABLE', code=-32010)
            else:
                self.backend.check_ready(policy_value, required_mode=self.mode)
        value = {'workspace_id': workspace_id, 'workspace_revision': workspace['revision'],
            'connection_id': params['connection_id'], 'connection_revision': connection['revision'],
            'model': json.loads(connection['configuration_json']), 'policy_id': policy['id'], 'policy_hash': policy['hash'],
            'budget_profile_id': params['budget_profile_id'], 'mode': self.mode,
            'model_source': 'injected' if self.model_client_factory else 'provider', **json.loads(budget['configuration_json'])}
        return value

    def _existing_action(self, method, params):
        row = self.store.connection.execute('SELECT * FROM actions WHERE profile_id=? AND method=? AND client_action_id=?',
            (self.profile_id, method, params['client_action_id'])).fetchone()
        if row:
            if row['params_hash'] != canonical_hash(params):
                raise ContractError('Action ID has different parameters', kind='IDEMPOTENCY_CONFLICT', code=-32010)
            return json.loads(row['result_json'])
        return None

    def _record_action(self, method, params, result):
        self.store.connection.execute('INSERT INTO actions VALUES(?,?,?,?,?,?)',
            (new_id('action'), self.profile_id, method, params['client_action_id'], canonical_hash(params), encoded(result)))

    def create_session(self, params, *, action_method='session.create', action_params=None):
        validate('session.create.request', params)
        action_params = params if action_params is None else action_params
        with self.store.transaction():
            existing = self._existing_action(action_method, action_params)
            if existing is not None:
                return existing
            configuration = self._configuration(params, params['workspace_id'])
            reference = self.store._configuration_snapshot(configuration, canonical_hash(configuration))
            session_id = new_id('ses')
            self.store.connection.execute('INSERT INTO sessions VALUES(?,?,NULL,?)', (session_id, params['workspace_id'], utc_now()))
            self.store.connection.execute('INSERT INTO session_configurations VALUES(?,?)', (session_id, reference['snapshot_id']))
            result = session_view(self.store, session_id)
            self._record_action(action_method, action_params, result)
            return result

    def start_turn(self, params, *, action_method='session.start_turn', action_params=None):
        validate('start-turn', params)
        action_params = params if action_params is None else action_params
        if not any(part['text'].strip() for part in params['input']):
            raise ContractError('Turn input must contain non-whitespace text')
        existing = self._existing_action(action_method, action_params)
        if existing is not None:
            return {**existing, 'reused_existing_action': True}
        session = session_view(self.store, params['session_id'])
        configuration = self._configuration(params, session['workspace_id'], execution=True)
        return self.store.accept_turn(self.profile_id, params, configuration,
            action_method=action_method, action_params=action_params)

    def cancel_turn(self, params):
        validate('session.cancel_turn.request', params)
        with self.store.transaction():
            existing = self._existing_action('session.cancel_turn', params)
            if existing is not None:
                return existing
            turn = turn_view(self.store, params['turn_id'])
            work = self.store.connection.execute('SELECT * FROM work_items WHERE business_id=?', (params['turn_id'],)).fetchone()
            if turn['state'] == 'queued':
                self.store.connection.execute("UPDATE turns SET state='finished',outcome='cancelled' WHERE id=?", (params['turn_id'],))
                self.store.connection.execute("UPDATE work_items SET state='finished',version=version+1 WHERE id=?", (work['id'],))
            elif turn['state'] in ('running', 'awaiting_approval'):
                self.store.connection.execute("UPDATE turns SET state='cancel_requested' WHERE id=?", (params['turn_id'],))
                self.store.connection.execute("UPDATE work_items SET state='cancel_requested' WHERE id=?", (work['id'],))
            if turn['state'] in ('queued', 'running', 'awaiting_approval'):
                self.store.connection.execute('INSERT INTO turn_lifecycle(turn_id,owner_epoch,cancel_state,cleanup_state) '
                    'VALUES(?,?,?,?) ON CONFLICT(turn_id) DO UPDATE SET cancel_state=excluded.cancel_state',
                    (params['turn_id'], self.store.epoch, 'confirmed' if turn['state'] == 'queued' else 'requested',
                     'clean' if turn['state'] == 'queued' else 'pending'))
                attributes = {'original_turn_id': params['turn_id'], 'original_attempt_id': None,
                    'unknown_side_effects': turn['state'] != 'queued', 'reason': params['reason'],
                    'cleanup_state': 'clean' if turn['state'] == 'queued' else 'pending'}
                self.store._turn_event('cancellation.requested', params['turn_id'], attributes)
                if turn['state'] == 'queued':
                    self.store.connection.execute('UPDATE turn_lifecycle SET cleanup_json=?,finished_at_utc=? WHERE turn_id=?',
                        (encoded({'state': 'clean', 'scope': 'not_dispatched', 'resources': {}}), utc_now(), params['turn_id']))
                    self.store._turn_event('cancellation.confirmed', params['turn_id'], attributes)
                    self.store._turn_event('turn.finished', params['turn_id'], {
                        'configuration': turn['configuration'], 'outcome': 'cancelled', 'native_outcome': None,
                        'reason': 'cancelled_before_dispatch', 'budget_summary': {
                            'model_calls_remaining': None, 'tool_calls_remaining': None, 'wall_seconds_remaining': None}})
            now = turn_view(self.store, params['turn_id'])
            result = {'turn_id': params['turn_id'], 'state': now['state'], 'outcome': now['outcome'],
                      'accepted': True, 'reused_existing_action': False}
            self._record_action('session.cancel_turn', params, result)
        task = self.running.get(params['turn_id'])
        if turn['state'] in ('running', 'awaiting_approval') and task is not None and not task.done():
            task.cancel()
        return result

    def get_snapshot(self, session_id):
        # A single read transaction binds the view and cursor to the same database snapshot.
        self.store.connection.execute('BEGIN')
        try:
            session = session_view(self.store, session_id)
            self._workspace(session['workspace_id'])
            rows = self.store.connection.execute('SELECT id FROM turns WHERE session_id=? ORDER BY rowid DESC LIMIT 101', (session_id,)).fetchall()
            cursor = self.store.connection.execute('SELECT COALESCE(MAX(store_seq),0) FROM events').fetchone()[0]
            return {'session': session, 'turns': [turn_view(self.store, r[0]) for r in rows[:100]],
                    'snapshot_cursor': str(cursor), 'has_more': len(rows) > 100}
        finally:
            self.store.connection.execute('ROLLBACK')

    async def execute_turn(self, turn_id):
        """Consume an accepted item with CAS. F05 dispatches this method asynchronously."""
        turn = self.store.connection.execute('SELECT * FROM turns WHERE id=?', (turn_id,)).fetchone()
        work = self.store.connection.execute('SELECT * FROM work_items WHERE business_id=?', (turn_id,)).fetchone()
        if turn is None or work is None:
            raise ContractError('Turn not found', kind='NOT_FOUND', code=-32010)
        claimed = self.store.claim_work_item(work['id'], expected_version=work['version'], emit_turn_event=True)
        self.running[turn_id] = asyncio.current_task()
        adapter = None
        turn_backend = self.backend
        result = None
        outcome = 'failed'
        reason = 'harness_error'
        timer = None
        deadline_expired = False
        cleanup = {'state': 'clean', 'scope': self.mode, 'resources': {}}
        projection_error = None
        model_cleanup_error = None
        messages = None
        observation_timer=None
        try:
            reference = json.loads(turn['config_json'])
            configuration = json.loads(self.store.connection.execute('SELECT normalized_json FROM configuration_snapshots WHERE id=?',
                (reference['snapshot_id'],)).fetchone()[0])
            params = {'expected_workspace_revision': configuration['workspace_revision'],
                **{key: configuration[key] for key in ('connection_id', 'policy_id', 'budget_profile_id')}}
            current = self._configuration(params, configuration['workspace_id'], execution=True)
            if canonical_hash(current) != canonical_hash(configuration):
                raise ContractError('Accepted configuration is no longer available', kind='STALE_REVISION', code=-32010)
            deadline = Deadline.start(configuration['wall_seconds'])
            with self.store.transaction():
                self.store.connection.execute('INSERT INTO turn_lifecycle(turn_id,owner_epoch,agent_deadline_utc,environment_deadline_utc,cancel_state,cleanup_state) '
                    'VALUES(?,?,?,?,?,?) ON CONFLICT(turn_id) DO UPDATE SET agent_deadline_utc=excluded.agent_deadline_utc, '
                    'environment_deadline_utc=excluded.environment_deadline_utc',
                    (turn_id, self.store.epoch, deadline.utc_text, deadline.utc_text, 'none', 'pending'))
            def expire():
                nonlocal deadline_expired
                deadline_expired = True
                self.cancel_turn({'turn_id': turn_id, 'client_action_id': new_id('act'), 'reason': 'Agent deadline exceeded'})
            timer = asyncio.create_task(deadline.watch(asyncio.current_task(), expire))
            if self.backend_factory is not None:
                policy = json.loads(self.store.connection.execute('SELECT normalized_json FROM policies WHERE id=?',
                    (configuration['policy_id'],)).fetchone()[0])
                workspace = self._workspace(configuration['workspace_id'], execution=True)
                owner = {'engine_epoch': self.store.epoch, 'sandbox_session_id': new_id('sandbox'), 'execution_id': None}
                turn_backend = await self.backend_factory.open(workspace, policy, owner, turn_id)
            metadata = dict(configuration['model'])
            metadata['request_timeout_seconds'] = float(metadata['request_timeout_seconds'])
            config = ForgeConfig(api_key=self.credentials.resolve(configuration['connection_id']), **metadata)
            self.store.observation_secrets=(config.api_key,)
            self.exporter.start()
            messages = TurnMessages(self.store, turn_id, config.api_key)
            for part in json.loads(turn['input_json']):
                messages.append('user', part['text'])
            session = self.store.connection.execute('SELECT * FROM sessions WHERE id=?', (turn['session_id'],)).fetchone()
            root = Path(self._workspace(session['workspace_id'], execution=True)['canonical_path'])
            native_store = SessionStore(root, data_root=self.store.data_dir / 'harness')
            previous = native_store.load(session['legacy_ref']) if session['legacy_ref'] else None
            fork = previous is not None and (previous.info.model != config.model_id or previous.info.provider != config.provider)
            approval = self.approval_handler
            if approval is None and self.approvals is not None:
                async def approval(request):
                    return await self.approvals.authorize(request, turn_id=turn_id, configuration=configuration)
            adapter = HarnessAdapter(root, config=config, data_root=self.store.data_dir / 'harness',
                backend=turn_backend, budget=configuration, model_client_factory=self.model_client_factory,
                recorder=self.recorder, approval_handler=approval, task_relation=self.task_relation,
                resume_identifier=session['legacy_ref'], fork_session=fork,
                turn_baseline_handler=lambda: self.workspaces.capture(turn_id, session['workspace_id']))
            adapter.journal.observation_scope=self.store.trace_scope(turn_id)
            adapter.journal.observation_scope_sink=self.store.register_execution_scope
            adapter.journal.observation_options=self.observation_options
            adapter.journal.observation_secrets=(config.api_key,)
            adapter.journal.observation_evidence_probe=lambda: self.evidence_observation(session['workspace_id'])
            with self.store.transaction():
                self.store.connection.execute('UPDATE sessions SET legacy_ref=? WHERE id=?', (adapter.journal.session_id, session['id']))
                self.store.connection.execute('UPDATE turns SET native_ref=? WHERE id=?', (adapter.journal.session_id, turn_id))
            last_projection=0.0
            last_sequence=-1
            def project_live():
                nonlocal last_projection,last_sequence,projection_error
                if adapter.journal.sequence==last_sequence:return
                try:
                    JournalProjector(self.store).project(adapter.journal.path,turn['session_id'],trusted=True)
                    last_sequence=adapter.journal.sequence;last_projection=asyncio.get_running_loop().time()
                except Exception as error:
                    projection_error=error
                    raise
            owner=asyncio.current_task()
            async def observe_quiet_boundaries():
                while True:
                    await asyncio.sleep(0.25)
                    try: project_live()
                    except Exception:
                        owner.cancel()
                        return
            observation_timer=asyncio.create_task(observe_quiet_boundaries())
            async for event in adapter.stream(json.loads(turn['input_json'])):
                messages.observe(event)
                now=asyncio.get_running_loop().time()
                from forge.runtime.state import ToolExecutionCompleted
                if now-last_projection>=0.25 or isinstance(event,(ToolExecutionCompleted,TurnCompleted)):
                    project_live()
                if isinstance(event, TurnCompleted):
                    result = event.result
            if result is None:
                outcome, reason = 'indeterminate', 'harness_returned_without_terminal_result'
            else:
                uncertain = any(r.status == 'indeterminate' for r in adapter.conversation.turn_state.execution_records)
                outcome = 'indeterminate' if uncertain else outcome_for(result)
                reason = 'unknown_tool_result' if uncertain else result.stop_reason or result.status
        except ContractError as error:
            outcome, reason = 'blocked', str(error.kind)
            cleanup = getattr(error, 'cleanup_report', cleanup)
        except BlockingIOError:
            outcome, reason = 'blocked', 'workspace_in_use'
        except asyncio.CancelledError as error:
            cleanup = getattr(error, 'cleanup_report', cleanup)
            records = adapter.conversation.turn_state.execution_records if adapter and adapter.conversation.turn_state else []
            outcome = 'indeterminate' if any(r.status == 'indeterminate' for r in records) else 'cancelled'
            reason = 'cancellation_propagated' if outcome == 'cancelled' else 'cancelled_with_unknown_tool_result'
            if deadline_expired and outcome != 'indeterminate':
                outcome, reason = 'timed_out', 'agent_deadline_exceeded'
        except Exception as error:
            outcome, reason = 'indeterminate' if adapter else 'failed', type(error).__name__
        finally:
            if observation_timer is not None:
                observation_timer.cancel()
                await asyncio.gather(observation_timer,return_exceptions=True)
            if messages is not None:
                messages.flush(final=True)
            if timer is not None:
                timer.cancel()
                await asyncio.gather(timer, return_exceptions=True)
            with self.store.transaction():
                self.store.connection.execute("UPDATE turn_lifecycle SET cleanup_state='running' WHERE turn_id=? AND owner_epoch=?",
                    (turn_id, self.store.epoch))
            if adapter is not None:
                try:
                    await asyncio.wait_for(adapter.close(), 2)
                except (Exception, asyncio.CancelledError) as error:
                    model_cleanup_error = type(error).__name__
            if turn_backend is not None and hasattr(turn_backend, 'close'):
                try:
                    cleanup = await asyncio.wait_for(turn_backend.close(), 5)
                    if not isinstance(cleanup, dict) or cleanup.get('state') not in ('clean', 'residual', 'unknown'):
                        cleanup = {'state': 'unknown', 'reason': 'invalid_backend_cleanup_report'}
                except (Exception, asyncio.CancelledError) as error:
                    cleanup = {'state': 'unknown', 'reason': type(error).__name__}
            elif self.mode != 'local-trusted' and not cleanup.get('reports'):
                cleanup = {'state': 'unknown', 'reason': 'native_cleanup_observation_unavailable'}
            if model_cleanup_error:
                cleanup = {'state': 'unknown', 'model_cleanup_error': model_cleanup_error, 'backend': cleanup}
            if adapter is not None:
                try:
                    JournalProjector(self.store).project(adapter.journal.path,turn['session_id'],trusted=True)
                except Exception as error:
                    projection_error=error  # Journal remains authoritative; no tool replay.
            if outcome in ('cancelled', 'timed_out') and cleanup['state'] != 'clean':
                outcome, reason = 'indeterminate', 'cancellation_cleanup_unconfirmed'
            self.running.pop(turn_id, None)
            if projection_error is not None:
                with self.store.transaction():
                    self.store.connection.execute("UPDATE work_items SET state='reconciling',version=version+1 WHERE id=?", (claimed['id'],))
                    self.store.connection.execute("UPDATE turns SET state='reconciling' WHERE id=?", (turn_id,))
                    self.store.connection.execute("UPDATE turn_lifecycle SET cancel_state='indeterminate',cleanup_state=?,cleanup_json=? WHERE turn_id=?",
                        (cleanup['state'], encoded(cleanup), turn_id))
                raise projection_error
            self._finish(claimed, turn, outcome, reason, result, cleanup=cleanup)
        return result

    def _finish(self, work, turn, outcome, reason, result, *, cleanup=None):
        native = {'status': result.status, 'stop_reason': result.stop_reason, 'model_calls': result.model_calls,
                  'tool_calls': [call.name for call in result.tool_calls], 'completion_reasons': list(result.completion_reasons),
                  'completion_report': asdict(result.completion_report) if result.completion_report else None} if result else None
        with self.store.transaction():
            updated = self.store.connection.execute("UPDATE work_items SET state='finished',version=version+1 WHERE id=? AND version=? AND owner_epoch=? AND state IN ('running','cancel_requested')",
                (work['id'], work['version'], self.store.epoch))
            if updated.rowcount != 1:
                raise ContractError('Execution ownership changed', kind='INDETERMINATE', code=-32010)
            self.store.connection.execute("UPDATE turns SET state='finished',outcome=? WHERE id=?", (outcome, turn['id']))
            self.store.connection.execute('INSERT INTO turn_results VALUES(?,?)', (turn['id'], encoded(native)))
            requested = self.store.connection.execute('SELECT cancel_state FROM turn_lifecycle WHERE turn_id=?', (turn['id'],)).fetchone()
            cleanup = cleanup or {'state': 'unknown', 'reason': 'cleanup_unobserved'}
            confirmed = bool(requested and requested[0] == 'requested' and outcome != 'indeterminate' and cleanup['state'] == 'clean')
            self.store.connection.execute('UPDATE turn_lifecycle SET cancel_state=?,cleanup_state=?,cleanup_json=?,finished_at_utc=? '
                'WHERE turn_id=? AND owner_epoch=?',
                ('confirmed' if confirmed else 'indeterminate' if requested and requested[0] == 'requested' else 'none',
                 cleanup['state'], encoded(cleanup), utc_now(), turn['id'], self.store.epoch))
            if confirmed:
                self.store._turn_event('cancellation.confirmed', turn['id'], {'original_turn_id': turn['id'],
                    'original_attempt_id': None, 'unknown_side_effects': False, 'reason': reason, 'cleanup_state': 'clean'})
            self.store._turn_event('turn.finished', turn['id'],
                {'configuration': json.loads(turn['config_json']), 'outcome': outcome, 'native_outcome': result.status if result else None,
                 'reason': reason, 'budget_summary': {'model_calls_remaining': None, 'tool_calls_remaining': None, 'wall_seconds_remaining': None}})
