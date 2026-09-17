from dataclasses import replace
from hashlib import sha256
import os
import subprocess
import sys

import pytest

from forge.permissions.risk import classify_tool_call
from forge.runtime.check_contracts import inherit_check_arguments, checker_repair_guidance
from forge.runtime.completion import checker_revision_covers, unresolved_verification_failures
from forge.runtime.state import ToolCall, VerificationEvidence


def classify(command, **arguments):
    return classify_tool_call(ToolCall(0, 'probe', 'run_command',
                                      {'command': command, **arguments}), 'process')


@pytest.mark.parametrize('command', [
    'command -v sudo', 'id; command -v apt-get || true; command -v sudo || true; cat /etc/os-release',
    'printf "sudo unavailable"', "echo 'sudo id'", 'echo ok # sudo id',
])
def test_literal_privilege_mentions_do_not_deny(command):
    assert not classify(command).hard_deny


@pytest.mark.parametrize('command', [
    'sudo id', 'command -v sudo; sudo id', 'command -v sudo\nsudo id',
    'echo $(sudo id)', 'printf "`sudo id`"', "sh -c 'sudo id'",
    'env sudo id', 'echo ok && /usr/bin/sudo id', 'runas /user:admin cmd',
    "printf 'sudo id' | sh", 'command -v sudo | xargs -I{} {} id',
    'command -v sudo; $CMD id',
])
def test_actual_or_nested_privilege_stays_denied(command):
    assert classify(command).hard_deny


def test_script_stdin_is_not_exempted_as_literal_output():
    assert classify('python -', stdin='import os; os.system("sudo id")').hard_deny


def test_delete_targets_stop_at_newline_and_keep_quoted_path():
    result = classify('rm -rf "build cache"\nSRC=$(printf hello)\nprintf done')
    assert not result.hard_deny
    assert result.targets == ('build cache',)
    assert classify('rm -rf build\nrm -rf /').hard_deny
    assert classify('rm -rf "$TARGET"\nprintf done').hard_deny
    assert classify("sh -c 'rm -rf build\nrm -rf /'").hard_deny


def test_isolated_installed_import_ignores_task_modules(tmp_path):
    # Use the actual installed package; no injected PYTHONPATH may be needed.
    for name in ('benchmark.py', 'forge.py'):
        (tmp_path / name).write_text('raise RuntimeError("task module imported")\n')
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run([sys.executable, '-I', '-c',
        'import benchmark.harbor.process_supervisor; import benchmark.harbor.run_forge; import forge'],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def old_check():
    return VerificationEvidence('python checker.py', '.', 1, .1, False, 0,
                                verification_id='old', requirement_ids=('r1',))


def test_unstructured_retry_inherits_requirements_without_rewriting_program():
    old = old_check()
    args = inherit_check_arguments(dict(command=old.command, inherit_checks_from=['old'],
                                       revision_reason='Retry repaired checker file'), [old])
    assert args['requirement_ids'] == ['r1']
    assert args['supersedes'] == ['old']
    new = replace(old, exit_code=0, verification_id='new', supersedes=('old',),
                  revision_reason='Retry repaired checker file')
    assert checker_revision_covers(old, new)
    assert not unresolved_verification_failures((old, new))
    assert not checker_revision_covers(old, replace(new, exit_code=1))
    assert not checker_revision_covers(old, replace(new, requirement_ids=()))


@pytest.mark.parametrize('changed', [
    {'command': 'echo OK'}, {'stdin': 'print("OK")'}, {'cwd': 'other'},
])
def test_unstructured_repair_cannot_silently_replace_assertions(changed):
    old = old_check()
    with pytest.raises(ValueError, match='Cannot change an unstructured checker'):
        inherit_check_arguments(dict(command=old.command, inherit_checks_from=['old'],
            revision_reason='repair') | changed, [old])
    new = replace(old, exit_code=0, verification_id='new', supersedes=('old',),
                  revision_reason='repair',
                  **({'stdin_sha256': sha256(changed['stdin'].encode()).hexdigest()}
                     if 'stdin' in changed else changed))
    assert not checker_revision_covers(old, new)
    assert unresolved_verification_failures((old, new))
    assert 'cannot automatically discharge' in checker_repair_guidance(old)
