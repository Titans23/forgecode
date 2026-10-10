"""Run a genuine read/repair/test turn through the private Engine without API keys."""
import argparse
import asyncio
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from forge.application.harness_adapter import LocalTrustedBackend
from forge.application.models import strict_loads
from forge.application.services import ApplicationServices
from forge.application.sessions import turn_view
from forge.config import ForgeConfig
from forge.engine.methods import manifest_hash
from forge.engine.persistence import Store, new_id
from forge.engine.test_profile import scripted_profile
from forge.sessions.store import SessionStore
from forge.testing.rpc_client import RpcClient


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'tests' / 'implementation' / 'fixtures'


def fixture_manifests():
    """Validate published fixture assets; this does not mark their acceptance pass."""
    expected = {'fix-python-add', 'cancel-long-command', 'stale-evidence', 'restricted-path', 'provider-interruption', 'eval-mini-bundle'}
    manifests = {}
    for path in sorted(FIXTURES.glob('*/manifest.json')):
        value = strict_loads(path.read_bytes())
        if set(value) != {'schema_version', 'fixture_id', 'origin', 'inputs', 'acceptance_tasks', 'assertions'} or value['schema_version'] != 'forge.fixture.v1' or value['fixture_id'] != path.parent.name or value['origin'] != 'synthetic-fixture':
            raise ValueError('Invalid fixture manifest')
        if not value['inputs'] or not value['assertions'] or not value['acceptance_tasks']:
            raise ValueError('Fixture requirements are empty')
        for relative in value['inputs']:
            target = (path.parent / relative).resolve(strict=True)
            if not target.is_relative_to(path.parent.resolve()) or not target.is_file():
                raise ValueError('Unsafe fixture input')
        manifests[value['fixture_id']] = value
    if manifests.keys() != expected:
        raise ValueError('Six required fixtures are missing')
    return manifests


def seed_demo(directory):
    fixture_manifests()
    project = directory / 'project'
    shutil.copytree(FIXTURES / 'fix-python-add' / 'project', project)
    value = strict_loads((FIXTURES / 'fix-python-add' / 'script.json').read_bytes())
    executable = subprocess.list2cmdline([sys.executable]) if os.name == 'nt' else shlex.quote(sys.executable)
    for response in value['responses']:
        for call in response.get('tool_calls', []):
            if 'command' in call['arguments']:
                call['arguments']['command'] = call['arguments']['command'].replace('${PYTHON}', executable)
    profile = scripted_profile(value)
    with Store(directory / 'data') as store:
        service = ApplicationServices(store, profile_id='test-profile', credentials=profile.credentials,
            mode='local-trusted', backend=LocalTrustedBackend())
        workspace = service.open_workspace(project)
        workspace = service.authorize_workspace(workspace['id'], expected_revision=0, allow=True)
        connection = service.put_connection(ForgeConfig(api_key='scripted-test-only', model_id='scripted-demo', max_tokens=1024))
        policy_id = new_id('policy')
        service.put_policy({'schema_version': 'forge.sandbox.policy.v1', 'policy_id': policy_id, 'workspace_id': workspace['id'],
            'filesystem': {'read_mode': 'backend_default_with_protected_paths', 'read_roots': [str(project)],
                'write_roots': [str(project)], 'protected_paths': [], 'deny_overrides_allow': True, 'reject_unsafe_links': True},
            'network': {'mode': 'deny_direct', 'allowed_domains': [], 'dns_isolation_required': False},
            'limits': {'memory_bytes': None, 'disk_bytes': None, 'pids': None, 'wall_time_seconds': 60,
                'command_output_bytes': 1048576, 'session_artifact_bytes': 104857600},
            'environment_keys': [], 'fallback': 'deny', 'session_mutation': 'replace_session'})
        budget = service.put_budget({'max_model_calls': 5, 'max_tool_calls': 8, 'wall_seconds': 60})
        params = {'client_action_id': new_id('act'), 'workspace_id': workspace['id'], 'expected_workspace_revision': 1,
            'connection_id': connection, 'policy_id': policy_id, 'budget_profile_id': budget}
    value['credentials'][connection] = 'scripted-test-only'
    fixture = directory / 'scripted.json'
    fixture.write_text(json.dumps(value, indent=2), encoding='utf-8')
    return params, fixture


async def launch_engine(directory, fixture, diagnostic):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'forge.engine',
        '--data-dir', str(directory / 'data'), '--profile', 'test', '--execution-mode', 'local-trusted',
        '--scripted-fixture', str(fixture), cwd=ROOT, env=environment,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=diagnostic, limit=1048577)
    client = RpcClient(process)
    try:
        hello = await client.call('system.initialize', {'protocol': {'major': 1, 'minor': 0},
            'client_build': 'offline-demo', 'expected_manifest_hash': manifest_hash(), 'profile': 'test'})
        if hello['readiness']['status'] != 'degraded' or 'scripted-model' not in hello['capabilities']['features']:
            raise RuntimeError('Engine did not use explicit offline test profile')
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    return client


async def execute_demo(directory, fixture, params, *, disconnect_before_response=False):
    events = {}
    with (directory / 'engine.log').open('wb') as diagnostic:
        client = await launch_engine(directory, fixture, diagnostic)
        try:
            session = await client.call('session.create', params)
            turn = {k: v for k, v in params.items() if k != 'workspace_id'}
            turn.update(client_action_id=new_id('act'), session_id=session['session_id'],
                input=[{'type': 'text', 'text': 'Fix integer addition in calculator.py. Preserve the tests; run unittest before and after the repair.'}])
            await client.call('events.subscribe', {'scope': {'kind': 'session', 'id': session['session_id']}})
            if disconnect_before_response:
                # Lose the start response after an explicit, durable drain request.
                # A bare Main EOF cancels work; it is not permission to continue.
                await client.send('session.start_turn', turn)
                await client.send('system.shutdown', {'client_action_id': new_id('act'), 'mode': 'drain'})
                client.process.stdin.close()
                if await client.drain() != 0:
                    raise RuntimeError('Disconnected Engine failed to drain')
                events.update(client.events)
                client = await launch_engine(directory, fixture, diagnostic)
                recovered = await client.call('action.get', {'client_action_id': turn['client_action_id'], 'method': 'session.start_turn'})
                accepted = recovered['result']
            else:
                accepted = await client.call('session.start_turn', turn)
            async with asyncio.timeout(60):
                while True:
                    snapshot = await client.call('session.get', {'session_id': session['session_id']})
                    if snapshot['turns'][0]['state'] == 'finished':
                        break
                    await asyncio.sleep(0.02)
            # Same ID retry must return the original accepted object, never reexecute.
            reused = await client.call('session.start_turn', turn)
            if not reused['reused_existing_action'] or {**reused, 'reused_existing_action': False} != accepted:
                raise RuntimeError('Idempotent start response changed')
            await client.call('system.shutdown', {'client_action_id': new_id('act'), 'mode': 'drain'})
            if await client.drain() != 0:
                raise RuntimeError('Engine failed to drain')
            events.update(client.events)
            return snapshot['turns'][0], events
        finally:
            if client.process.returncode is None:
                client.process.kill()
            await client.process.wait()


def run_demo(output_dir=None, *, disconnect_before_response=False):
    directory = Path(output_dir).resolve() if output_dir is not None else Path(tempfile.mkdtemp(prefix='forgecode-demo-')).resolve()
    if output_dir is not None:
        directory.mkdir(parents=True, exist_ok=False)
    params, fixture = seed_demo(directory)
    tests = directory / 'project' / 'test_calculator.py'
    before_hash = sha256(tests.read_bytes()).hexdigest()
    turn, events = asyncio.run(execute_demo(directory, fixture, params, disconnect_before_response=disconnect_before_response))
    with Store(directory / 'data') as store:
        turn = turn_view(store, turn['turn_id'])
        row = store.connection.execute('SELECT s.legacy_ref FROM sessions s JOIN turns t ON t.session_id=s.id WHERE t.id=?', (turn['turn_id'],)).fetchone()
        native = SessionStore(directory / 'project', data_root=store.data_dir / 'harness')
        history = native.load(row['legacy_ref']).verification_history
        journal_path = native.directory / (row['legacy_ref'] + '.jsonl')
        turn_count = store.connection.execute('SELECT count(*) FROM turns').fetchone()[0]
        facts = store.connection.execute('SELECT count(*) FROM events').fetchone()[0]
    after_hash = sha256(tests.read_bytes()).hexdigest()
    observed = [asdict(item) for item in history]
    native_outcome = turn['native_outcome'] or {}
    report = native_outcome.get('completion_report') or {}
    passed = set(report.get('passed_checks', []))
    checks = {'single_turn': turn_count == 1, 'original_tests_preserved': before_hash == after_hash,
        'real_tests_failed_before': len(history) == 2 and history[0].exit_code != 0,
        'real_tests_passed_after': len(history) == 2 and history[1].success,
        'current_execution_evidence': len(history) == 2 and history[1].verification_id in passed and history[1].workspace_revision > history[0].workspace_revision,
        'harness_completed': turn['outcome'] == 'completed',
        'real_tool_sequence': native_outcome.get('tool_calls') == ['read_file', 'read_file', 'verify', 'apply_patch', 'verify', 'finish_task'],
        'durable_facts': facts > 0}
    value = {'schema_version': 'forge.demo-report.v1', 'origin': 'scripted', 'mode': 'local-trusted',
        'sandbox_acceptance': 'blocked', 'sandbox_reason': 'Real host tools; no OS sandbox isolation',
        'eligible_for_benchmark': False, 'status': 'pass' if all(checks.values()) else 'fail', 'checks': checks,
        'directory': str(directory), 'turn': turn, 'verification_history': observed,
        'history_freshness': 'unknown after reload; execution report retains original acceptance facts',
        'durable_event_count': facts, 'received_event_count': len(events),
        'artifacts': {str(path.relative_to(directory)): sha256(path.read_bytes()).hexdigest() for path in
            (directory / 'project' / 'calculator.py', tests, journal_path, directory / 'data' / 'engine.sqlite3')}}
    report_path = directory / 'demo-report.json'
    with report_path.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, help='New directory for the private fixture copy and evidence')
    args = parser.parse_args(argv)
    value = run_demo(args.output_dir)
    print(json.dumps(value, indent=2))
    return 0 if value['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
