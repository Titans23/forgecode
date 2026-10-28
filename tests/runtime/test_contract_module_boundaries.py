"""Evidence reports work without loading an execution gate or provider SDK."""
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize('entry', ['forge.runtime.check_contracts', 'forge.runtime.delivery'])
def test_standalone_contract_repair_and_report_preserve_evidence(entry):
    script = textwrap.dedent('''
        import builtins
        from dataclasses import asdict, replace
        import importlib
        import json
        import sys

        blocked = ('forge.runtime.completion', 'forge.runtime.model_client',
                   'forge.runtime.providers', 'anthropic', 'openai')
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if any(name == prefix or name.startswith(prefix + '.') for prefix in blocked):
                raise AssertionError('Evidence diagnostics imported an execution dependency: ' + name)
            return original_import(name, *args, **kwargs)
        builtins.__import__ = guarded_import
        importlib.import_module(sys.argv[1])

        from forge.runtime.check_contracts import (
            inherit_check_arguments, checker_revision_covers, unresolved_verification_failures)
        from forge.runtime.delivery import completion_report, requirement_observation, requirement_reason
        from forge.runtime.state import VerificationEvidence

        old = VerificationEvidence('python old.py', '.', 1, .1, False, 0,
            verification_id='old', requirement_ids=('r1',), check_signature='original', output_checks=({
                'key': 'quality', 'operator': 'ge', 'expected': .95,
                'requirement': 'accurate result', 'requirement_id': 'r1', 'expected_source': 'reference A'},))
        arguments = inherit_check_arguments({'command': 'python fixed.py', 'inherit_checks_from': ['old'],
            'revision_reason': 'Repair checker while retaining the original assertion'}, [old])
        fixed = replace(old, command=arguments['command'], exit_code=0, verification_id='fixed',
            supersedes=tuple(arguments['supersedes']), revision_reason=arguments['revision_reason'],
            output_checks=tuple(arguments['output_checks']), asserted_requirement_ids=('r1',))
        assert checker_revision_covers(old, fixed)
        assert unresolved_verification_failures((old, fixed)) == ()
        weaker = replace(fixed, output_checks=({**fixed.output_checks[0], 'expected': .8},))
        assert not checker_revision_covers(old, weaker)
        assert unresolved_verification_failures((old, weaker)) == (old,)
        try:
            inherit_check_arguments({**arguments, 'inherit_checks_from': ['old'],
                'output_checks': list(weaker.output_checks)}, [old])
        except ValueError:
            pass
        else:
            raise AssertionError('Weakened assertion was inherited')

        observation = requirement_observation({'id': 'r1', 'source_quote': 'accurate result'}, (fixed,), 0, 0)
        assert observation['state'] == 'recorded_assertion_passed'
        assert requirement_reason(observation) is None
        stale = requirement_observation({'id': 'r1'}, (replace(fixed, environment_epoch=1),), 0, 0)
        assert stale['state'] == 'stale_evidence' and requirement_reason(stale)
        report = completion_report(status='completed', evidence=(old, fixed), workspace_revision=0,
            environment_epoch=0, reasons=(), has_contract=True, usage_complete=False)
        assert json.loads(json.dumps(asdict(report))) == {
            'run_status': 'completed', 'agent_assessment': 'completed', 'acceptance_status': 'met',
            'verification_status': 'passed_checks', 'passed_checks': ['fixed'], 'failed_checks': [],
            'historical_checks': ['old'], 'unmet_requirements': [], 'usage_complete': False}
        assert report.summary() == ('Verification: passed_checks; 1 passed, 0 failed, 1 historical/expired. '
                                    'Caller acceptance: met. Token usage is incomplete.')
        assert not any(name == prefix or name.startswith(prefix + '.')
                       for name in sys.modules for prefix in blocked)
    ''')
    completed = subprocess.run([sys.executable, '-c', script, entry],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_legacy_contract_and_report_exports_reference_the_same_objects():
    from forge.runtime import check_contracts, completion, delivery, state

    for name in ('verification_command_key', 'verification_obligation_key',
                 'unresolved_verification_failures', 'checker_revision_covers'):
        assert getattr(completion, name) is getattr(check_contracts, name)
    assert delivery.CompletionReport is state.CompletionReport
