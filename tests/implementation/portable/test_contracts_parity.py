import json
from pathlib import Path
import shutil
import subprocess

from forge.application.models import ContractError, ErrorKind, canonical_hash, strict_loads, validate, validate_request

ROOT = Path(__file__).resolve().parents[3]


def test_python_and_typescript_validate_the_same_real_fixtures_and_hash():
    directory = ROOT / 'contracts/v1'
    manifest = json.loads((directory / 'examples.manifest.json').read_text())
    cases = [{'schema': item['schema'], 'raw': (directory / item['example']).read_text(encoding='utf-8')}
             for item in manifest['examples']]
    cases += [{'schema': item['schema'], 'raw': json.dumps(item['value'])}
              for item in json.loads((directory / 'method-fixtures.json').read_text())['cases']]
    cases += [{'schema': item['schema'], 'raw': json.dumps(item['value'])}
              for item in json.loads((directory / 'additional-fixtures.json').read_text())['cases']]
    cases += [{'schema': None, 'raw': raw} for raw in [
        '{"a":1,"a":2}', '{"x":NaN}', '{"x":1e999}', '"\\ud800"',
        '[' * 33 + '0' + ']' * 33, '{"n":9007199254740992}',
        '"' + 'x' * 1048576 + '"', '{"n":1.0}',
    ]]
    value = {'budget': '2.5', '\U00010000': [1, 2], '\ue000': '中文'}
    requests = []
    for fixture in json.loads((directory / 'method-fixtures.json').read_text())['cases']:
        if fixture['expected'] != 'valid' or not fixture['schema'].endswith('.request'):
            continue
        for principal in ('main', 'renderer'):
            requests.append({'request': {'jsonrpc': '2.0', 'id': 'r1', 'method': fixture['schema'].removesuffix('.request'), 'params': fixture['value']}, 'principal': principal})
    for method in ('__proto__', 'constructor', 'unknown'):
        requests.append({'request': {'jsonrpc': '2.0', 'id': 'r1', 'method': method, 'params': {}}, 'principal': 'renderer'})
    node = shutil.which('node')
    assert node, 'Contract parity requires the Node development toolchain.'
    result = subprocess.run([node, str(ROOT / 'packages/contracts/dist/index.js'), '--verify-fixtures'],
                            input=json.dumps({'cases': cases, 'hash_value': value, 'requests': requests}, ensure_ascii=False),
                            capture_output=True, text=True, encoding='utf-8', cwd=ROOT)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    expected = []
    for case in cases:
        try:
            payload = strict_loads(case['raw'])
            if case['schema']:
                validate(case['schema'], payload)
            expected.append(True)
        except ContractError:
            expected.append(False)
    assert actual['valid'] == expected
    assert actual['hash'] == canonical_hash(value)
    assert actual['error_kinds'] == [kind.value for kind in ErrorKind]
    expected_requests = []
    for item in requests:
        try:
            validate_request(item['request'], item['principal'])
            expected_requests.append('valid')
        except ContractError as error:
            expected_requests.append(error.kind)
    assert actual['requests'] == expected_requests
