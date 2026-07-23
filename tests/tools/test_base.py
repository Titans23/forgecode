'''Tests for the shared M1.3 tool contract.'''

import asyncio
from pathlib import Path

from forge.tools import create_default_registry
from forge.tools.base import ToolResult


def run(coroutine: object) -> ToolResult:
    return asyncio.run(coroutine)  # type: ignore[arg-type]


def test_default_registry_hides_legacy_edit_tools_from_model(tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path)

    assert registry.names == (
        'list_directory',
        'find_files',
        'read_file',
        'grep',
        'write_file',
        'write_file_chunk',
        'replace_text',
        'apply_patch',
        'run_command',
        'verify',
        'git_status',
        'git_diff',
        'explore_repository',
        'finish_task',
    )
    exposed = [definition['name'] for definition in registry.definitions]
    assert exposed == [
        name
        for name in registry.names
        if name not in {'write_file_chunk', 'replace_text'}
    ]
    assert 'write_file_chunk' not in exposed
    assert 'replace_text' not in exposed
    assert all(
        definition['input_schema']['type'] == 'object'
        for definition in registry.definitions
    )
    assert registry.effect('read_file') == 'read_only'
    assert registry.effect('write_file') == 'workspace_write'
    assert registry.effect('write_file_chunk') == 'workspace_write'
    assert registry.effect('replace_text') == 'workspace_write'
    assert registry.effect('apply_patch') == 'workspace_write'
    assert registry.effect('run_command') == 'process'
    assert registry.effect('finish_task') == 'read_only'
    assert registry.effect('missing') is None


def test_registry_returns_structured_unknown_tool_error(
    tmp_path: Path,
) -> None:
    registry = create_default_registry(tmp_path)

    result = run(registry.execute('missing', {}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'unknown_tool'
    assert result.error.details['available_tools'] == list(registry.names)


def test_tool_validation_failure_does_not_raise(tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path)

    result = run(
        registry.execute(
            'read_file',
            {'path': 'README.md', 'unexpected': True},
        )
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'invalid_arguments'
    assert 'Allowed arguments: end_line, path, start_line' in (
        result.error.message
    )
    assert result.error.details['allowed_arguments'] == [
        'end_line',
        'path',
        'start_line',
    ]
    assert result.error.details['required_arguments'] == ['path']
    assert result.error.details['unknown_arguments'] == ['unexpected']
    assert result.error.details['validation_errors'][0]['type'] == (
        'extra_forbidden'
    )


def test_tool_validation_failure_explains_oversized_content(
    tmp_path: Path,
) -> None:
    registry = create_default_registry(tmp_path)

    result = run(
        registry.execute(
            'write_file',
            {'path': 'large.js', 'content': 'x' * 30_001},
        )
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'invalid_arguments'
    assert '`content` has 30001 characters; maximum is 30000' in (
        result.error.message
    )
    assert 'smaller new-file skeleton' in result.error.message
    assert result.error.details['recovery_hint'].startswith(
        'Create a smaller new-file skeleton'
    )


def test_repository_escape_returns_structured_error(tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path)

    result = run(registry.execute('read_file', {'path': '../secret.txt'}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'path_outside_repository'
