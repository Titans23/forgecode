'''Tests for lifecycle hook configuration and command execution.'''

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest

from forge.hooks.config import HookConfigurationError, load_hook_settings
from forge.hooks.manager import HookManager
from forge.hooks.models import HookEvent, HookSettings


def run_hook(manager: HookManager, event: HookEvent):
    return asyncio.run(manager.emit(event))


def test_settings_merge_user_hooks_before_project_hooks(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'repo'
    root.mkdir()
    user = tmp_path / 'user-settings.json'
    user.write_text(
        json.dumps({'hooks': {'SessionStart': [
            {'id': 'user-start', 'command': [sys.executable, '-c', 'pass']}
        ]}}),
        encoding='utf-8',
    )
    project_settings = root / '.forge' / 'settings.json'
    project_settings.parent.mkdir()
    project_settings.write_text(
        json.dumps({'hooks': {'SessionStart': [
            {'id': 'project-start', 'command': [sys.executable, '-c', 'pass']}
        ]}}),
        encoding='utf-8',
    )

    settings = load_hook_settings(root, user_settings_path=user)

    assert [item.id for item in settings.hooks['SessionStart']] == [
        'user-start',
        'project-start',
    ]


def test_invalid_event_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / 'repo'
    settings_path = root / '.forge' / 'settings.json'
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({'hooks': {'NotAnEvent': [
            {'id': 'bad', 'command': ['bad']}
        ]}}),
        encoding='utf-8',
    )

    with pytest.raises(HookConfigurationError):
        load_hook_settings(
            root,
            user_settings_path=tmp_path / 'missing.json',
        )


def test_pre_tool_hook_modifies_arguments_and_injects_context(
    tmp_path: Path,
) -> None:
    code = (
        'import json,sys; data=json.load(sys.stdin); '
        'assert data[\'tool\'][\'name\'] == \'read_file\'; '
        'print(json.dumps({\'decision\':\'allow\','
        '\'updated_arguments\':{\'path\':\'changed.py\'},'
        '\'additional_context\':\'inspect generated code\'}))'
    )
    settings = HookSettings.model_validate(
        {
            'hooks': {
                'PreToolUse': [
                    {
                        'id': 'rewrite',
                        'command': [sys.executable, '-c', code],
                        'matcher': {
                            'tools': ['read_*'],
                            'paths': ['**/*.py'],
                        },
                    }
                ]
            }
        }
    )
    outcome = run_hook(
        HookManager(tmp_path, settings),
        HookEvent(
            name='PreToolUse',
            tool_name='read_file',
            arguments={'path': 'app.py'},
            paths=('app.py',),
        ),
    )

    assert outcome.allowed
    assert outcome.arguments == {'path': 'changed.py'}
    assert outcome.additional_context == ('inspect generated code',)
    assert outcome.executions[0].arguments_modified
    assert outcome.executions[0].context_injected


def test_exit_code_two_denies_blocking_event(tmp_path: Path) -> None:
    settings = HookSettings.model_validate(
        {
            'hooks': {
                'BeforeFileEdit': [
                    {
                        'id': 'protect',
                        'command': [
                            sys.executable,
                            '-c',
                            'import sys; print(\'protected\', '
                            'file=sys.stderr); raise SystemExit(2)',
                        ],
                    }
                ]
            }
        }
    )
    outcome = run_hook(
        HookManager(tmp_path, settings),
        HookEvent(name='BeforeFileEdit', paths=('private/key.txt',)),
    )

    assert not outcome.allowed
    assert outcome.reason == 'protected'
    assert outcome.executions[0].decision == 'deny'


def test_post_event_failure_is_audited_but_cannot_undo_action(
    tmp_path: Path,
) -> None:
    settings = HookSettings.model_validate(
        {
            'hooks': {
                'AfterFileEdit': [
                    {
                        'id': 'broken-formatter',
                        'command': ['command-that-does-not-exist-forge'],
                    }
                ]
            }
        }
    )
    outcome = run_hook(
        HookManager(tmp_path, settings),
        HookEvent(name='AfterFileEdit', paths=('app.py',)),
    )

    assert outcome.allowed
    assert outcome.executions[0].decision == 'error'


def test_after_edit_command_expands_path_without_shell(
    tmp_path: Path,
) -> None:
    target = tmp_path / 'sample.py'
    target.write_text('value=1', encoding='utf-8')
    code = (
        'from pathlib import Path; import sys; '
        'path=Path(sys.argv[1]); '
        'path.write_text(path.read_text(encoding=\'utf-8\')+'
        '\'\\n# formatted\\n\',encoding=\'utf-8\')'
    )
    settings = HookSettings.model_validate(
        {
            'hooks': {
                'AfterFileEdit': [
                    {
                        'id': 'formatter',
                        'command': [
                            sys.executable,
                            '-c',
                            code,
                            '{path}',
                        ],
                        'matcher': {'paths': ['**/*.py']},
                    }
                ]
            }
        }
    )
    outcome = run_hook(
        HookManager(tmp_path, settings),
        HookEvent(name='AfterFileEdit', paths=('sample.py',)),
    )

    assert outcome.allowed
    assert target.read_text(encoding='utf-8').endswith('# formatted\n')


def test_blocking_hook_timeout_fails_closed(tmp_path: Path) -> None:
    settings = HookSettings.model_validate(
        {
            'hooks': {
                'BeforeModelCall': [
                    {
                        'id': 'slow',
                        'command': [
                            sys.executable,
                            '-c',
                            'import time; time.sleep(5)',
                        ],
                        'timeout_seconds': 0.1,
                    }
                ]
            }
        }
    )
    outcome = run_hook(
        HookManager(tmp_path, settings),
        HookEvent(name='BeforeModelCall'),
    )

    assert not outcome.allowed
    assert outcome.executions[0].timed_out
    assert 'timed out' in outcome.reason


def test_hook_output_limit_is_enforced_while_streaming(
    tmp_path: Path,
) -> None:
    settings = HookSettings.model_validate(
        {
            'hooks': {
                'PreToolUse': [
                    {
                        'id': 'noisy',
                        'command': [
                            sys.executable,
                            '-c',
                            'import sys; sys.stdout.write(\'x\' * 1000001)',
                        ],
                    }
                ]
            }
        }
    )
    outcome = run_hook(
        HookManager(tmp_path, settings),
        HookEvent(name='PreToolUse', tool_name='read_file'),
    )

    assert not outcome.allowed
    assert outcome.executions[0].decision == 'error'
    assert 'exceeded 1000000 bytes' in outcome.reason
