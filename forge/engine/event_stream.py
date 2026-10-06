"""Consistent SQLite snapshots and bounded, acked replay of durable events."""
import base64
from hashlib import sha256
import hmac
import json
import secrets

from forge.application.models import ContractError, validate
from forge.application.sessions import session_view
from forge.engine.persistence import encoded, new_id


def workspace_view(row):
    return {'workspace_id': row['id'], 'path': row['canonical_path'], 'canonical_path': row['canonical_path'],
            'revision': row['revision'], 'trust': row['trust'], 'diagnostics': []}


def accepted_view(row):
    return {'turn_id': row['id'], 'state': row['state'], 'outcome': row['outcome'],
            'accepted': True, 'reused_existing_action': False}


class EventStream:
    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.subscriptions = {}
        self.generation = self.store.connection.execute("SELECT value FROM store_meta WHERE key='generation'").fetchone()[0]
        row = self.store.connection.execute("SELECT value FROM store_meta WHERE key='cursor_key'").fetchone()
        self.ephemeral_key = row is None and self.store.read_only
        if self.ephemeral_key:
            self.key = secrets.token_bytes(32)
            return
        if row is None:
            with self.store.transaction():
                self.store.connection.execute('INSERT INTO store_meta VALUES(?,?)', ('cursor_key', secrets.token_hex(32)))
            row = self.store.connection.execute("SELECT value FROM store_meta WHERE key='cursor_key'").fetchone()
        self.key = bytes.fromhex(row[0])

    def cursor(self, scope, seq):
        raw = encoded({'generation': self.generation, 'profile': self.service.profile_id,
                       'scope': scope, 'seq': str(seq)}).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip('=') + '.' + hmac.new(self.key, raw, sha256).hexdigest()

    def decode_cursor(self, cursor, scope):
        try:
            body, signature = cursor.split('.')
            raw = base64.urlsafe_b64decode(body + '=' * (-len(body) % 4))
            if not hmac.compare_digest(hmac.new(self.key, raw, sha256).hexdigest(), signature):
                raise ValueError()
            value = json.loads(raw)
            seq = int(value['seq'])
            if value != {'generation': self.generation, 'profile': self.service.profile_id, 'scope': scope, 'seq': str(seq)} or seq < 0:
                raise ValueError()
            return seq
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise ContractError('Cursor is invalid for this store, profile or scope', kind='INVALID_CURSOR', code=-32010) from None

    def check_scope(self, scope):
        validate('scope', scope)
        kind = scope['kind']
        if kind == 'all':
            return
        table = {'workspace': 'workspaces', 'session': 'sessions', 'turn': 'turns', 'run': 'runs'}[kind]
        if not self.store.connection.execute(f'SELECT 1 FROM {table} WHERE id=?', (scope['id'],)).fetchone():
            raise ContractError('Subscription scope not found', kind='NOT_FOUND', code=-32010)

    def snapshot(self, scope):
        self.check_scope(scope)
        kind = scope['kind']
        workspace_id = scope.get('id') if kind == 'workspace' else None
        session_id = scope.get('id') if kind == 'session' else None
        turn_id = scope.get('id') if kind == 'turn' else None
        if turn_id:
            session_id = self.store.connection.execute('SELECT session_id FROM turns WHERE id=?', (turn_id,)).fetchone()[0]
        if session_id:
            workspace_id = self.store.connection.execute('SELECT workspace_id FROM sessions WHERE id=?', (session_id,)).fetchone()[0]
        workspaces = [] if kind == 'run' else self.store.connection.execute('SELECT * FROM workspaces WHERE (? IS NULL OR id=?) ORDER BY rowid LIMIT 101',
            (workspace_id, workspace_id)).fetchall()
        sessions = [] if kind == 'run' else self.store.connection.execute('SELECT s.id FROM sessions s JOIN session_configurations c ON c.session_id=s.id '
            'WHERE (? IS NULL OR workspace_id=?) AND (? IS NULL OR s.id=?) ORDER BY s.rowid LIMIT 101',
            (workspace_id, workspace_id, session_id, session_id)).fetchall()
        turns = [] if kind == 'run' else self.store.connection.execute('SELECT t.* FROM turns t JOIN sessions s ON s.id=t.session_id '
            'WHERE (? IS NULL OR s.workspace_id=?) AND (? IS NULL OR s.id=?) AND (? IS NULL OR t.id=?) ORDER BY t.rowid LIMIT 101',
            (workspace_id, workspace_id, session_id, session_id, turn_id, turn_id)).fetchall()
        # The client must narrow scope or use collection paging when a snapshot is incomplete.
        gap = any(len(rows) > 100 for rows in (workspaces, sessions, turns))
        return {'scope': scope, 'workspaces': [workspace_view(r) for r in workspaces[:100]],
                'sessions': [session_view(self.store, r[0]) for r in sessions[:100]],
                'turns': [accepted_view(r) for r in turns[:100]]}, gap

    def subscribe(self, params):
        validate('events.subscribe.request', params)
        if len(self.subscriptions) >= 16:
            raise ContractError('Subscription limit reached', kind='ARTIFACT_LIMIT', code=-32010)
        scope = params['scope']
        self.store.connection.execute('BEGIN')
        try:
            snapshot, gap = self.snapshot(scope)
            high = self.store.connection.execute('SELECT COALESCE(MAX(store_seq),0) FROM events').fetchone()[0]
            minimum = self.store.connection.execute('SELECT COALESCE(MIN(store_seq),1) FROM events').fetchone()[0]
            gap = gap or minimum > 1 or self.ephemeral_key
            start = self.decode_cursor(params['after_cursor'], scope) if 'after_cursor' in params else high
            if start > high or start < minimum - 1:
                raise ContractError('Cursor is outside retained history', kind='INVALID_CURSOR', code=-32010)
            subscription = new_id('sub')
            cursor = self.cursor(scope, start)
            result = {'subscription_id': subscription, 'snapshot': snapshot, 'cursor': cursor,
                      'high_watermark': str(high), 'history_gap': gap}
            validate('events.subscribe.result', result)
            self.subscriptions[subscription] = {'scope': scope, 'scanned': start, 'acked': start, 'pending': None}
            return result
        finally:
            self.store.connection.execute('ROLLBACK')

    def _subscription(self, subscription_id):
        if subscription_id not in self.subscriptions:
            raise ContractError('Subscription not found', kind='NOT_FOUND', code=-32010)
        return self.subscriptions[subscription_id]

    def next_batch(self, subscription_id):
        subscription = self._subscription(subscription_id)
        if subscription['pending'] is not None:
            return None
        scope = subscription['scope']
        events = []
        scanned = subscription['scanned']
        # Bound scanning as well as output, so cancel/control requests keep making progress.
        for event in self.store.events(after=scanned, limit=100):
            matches = scope['kind'] == 'all' or event[scope['kind'] + '_id'] == scope['id']
            candidate = events + [event] if matches else events
            body = {'jsonrpc': '2.0', 'method': 'events.batch', 'params': {'subscription_id': subscription_id,
                    'cursor': self.cursor(scope, int(event['store_seq'])), 'events': candidate}}
            if candidate and len(encoded(body).encode('utf-8')) > 65536:
                if not events:
                    raise ContractError('Event needs an artifact reference before streaming', kind='ARTIFACT_LIMIT', code=-32010)
                break
            scanned = int(event['store_seq'])
            events = candidate
        subscription['scanned'] = scanned
        if not events:
            return None
        cursor = self.cursor(scope, scanned)
        batch = {'jsonrpc': '2.0', 'method': 'events.batch', 'params': {'subscription_id': subscription_id,
                 'cursor': cursor, 'events': events}}
        validate('event-notification', batch)
        subscription['pending'] = cursor
        return batch

    def ack(self, params):
        validate('events.ack.request', params)
        subscription = self._subscription(params['subscription_id'])
        sequence = self.decode_cursor(params['cursor'], subscription['scope'])
        if sequence == subscription['acked']:
            return {'acknowledged': True}
        if params['cursor'] != subscription['pending']:
            raise ContractError('Ack must match an emitted batch', kind='INVALID_CURSOR', code=-32010)
        subscription['acked'] = sequence
        subscription['pending'] = None
        return {'acknowledged': True}

    def unsubscribe(self, params):
        validate('events.unsubscribe.request', params)
        self.subscriptions.pop(params['subscription_id'], None)
        return {'unsubscribed': True}
