"""Run the same independent business oracle against ForgeCode and public Pi SDK.

Scripted fixtures measure workflow behavior, not natural-language generation
quality, human effort or model advantage. No provider is contacted.
"""
import argparse
import asyncio
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from forge.agents import AgentDefinition, PluginSpec
from forge.delivery import export_agent, import_agent, load_definition
from forge.validation import check_agent, match_result, owned_command, receipt_status

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


async def compare(output, runtime):
    output, runtime = Path(output).resolve(), Path(runtime).resolve()
    output.mkdir(parents=True, exist_ok=False)
    acceptance = {'cases': [
        {'id': 'normal', 'tool': 'summarize_expenses', 'arguments': {'csv': 'category,amount\nmeal,25.50\ntravel,50\n'},
         'expect': {'json': {'total': '75.50', 'rejected': 0}}},
        {'id': 'rules', 'tool': 'summarize_expenses', 'arguments': {'csv': 'category,amount\ntravel,100.01\nalcohol,10\nmeal,-1\n'},
         'expect': {'json': {'total': '0.00', 'rejected': 3}}}]}
    (output / 'acceptance.json').write_text(json.dumps(acceptance, indent=2), encoding='utf-8')
    source = output / 'forge-plugin'
    shutil.copytree(ROOT / 'examples/agents/expenses', source)
    agent = AgentDefinition(name='expenses', base='core', plugins=(PluginSpec(str(source), trusted=True),))
    original = (source / 'plugin.py').read_text(encoding='utf-8')
    pi_original = (HERE / 'expenses.mjs').read_text(encoding='utf-8')
    pi_extension = output / 'expenses.mjs'
    environment = {k: v for k, v in os.environ.items() if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}}
    environment.update(HOME=str(output), USERPROFILE=str(output), TMP=str(output), TEMP=str(output))
    records, first_receipt, first_pi_hash, last_receipt = [], None, None, None
    for phase in ('baseline', 'fault', 'repair', 'model-change', 'transfer'):
        if phase == 'transfer':
            export_agent(agent, acceptance, output / 'agent.zip', receipt=last_receipt)
            import_agent(output / 'agent.zip', output / 'imported')
            agent = load_definition(output / 'imported/agent.json', trust=True)
            # Source artifacts are a supported extension delivery form on both sides.
            shutil.copy2(pi_extension, output / 'imported/expenses.mjs')
            pi_extension = output / 'imported/expenses.mjs'
        else:
            (source / 'plugin.py').write_text(original.replace("'100'", "'1000'") if phase == 'fault' else original, encoding='utf-8')
            pi_extension.write_text(pi_original.replace('limit = 100;', 'limit = 1000;') if phase == 'fault' else pi_original, encoding='utf-8')
            if phase == 'model-change':
                agent = replace(agent, role_models={'main': 'alternate-scripted-model'})
        started = time.monotonic()
        forge = await check_agent(agent, acceptance, work_dir=output / 'checks')
        forge_seconds = time.monotonic() - started
        pi_hash = sha256(pi_extension.read_bytes()).hexdigest()
        payload = {'runtime': str(runtime), 'directory': str(output / ('pi-' + phase)), 'extension': str(pi_extension),
                   'modelId': 'alternate-scripted-model' if phase in ('model-change', 'transfer') else None,
                   'cases': [{k: v for k, v in c.items() if k != 'expect'} for c in acceptance['cases']]}
        started = time.monotonic()
        process = await owned_command([shutil.which('node') or 'node', str(HERE / 'pi_probe.mjs')],
            cwd=output, environment=environment, payload=json.dumps(payload).encode(), timeout=90)
        pi_seconds = time.monotonic() - started
        try:
            pi = json.loads(process['stdout'].splitlines()[-1]) if process['exit_code'] == 0 else {}
        except (ValueError, IndexError):
            pi = {}
        pi_valid = process['cleanup']['state'] == 'clean' and process['exit_code'] == 0 and bool(pi) and all(
            c['id'] in pi.get('results', {}) for c in acceptance['cases'])
        pi_pass = pi_valid and all(
            match_result(pi.get('results', {}).get(c['id']), c['expect']) for c in acceptance['cases'])
        records.append({'phase': phase, 'forge': {'status': forge['status'], 'seconds': forge_seconds,
            'framework_status': forge['interface']['status'], 'cases': forge['business']['cases'],
            'previous_receipt': receipt_status(first_receipt, agent, acceptance) if first_receipt else None},
            'pi': {'status': 'passed' if pi_pass else 'failed', 'seconds': pi_seconds, 'sdk_version': pi.get('sdk_version'),
                'execution_valid': pi_valid, 'cases': [{'id': c['id'], 'status': 'passed' if match_result(pi.get('results', {}).get(c['id']), c['expect']) else 'failed',
                    'actual': pi.get('results', {}).get(c['id'])} for c in acceptance['cases']],
                'code_sha256': pi_hash, 'previous_artifact_unchanged': first_pi_hash == pi_hash if first_pi_hash else None,
                'scripted_requests': pi.get('scripted_requests'), 'process_cleanup': process['cleanup'],
                'error': process['stderr'][-4000:] if not pi else None}})
        last_receipt = forge
        if phase == 'baseline':
            first_receipt, first_pi_hash = forge, pi_hash
        (output / 'observations.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    expected = ['passed', 'failed', 'passed', 'passed', 'passed']
    result = {'status': 'passed' if all([r[side]['status'] for r in records] == expected for side in ('forge', 'pi')) and
              all(r['forge']['framework_status'] == 'passed' and r['pi']['execution_valid'] for r in records) else 'failed',
        'schema_version': 'forge.workflow.comparison.v1', 'observations': records, 'model_api_calls': 0,
        'forge_revision': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip(),
        'pi_dependency_lock_sha256': sha256((runtime / 'package-lock.json').read_bytes()).hexdigest(),
        'human_effort': {'status': 'unmeasured', 'reason': 'Scripted fixture; no natural-language generation or human study'},
        'model_behavior_evidence': 'unverified; deterministic checks remain reusable after model configuration changes',
        'cost_and_tokens': None, 'core_modifications_for_customization': {'forge': 0, 'pi': 0},
        'interpretation': 'Both use the same controller-owned oracle. Pi uses public SDK/extensions with its built-in tools available. '
            'No superiority, live-model performance or human-step advantage is established. Transfer is a fresh working directory on the same installed SDK.'}
    (output / 'comparison.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pi-runtime', type=Path, default=HERE)
    args = parser.parse_args()
    result = asyncio.run(compare(args.output, args.pi_runtime))
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
