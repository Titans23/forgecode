"""Freeze and resolve RunSpec references before any worker can claim a trial."""
from copy import deepcopy
from decimal import Decimal
import json

from forge.application.models import ContractError, canonical_hash, validate


INFRASTRUCTURE_ERRORS = ('runner_unavailable', 'runner_crash', 'environment_setup', 'provider_unavailable', 'grader_infrastructure')

REFERENCES = {
    'source': ('source', 'source_snapshot', None),
    'model_parameters': ('model', 'parameters', 'model-parameters'),
    'harness': ('harness', 'configuration', 'harness-config'),
    'environment': ('execution', 'environment_snapshot', 'environment'),
    'policy': ('execution', 'sandbox_policy', 'sandbox-policy'),
    'capabilities': ('execution', 'sandbox_capabilities', 'capability-report'),
    'network_cache': ('execution', 'network_cache_configuration', 'network-cache'),
    'grader': ('grader', 'configuration', 'grader-configuration'),
    'grader_environment': ('grader', 'environment', 'environment'),
    'pricing': ('observability', 'pricing_snapshot', None),
}


def resolve_snapshot(store, reference, schema=None):
    row = store.connection.execute('SELECT hash,normalized_json FROM configuration_snapshots WHERE id=?',
        (reference['snapshot_id'],)).fetchone()
    if row is None:
        raise ContractError('RunSpec snapshot is unavailable', kind='NOT_FOUND', code=-32010)
    value = json.loads(row['normalized_json'])
    if row['hash'] != reference['sha256'] or canonical_hash(value) != reference['sha256']:
        raise ContractError('RunSpec snapshot hash changed', kind='STALE_REVISION', code=-32010)
    if schema:
        validate(schema, value)
    return value


def validate_resolved_spec(spec, values):
    """Apply the same semantic checks to stored and portable resolved plans.

    Hashes establish byte identity, not consistency between a budget, a model,
    its environment and the snapshots. This function grants no execution rights.
    """
    validate('run-spec', spec)
    if len(spec['dataset']['task_ids']) * spec['protocol']['repeats'] > 10000:
        raise ContractError('P0 run supports at most 10000 planned trials', kind='ARTIFACT_LIMIT', code=-32010)
    if len(json.dumps(spec, ensure_ascii=False).encode()) > 524288:
        raise ContractError('RunSpec exceeds the bounded plan size', kind='ARTIFACT_LIMIT', code=-32010)
    if not isinstance(values, dict) or set(values) != set(REFERENCES):
        raise ContractError('Resolved RunSpec snapshot index is incomplete')
    for name, (group, key, schema) in REFERENCES.items():
        if canonical_hash(values[name]) != spec[group][key]['sha256']:
            raise ContractError('Resolved snapshot content differs from immutable RunSpec', kind='STALE_REVISION', code=-32010)
        if schema:
            validate(schema, values[name])
    pricing = values['pricing']
    if not isinstance(pricing,dict):
        raise ContractError('Pricing snapshot requires an object')
    if 'rates' in pricing:
        from forge.observability.usage_ledger import PriceBook
        try:
            PriceBook(pricing)
        except ValueError as error:
            raise ContractError('Pricing snapshot is invalid') from error
    else:
        validate('pricing', pricing)
        if (pricing['provider'], pricing['model']) != (spec['model']['provider'], spec['model']['requested_model']):
            raise ContractError('Pricing is for a different provider or model')
    if pricing['currency'] != spec['budget']['currency']:
        raise ContractError('Pricing and spend budget currencies differ')
    harness, budget = values['harness'], spec['budget']
    if budget['trial_wall_seconds'] > 604800:
        raise ContractError('P0 trial wall budget is bounded to seven days')
    if harness['max_delivery_repairs'] != spec['harness']['max_delivery_repairs'] or harness['parent_budget'] != {
            'max_model_calls': budget['max_model_requests_per_attempt'],
            'max_tool_calls': budget['max_tool_calls_per_attempt'], 'wall_seconds': budget['attempt_wall_seconds']}:
        raise ContractError('Harness and attempt budgets must match; repairs receive no extra budget')
    if values['environment']['platform'] != spec['execution']['target_platform']:
        raise ContractError('Execution target and frozen environment differ')
    if (values['grader']['adapter_id'], values['grader']['revision']) != (spec['grader']['adapter_id'], spec['grader']['revision']):
        raise ContractError('Grader configuration identity differs')
    parameters = values['model_parameters']
    if parameters['temperature'] is not None and Decimal(parameters['temperature']) > 2:
        raise ContractError('Temperature is outside the supported interval')
    if parameters['top_p'] is not None and Decimal(parameters['top_p']) > 1:
        raise ContractError('top_p is outside the supported interval')
    cache = values['network_cache']
    if cache['cache_mode'] == 'shared_read_only' and cache['cache_snapshot'] is None:
        raise ContractError('Shared cache requires an immutable snapshot')
    if cache['network_mode'] == 'deny_direct' and cache['allowed_domains']:
        raise ContractError('Denied network must not contain an implicit domain allowlist')
    if spec['model_mode'] == 'scripted_mock' and spec['model']['provider'] != 'scripted_mock':
        raise ContractError('Scripted protocol must identify its synthetic provider')


def freeze_spec(store, spec, *, check_connection=True):
    spec = deepcopy(validate('run-spec', spec))
    values = {name: resolve_snapshot(store, spec[group][key])
        for name, (group, key, _) in REFERENCES.items()}
    validate_resolved_spec(spec, values)
    if values['network_cache']['cache_snapshot']:
        resolve_snapshot(store, values['network_cache']['cache_snapshot'])
    if spec['model_mode'] == 'live' and check_connection:
        connection = store.connection.execute('SELECT * FROM connections WHERE id=?', (spec['model']['connection_id'],)).fetchone()
        if connection is None:
            raise ContractError('Model connection is unavailable', kind='CONNECTION_UNAVAILABLE', code=-32010)
        metadata = json.loads(connection['configuration_json'])
        if connection['revision'] != spec['model']['connection_revision'] or (metadata['provider'], metadata['model_id']) != (
                spec['model']['provider'], spec['model']['requested_model']):
            raise ContractError('Model connection revision or identity changed', kind='STALE_REVISION', code=-32010)
    return spec, values, canonical_hash(spec)
