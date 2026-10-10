"""Confirmed connection metadata and revision-bound, memory-only Engine credentials."""
import asyncio
import ipaddress
import json
from urllib.parse import urlsplit, urlunsplit

import httpx

from forge.application.models import ContractError, canonical_hash, validate
from forge.config import DEFAULT_MODEL_MAX_TOKENS, DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS
from forge.engine.persistence import encoded, new_id, utc_now


def refuse(message, kind='UNAUTHORIZED'):
    raise ContractError(message, kind=kind, code=-32010)


def endpoint(value):
    try:
        parsed = urlsplit(value)
        if '\\' in value or any(ord(char) < 32 or ord(char) == 127 for char in value) or parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii').lower()
        port = parsed.port
        if parsed.scheme == 'http' and host != 'localhost' and not ipaddress.ip_address(host).is_loopback:
            raise ValueError()
        authority = '[' + host + ']' if ':' in host else host
        if port and port != (443 if parsed.scheme == 'https' else 80): authority += ':' + str(port)
        return urlunsplit((parsed.scheme, authority, parsed.path.rstrip('/'), '', ''))
    except (ValueError, UnicodeError):
        refuse('Connection needs HTTPS or an explicitly selected loopback URL without embedded credentials', 'INVALID_PARAMS')


class BoundCredentials:
    def __init__(self, store, trusted_source):
        self.store, self.trusted_source = store, trusted_source
        self.values, self.revoked = {}, set()

    def scope(self, connection_id):
        row = self.store.connection.execute('SELECT revision,configuration_json FROM connections WHERE id=?', (connection_id,)).fetchone()
        return canonical_hash(dict(row)) if row else None

    def resolve(self, connection_id):
        if connection_id in self.values:
            scope, secret = self.values[connection_id]
            return secret if scope == self.scope(connection_id) else None
        return None if connection_id in self.revoked or self.scope(connection_id) is None else self.trusted_source.resolve(connection_id)

    def install(self, connection_id, secret):
        scope = self.scope(connection_id)
        if scope is None: refuse('Connection not found', 'NOT_FOUND')
        self.values[connection_id] = (scope, secret)
        self.revoked.discard(connection_id)

    def revoke(self, connection_id):
        self.values.pop(connection_id, None)
        self.revoked.add(connection_id)


class ConnectionService:
    def __init__(self, service, nonces):
        self.service, self.store, self.nonces = service, service.store, nonces
        if not isinstance(service.credentials, BoundCredentials):
            service.credentials = BoundCredentials(self.store, service.credentials)
        self.credentials = service.credentials
        self.testing = {}

    def row(self, connection_id, revision=None, *, allow_new=False):
        row = self.store.connection.execute('SELECT * FROM connections WHERE id=?', (connection_id,)).fetchone()
        if row is None and not (allow_new and revision == 0): refuse('Connection not found', 'NOT_FOUND')
        if row is not None and revision is not None and row['revision'] != revision: refuse('Connection revision changed', 'STALE_REVISION')
        return row

    def view(self, row):
        metadata = json.loads(row['configuration_json'])
        return validate('connection', {'connection_id': row['id'], 'revision': row['revision'], 'provider': metadata['provider'],
            'base_url': metadata['base_url'], 'requested_model': metadata['model_id'], 'credential_present': bool(self.credentials.resolve(row['id']))})

    def desired(self, params):
        if params['provider'] not in ('anthropic', 'openai_responses', 'deepseek'): refuse('Unsupported model adapter', 'INVALID_PARAMS')
        self.row(params['connection_id'], params['expected_revision'], allow_new=True)
        return {'connection_id': params['connection_id'], 'expected_revision': params['expected_revision'], 'provider': params['provider'],
            'base_url': endpoint(params['base_url']), 'requested_model': params['requested_model'], 'engine_epoch': self.store.epoch}

    def prepare_set(self, params):
        validate('connection.prepare_set.request', params)
        return self.nonces.issue('connection_set', params['connection_id'], canonical_hash(self.desired(params)))

    def _mutate(self, method, params, operation):
        with self.store.transaction():
            existing = self.service._existing_action(method, params)
            if existing is not None:
                return {**existing, **({'reused_existing_action': True} if 'reused_existing_action' in existing else {})}
            result = operation()
            self.service._record_action(method, params, result)
        return result

    def _cancel_connection(self, connection_id):
        for task in tuple(self.testing.get(connection_id, ())): task.cancel()
        for turn_id in list(self.service.running):
            row = self.store.connection.execute('SELECT c.normalized_json FROM turns t JOIN configuration_snapshots c '
                "ON c.id=json_extract(t.config_json,'$.snapshot_id') WHERE t.id=?", (turn_id,)).fetchone()
            if row and json.loads(row[0])['connection_id'] == connection_id:
                self.service.cancel_turn({'turn_id': turn_id, 'client_action_id': new_id('act'), 'reason': 'Connection credential revoked'})

    def set(self, params):
        validate('connection.set.request', params)
        def operation():
            desired = self.desired(params)
            self.nonces.consume(params['confirmation_token'], 'connection_set', params['connection_id'], canonical_hash(desired))
            row = self.row(params['connection_id'], params['expected_revision'], allow_new=True)
            previous = json.loads(row['configuration_json']) if row else {}
            metadata = {**previous, 'model_id': desired['requested_model'], 'provider': desired['provider'], 'base_url': desired['base_url'],
                'max_tokens': previous.get('max_tokens', DEFAULT_MODEL_MAX_TOKENS), 'context_window': previous.get('context_window'),
                'reasoning_effort': previous.get('reasoning_effort'),
                'request_timeout_seconds': previous.get('request_timeout_seconds', str(DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS))}
            self.store.connection.execute('INSERT INTO connections VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET '
                'revision=excluded.revision,configuration_json=excluded.configuration_json',
                (params['connection_id'], params['expected_revision'] + 1, encoded(metadata)))
            result = self.view(self.row(params['connection_id']))
            result['credential_present'] = False
            return result
        existing = self.service._existing_action('connection.set', params)
        if existing is not None: return existing
        result = self._mutate('connection.set', params, operation)
        self.credentials.revoke(params['connection_id'])
        self._cancel_connection(params['connection_id'])
        return result

    def inject(self, params):
        validate('credentials.inject.request', params)
        self.row(params['connection_id'], params['expected_revision'])
        if not params['credential'].strip() or any(value in params['credential'] for value in ('\r', '\n', '\0')):
            refuse('Credential is empty or contains header control characters', 'INVALID_PARAMS')
        existing = self.service._existing_action('credentials.inject', params)
        if existing is not None:
            return {'installed': bool(self.credentials.resolve(params['connection_id'])), 'reused_existing_action': True}
        def operation():
            self.credentials.install(params['connection_id'], params['credential'])
            return {'installed': True, 'reused_existing_action': False}
        try: return self._mutate('credentials.inject', params, operation)
        except BaseException:
            self.credentials.revoke(params['connection_id'])
            raise

    def clear(self, params):
        validate('credentials.clear.request', params)
        self.row(params['connection_id'], params['expected_revision'])
        result = self._mutate('credentials.clear', params, lambda: {'cleared': True, 'reused_existing_action': False})
        if not result['reused_existing_action']:
            self.credentials.revoke(params['connection_id'])
            self._cancel_connection(params['connection_id'])
        return result

    def delete(self, params):
        validate('connection.delete.request', params)
        def operation():
            self.row(params['connection_id'], params['expected_revision'])
            self.store.connection.execute('DELETE FROM connections WHERE id=?', (params['connection_id'],))
            return {'deleted': True, 'reused_existing_action': False}
        result = self._mutate('connection.delete', params, operation)
        if not result['reused_existing_action']:
            self.credentials.revoke(params['connection_id'])
            self._cancel_connection(params['connection_id'])
        return result

    def prepare_test(self, params):
        validate('connection.prepare_test.request', params)
        row = self.row(params['connection_id'], params['expected_revision'])
        return self.nonces.issue('connection_test', params['connection_id'], canonical_hash(dict(row)))

    async def test(self, params):
        validate('connection.test.request', params)
        existing = self.service._existing_action('connection.test', params)
        if existing is not None:
            observation = self.store.connection.execute('SELECT status FROM connection_test_results WHERE diagnostic_id=?',
                (existing['diagnostic_id'],)).fetchone()
            return {**existing, 'status': observation['status'] if observation else 'blocked', 'reused_existing_action': True}
        row = self.row(params['connection_id'], params['expected_revision'])
        key = self.credentials.resolve(params['connection_id'])
        if not key: refuse('Connection credential is unavailable', 'CONNECTION_UNAVAILABLE')
        metadata = json.loads(row['configuration_json'])
        base = endpoint(metadata['base_url'])
        path = '/v1/models' if metadata['provider'] == 'anthropic' and not urlsplit(base).path.endswith('/v1') else '/models'
        provisional = {'status': 'blocked', 'diagnostic_id': new_id('diag'), 'reused_existing_action': False}
        with self.store.transaction():
            self.nonces.consume(params['confirmation_token'], 'connection_test', params['connection_id'], canonical_hash(dict(row)))
            self.service._record_action('connection.test', params, provisional)
            self.store.connection.execute('INSERT INTO connection_test_results VALUES(?,?,?,?,NULL)',
                (provisional['diagnostic_id'], self.store.epoch, 'blocked', utc_now()))
        headers = {'x-api-key': key, 'anthropic-version': '2023-06-01'} if metadata['provider'] == 'anthropic' else {'Authorization': 'Bearer ' + key}
        status = 'blocked'
        task = asyncio.current_task()
        self.testing.setdefault(params['connection_id'], set()).add(task)
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
                async with client.stream('GET', base + path, headers=headers) as response:
                    status = 'pass' if 200 <= response.status_code < 300 else 'fail'
        except httpx.HTTPError: status = 'fail'
        except asyncio.CancelledError: status = 'blocked'
        finally:
            self.testing[params['connection_id']].discard(task)
            if not self.testing[params['connection_id']]: self.testing.pop(params['connection_id'])
            result = {**provisional, 'status': status}
            with self.store.transaction():
                self.store.connection.execute('UPDATE connection_test_results SET status=?,finished_at=? WHERE diagnostic_id=? AND owner_epoch=?',
                    (status, utc_now(), provisional['diagnostic_id'], self.store.epoch))
        return result
