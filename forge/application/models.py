"""Strict protocol decoding and validation. No services or model clients are loaded."""

from enum import StrEnum
from hashlib import sha256
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


MAX_FRAME_BYTES = 1024 * 1024
MAX_DEPTH = 32
MAX_SAFE_INTEGER = 2**53 - 1


class ContractError(ValueError):
    def __init__(self, message: str, *, kind: str = 'INVALID_PARAMS', code: int = -32602):
        super().__init__(message)
        self.kind = kind
        self.code = code


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError('Duplicate JSON key', code=-32700)
        result[key] = value
    return result


def _check_tree(value, depth=0):
    if isinstance(value, (dict, list)):
        if depth >= MAX_DEPTH:
            raise ContractError('JSON nesting exceeds 32 containers')
        for child in (value.items() if isinstance(value, dict) else enumerate(value)):
            if isinstance(value, dict):
                if not isinstance(child[0], str):
                    raise ContractError('JSON object keys must be strings')
                _check_tree(child[0], depth + 1)
            _check_tree(child[1], depth + 1)
    elif isinstance(value, str):
        try:
            value.encode('utf-8', errors='strict')
        except UnicodeEncodeError as error:
            raise ContractError('Unpaired Unicode surrogate') from error
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ContractError('JSON number must be finite')
        if value == int(value) and abs(value) > MAX_SAFE_INTEGER:
            raise ContractError('JSON integer exceeds interoperable range')
    elif value is not None and not isinstance(value, bool):
        raise ContractError('Unsupported JSON value')


def strict_loads(raw: str | bytes):
    try:
        encoded = raw.encode('utf-8', errors='strict') if isinstance(raw, str) else raw
        if len(encoded) > MAX_FRAME_BYTES:
            raise ContractError('JSON frame exceeds 1 MiB')
        text = encoded.decode('utf-8', errors='strict')
        # Bound nesting before the platform parser can recurse. Quoted brackets do not count.
        depth, quoted, escaped = 0, False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in '[{':
                depth += 1
                if depth > MAX_DEPTH:
                    raise ContractError('JSON nesting exceeds 32 containers')
            elif char in ']}':
                depth -= 1
        def constant(_):
            raise ContractError('Non-standard JSON number', code=-32700)
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=constant)
        _check_tree(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ContractError('Invalid UTF-8 JSON', code=-32700) from error


def canonical_hash(value) -> str:
    """SHA-256 of codepoint-sorted UTF-8 JSON; configuration decimals use strings."""
    _check_tree(value)
    def normalize(item):
        if isinstance(item, float):
            if not item.is_integer():
                raise ContractError('Canonical configuration decimals must use normalized strings')
            return int(item)
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        return item
    raw = json.dumps(normalize(value), sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return sha256(raw.encode('utf-8')).hexdigest()


_BUNDLE = json.loads(Path(__file__).with_name('_generated_contracts.json').read_text(encoding='utf-8'))
METHODS = _BUNDLE['methods']
BRIDGE_METHODS = _BUNDLE['bridge_methods']
EVENTS = _BUNDLE['events']
_SCHEMAS = _BUNDLE['schemas']
ErrorKind = StrEnum('ErrorKind', {kind: kind for kind in _SCHEMAS['business-error']['properties']['data']['properties']['kind']['enum']})
_REGISTRY = Registry().with_resources((schema['$id'], Resource.from_contents(schema)) for schema in _SCHEMAS.values())
_VALIDATORS = {}


def validate(name: str, value):
    _check_tree(value)
    key = name.removeprefix('schemas/').removesuffix('.schema.json')
    if key not in _VALIDATORS:
        if key not in _SCHEMAS:
            raise ContractError('Unknown contract schema')
        _VALIDATORS[key] = Draft202012Validator(_SCHEMAS[key], registry=_REGISTRY, format_checker=FormatChecker())
    error = next(_VALIDATORS[key].iter_errors(value), None)
    if error:
        # Do not include payload values, secrets, or jsonschema's full instance in errors.
        raise ContractError(f'Contract {key}: {error.validator} failed at /' + '/'.join(map(str, error.absolute_path)))
    if key == 'run-spec':
        if set(value['dataset']['task_ids']) != set(value['dataset']['task_revisions']):
            raise ContractError('Task revisions must match the selected task set')
        if value['budget']['trial_wall_seconds'] < value['budget']['attempt_wall_seconds']:
            raise ContractError('Trial wall budget cannot be smaller than attempt budget')
    if key == 'bundle-manifest':
        paths = [entry['path'].lower() for entry in value['contents']]
        if len(paths) != len(set(paths)):
            raise ContractError('Bundle content paths conflict')
        if sum(entry['size_bytes'] for entry in value['contents']) != value['total_size_bytes']:
            raise ContractError('Bundle total size differs from content inventory')
    if key in ('evaluation.validate.request', 'evaluation.create_run.request'):
        validate('run-spec', value['spec'])
    if key == 'bundle.export.result':
        validate('bundle-manifest', value['manifest'])
    if key == 'event-notification' and len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > 65536:
        raise ContractError('Event batch exceeds 64 KiB')
    return value


def validate_request(request, principal: str):
    validate('rpc-request', request)
    method = METHODS.get(request['method'])
    if method is None:
        raise ContractError('Unknown RPC method', kind='NOT_FOUND', code=-32601)
    if principal not in ('main', 'renderer') or (method['audience'] == 'main' and principal != 'main'):
        raise ContractError('Method requires a trusted Main channel', kind='UNAUTHORIZED', code=-32010)
    if method['mutation'] and 'id' not in request:
        raise ContractError('Mutation requires a request ID')
    validate(method['request_schema'], request['params'])
    return request


def validate_event(value):
    validate('event-envelope', value)
    event = EVENTS.get(value['event_type'])
    if event is None:
        raise ContractError('Unknown event type')
    validate(event['payload_schema'], value['attributes'])
    return value
