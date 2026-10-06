"""Durable exact-scope permission waits and Main-only, short-lived confirmation nonces."""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
from pathlib import Path
import secrets
from time import monotonic

from forge.application.models import ContractError, canonical_hash, validate
from forge.engine.persistence import encoded, new_id, utc_now
from forge.permissions.context import tool_approval_context
from forge.permissions.policy import ApprovalResponse


def refuse(message, kind='UNAUTHORIZED'):
    raise ContractError(message, kind=kind, code=-32010)


class ConfirmationNonces:
    """Tokens never leave the private Main channel; restart invalidates all live grants."""
    def __init__(self, store, *, ttl=30):
        if not 0 < ttl <= 120:
            raise ValueError('Confirmation lifetime must be positive and at most 120 seconds')
        self.store, self.ttl, self.tokens = store, ttl, {}

    def issue(self, kind, target, binding_hash, *, expires_at=None):
        now = datetime.now(timezone.utc)
        self.tokens = {key: value for key, value in self.tokens.items() if value[3] > monotonic() and value[4] > now}
        if len(self.tokens) >= 4096:
            refuse('Confirmation queue is full')
        expires = min(now + timedelta(seconds=self.ttl), datetime.fromisoformat(expires_at.replace('Z', '+00:00'))) if expires_at else now + timedelta(seconds=self.ttl)
        if expires <= now:
            refuse('Confirmation has expired')
        token = secrets.token_hex(32)
        digest = sha256(token.encode()).hexdigest()
        self.tokens[digest] = (kind, target, binding_hash, monotonic() + (expires - now).total_seconds(), expires)
        return {'confirmation_token': token, 'expires_at_utc': expires.isoformat().replace('+00:00', 'Z')}

    def consume(self, token, kind, target, binding_hash):
        digest = sha256(token.encode()).hexdigest()
        value = self.tokens.get(digest)
        if not value or value[:2] != (kind, target) or not hmac.compare_digest(value[2], binding_hash) or value[3] <= monotonic() or value[4] <= datetime.now(timezone.utc):
            refuse('Confirmation is unknown, expired or bound to another operation')
        self.tokens.pop(digest)
        self.store.connection.execute('INSERT INTO consumed_nonces VALUES(?,?,?,?,?,?)',
            (digest, kind, target, binding_hash, self.store.epoch, utc_now()))


class ApprovalService:
    def __init__(self, service, *, nonce_ttl=30):
        self.service, self.store = service, service.store
        self.nonces = ConfirmationNonces(self.store, ttl=nonce_ttl)
        self.pending = {}

    def _row(self, approval_id):
        row = self.store.connection.execute('SELECT a.*,d.owner_epoch,d.binding_json,d.database_revision,d.environment_epoch,d.state '
            'FROM approvals a JOIN approval_details d ON d.approval_id=a.id WHERE a.id=?', (approval_id,)).fetchone()
        if row is None:
            refuse('Approval not found', 'NOT_FOUND')
        return row

    def get(self, params):
        row = self._row(params['approval_id'])
        value = json.loads(row['binding_json'])['view']
        value['state'] = row['state']
        if value['state'] in ('pending', 'approved') and (row['owner_epoch'] != self.store.epoch or datetime.fromisoformat(value['expires_at_utc'].replace('Z', '+00:00')) <= datetime.now(timezone.utc)):
            value['state'] = 'expired'
        return validate('approval', value)

    def _event(self, event_type, value, decision):
        producer = self.store.connection.execute("SELECT value FROM store_meta WHERE key='producer_id'").fetchone()[0]
        sequence = self.store.connection.execute('SELECT COALESCE(MAX(source_seq),0)+1 FROM events WHERE source_id=?', (producer,)).fetchone()[0]
        body = self.store.event_body(event_type, producer, sequence, {'approval_id': value['approval_id'], 'binding_hash': value['binding_hash'],
            'scope': {'kind': 'turn', 'id': value['turn_id']}, 'actor': 'engine' if decision is None else 'user', 'decision': decision},
            workspace_id=value['workspace_id'], turn_id=value['turn_id'], execution_id=value['execution_id'],
            session_id=self.store.connection.execute('SELECT session_id FROM turns WHERE id=?', (value['turn_id'],)).fetchone()[0])
        self.store._insert_event(body, producer, sequence)

    async def authorize(self, request, *, turn_id, configuration):
        context = tool_approval_context.get()
        if context is None or context.tracker is None or request.hard_deny:
            return ApprovalResponse('deny', 'Exact trusted execution context is unavailable')
        tracker = context.tracker
        await tracker.watch_paths_async(tuple(request.targets))
        await tracker.refresh()
        if not tracker.available:
            return ApprovalResponse('deny', 'Workspace observation is incomplete')
        workspace = self.service._workspace(configuration['workspace_id'], expected_revision=configuration['workspace_revision'], execution=True)
        policy = self.store.connection.execute('SELECT hash FROM policies WHERE id=?', (configuration['policy_id'],)).fetchone()[0]
        arguments = context.call.arguments
        cwd = (Path(workspace['canonical_path']) / arguments.get('cwd', '.')).resolve()
        if not cwd.is_relative_to(Path(workspace['canonical_path'])):
            return ApprovalResponse('deny', 'Tool cwd is outside the selected workspace')
        expires = (datetime.now(timezone.utc) + timedelta(seconds=min(120, configuration['wall_seconds']))).isoformat().replace('+00:00', 'Z')
        value = {'approval_id': new_id('approval'), 'workspace_id': workspace['id'], 'turn_id': turn_id,
            'execution_id': context.execution_id, 'workspace_revision': tracker.revision, 'policy_hash': policy, 'cwd': str(cwd),
            'argv': None, 'script_hash': sha256(arguments['command'].encode()).hexdigest() if isinstance(arguments.get('command'), str) else None,
            'path_delta': list(request.targets), 'network_delta': [], 'expires_at_utc': expires,
            'tool_name': context.call.name, 'risk': request.risk, 'reason': request.reason,
            'preview': request.preview[:4000], 'arguments_hash': canonical_hash(arguments)}
        binding = {'view': value.copy(), 'call_id': context.call.id, 'capability': request.capability,
            'environment_epoch': tracker.environment_epoch, 'database_revision': workspace['revision']}
        value['binding_hash'] = canonical_hash(binding)
        binding['view'] = value.copy()
        validate('approval', {**value, 'state': 'pending'})
        future = asyncio.get_running_loop().create_future()
        with self.store.transaction():
            self.store.connection.execute('INSERT INTO approvals VALUES(?,?,?,?,?,NULL,NULL)',
                (value['approval_id'], turn_id, context.execution_id, value['binding_hash'], expires))
            self.store.connection.execute('INSERT INTO approval_details VALUES(?,?,?,?,?,?)',
                (value['approval_id'], self.store.epoch, encoded(binding), workspace['revision'], tracker.environment_epoch, 'pending'))
            self.store.connection.execute("UPDATE turns SET state='awaiting_approval' WHERE id=? AND state='running'", (turn_id,))
            self._event('approval.requested', value, None)
        self.pending[value['approval_id']] = (future, tracker, configuration, context)
        try:
            decision = await asyncio.wait_for(future, min(120, configuration['wall_seconds']))
            if decision != 'approve':
                return ApprovalResponse('deny', 'Main denied this exact operation')
            await self._check_live(value['approval_id'], value['binding_hash'], states=('approved',))
            with self.store.transaction():
                updated = self.store.connection.execute("UPDATE approval_details SET state='consumed' WHERE approval_id=? AND state='approved'", (value['approval_id'],))
                if updated.rowcount != 1:
                    refuse('Approval was already consumed')
                self.store.connection.execute('UPDATE approvals SET consumed_at=? WHERE id=?', (utc_now(), value['approval_id']))
            return ApprovalResponse('allow_once', 'Exact Main confirmation consumed')
        except (TimeoutError, ContractError):
            return ApprovalResponse('deny', 'Approval expired or its exact binding changed')
        finally:
            self.pending.pop(value['approval_id'], None)
            with self.store.transaction():
                expired = self.store.connection.execute("UPDATE approval_details SET state='expired' WHERE approval_id=? AND state IN ('pending','approved')", (value['approval_id'],))
                if expired.rowcount:
                    self._event('approval.expired', value, None)
                self.store.connection.execute("UPDATE turns SET state='running' WHERE id=? AND state='awaiting_approval'", (turn_id,))

    async def _check_live(self, approval_id, binding_hash, *, states=('pending',)):
        row = self._row(approval_id)
        if row['owner_epoch'] != self.store.epoch or row['state'] not in states or not hmac.compare_digest(row['binding_hash'], binding_hash):
            refuse('Approval binding or lifecycle changed')
        value = self.get({'approval_id': approval_id})
        if value['state'] == 'expired' or approval_id not in self.pending:
            refuse('Approval has no live owned execution')
        future, tracker, configuration, context = self.pending[approval_id]
        if canonical_hash(context.call.arguments) != value['arguments_hash']:
            refuse('Final tool arguments changed', 'STALE_REVISION')
        await tracker.refresh()
        self.service._workspace(value['workspace_id'], expected_revision=row['database_revision'], execution=True)
        policy_hash = self.store.connection.execute('SELECT hash FROM policies WHERE id=?', (configuration['policy_id'],)).fetchone()[0]
        turn = self.store.connection.execute('SELECT state FROM turns WHERE id=?', (row['turn_id'],)).fetchone()
        if not tracker.available or tracker.revision != value['workspace_revision'] or tracker.environment_epoch != row['environment_epoch'] or policy_hash != value['policy_hash'] or turn['state'] != 'awaiting_approval':
            refuse('Workspace, policy, environment or turn changed', 'STALE_REVISION')
        return value

    async def prepare(self, params):
        value = await self._check_live(params['approval_id'], params['binding_hash'])
        return self.nonces.issue('approval', params['approval_id'], params['binding_hash'], expires_at=value['expires_at_utc'])

    async def decide(self, params):
        validate('approval.decide.request', params)
        existing = self.service._existing_action('approval.decide', params)
        if existing is not None:
            return {**existing, 'reused_existing_action': True}
        value = await self._check_live(params['approval_id'], params['binding_hash'])
        with self.store.transaction():
            existing = self.service._existing_action('approval.decide', params)
            if existing is not None:
                return {**existing, 'reused_existing_action': True}
            self.nonces.consume(params['confirmation_token'], 'approval', params['approval_id'], params['binding_hash'])
            state = 'approved' if params['decision'] == 'approve' else 'denied'
            updated = self.store.connection.execute("UPDATE approval_details SET state=? WHERE approval_id=? AND state='pending'", (state, params['approval_id']))
            if updated.rowcount != 1:
                refuse('Approval was already decided')
            self.store.connection.execute('UPDATE approvals SET decision=? WHERE id=?', (params['decision'], params['approval_id']))
            self._event('approval.decided', value, params['decision'])
            result = {'approval_id': params['approval_id'], 'state': state, 'reused_existing_action': False}
            self.service._record_action('approval.decide', params, result)
        self.pending[params['approval_id']][0].set_result(params['decision'])
        return result
