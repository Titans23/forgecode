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
    from copy import deepcopy
    from forge.sandbox.capabilities import unavailable_report
    capability = unavailable_report().value
    cases.append({'schema': 'capability-report', 'raw': json.dumps(capability)})
    for invalid in ({'status': 'verified', 'evidence_refs': []}, {'status': 'invented', 'evidence_refs': ['x']},
                    {'status': 'unsupported', 'evidence_refs': [], 'permission': True}):
        value = deepcopy(capability)
        value['verification']['dns_isolation'] = invalid
        cases.append({'schema': 'capability-report', 'raw': json.dumps(value)})
    cases += [{'schema': None, 'raw': raw} for raw in [
        '{"a":1,"a":2}', '{"x":NaN}', '{"x":1e999}', '"\\ud800"',
        '[' * 33 + '0' + ']' * 33, '{"n":9007199254740992}',
        '"' + 'x' * 1048576 + '"', '{"n":1.0}',
    ]]
    from forge.engine.persistence import new_id
    owner = {'engine_epoch': new_id('epoch'), 'sandbox_session_id': new_id('sandbox'), 'execution_id': new_id('exec')}
    output = {'execution_id': owner['execution_id'], 'owner': owner, 'stream': 'stdout', 'sequence': '1',
              'raw_base64': '5Lit', 'text': '中', 'encoding': 'utf-8', 'final': False}
    cases.append({'schema': 'bridge-output', 'raw': json.dumps(output)})
    for field, bad in [('raw_base64', 'not base64!'), ('sequence', '0'), ('stream', 'approval'), ('encoding', 'invented')]:
        cases.append({'schema': 'bridge-output', 'raw': json.dumps({**output, field: bad})})
    cases.append({'schema': 'bridge-output', 'raw': json.dumps({**output, 'grade': 1})})
    command = {'mode': 'argv', 'argv': ['program', ''], 'cwd': str(ROOT), 'environment': {},
               'deadline_utc': '2026-10-07T00:00:00Z', 'output_limit_bytes': 1024}
    cases += [{'schema': 'command-spec', 'raw': json.dumps(command)},
              {'schema': 'command-spec', 'raw': json.dumps({**command, 'argv': ['']})}]
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


def test_lightweight_policy_uses_shared_contract_without_srt():
    policy = json.loads((ROOT/'contracts/v1/examples/sandbox-policy.valid.json').read_text(encoding='utf-8'))
    policy['filesystem']['read_mode'] = 'host_default'
    policy['network'].update(mode='inherit', allowed_domains=[], dns_isolation_required=False)
    from forge.sandbox.workspace_backend import require_workspace_policy
    require_workspace_policy(policy)
    program = """import fs from 'node:fs';
import {validate} from './packages/contracts/dist/index.js';
const policy=JSON.parse(fs.readFileSync(0,'utf8'));validate('sandbox-policy',policy);console.log(JSON.stringify(policy));"""
    result=subprocess.run([shutil.which('node'),'--input-type=module','-e',program],input=json.dumps(policy),
        capture_output=True,text=True,encoding='utf-8',cwd=ROOT,timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == policy
