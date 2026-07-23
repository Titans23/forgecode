'''Tests for repository filesystem and search tools.'''

import asyncio
import hashlib
from pathlib import Path

from forge.tools.filesystem import (
    ListDirectoryTool,
    ReadFileTool,
    ReplaceTextTool,
    WriteFileChunkTool,
    WriteFileTool,
)
from forge.tools.search import FindFilesTool, GrepTool
from forge.tools.base import ToolResult


def run(coroutine: object) -> ToolResult:
    return asyncio.run(coroutine)  # type: ignore[arg-type]


def create_repository(root: Path) -> None:
    (root / 'src').mkdir()
    (root / 'src' / 'app.py').write_text(
        'first\nTODO: repair parser\nthird\n',
        encoding='utf-8',
    )
    (root / 'src' / 'app.ts').write_text(
        '// TODO: TypeScript\n',
        encoding='utf-8',
    )
    (root / 'README.md').write_text('# Example\n', encoding='utf-8')
    (root / 'node_modules').mkdir()
    (root / 'node_modules' / 'ignored.py').write_text(
        'TODO: ignored\n',
        encoding='utf-8',
    )


def create_protected_repository(root: Path) -> None:
    (root / '.forge').mkdir()
    (root / '.forge' / 'trajectory.jsonl').write_text(
        'CONTROL_SECRET\n',
        encoding='utf-8',
    )
    (root / '.git').mkdir()
    (root / '.git' / 'config').write_text(
        'CONTROL_SECRET\n',
        encoding='utf-8',
    )
    (root / '.env').write_text('ENV_SECRET\n', encoding='utf-8')
    (root / '.env.local').write_text('ENV_SECRET\n', encoding='utf-8')
    (root / '.env.production').write_text(
        'ENV_SECRET\n',
        encoding='utf-8',
    )
    (root / '.env.example').write_text(
        'SAFE_PLACEHOLDER=true\n',
        encoding='utf-8',
    )
    (root / '.gitignore').write_text('.env.local\n', encoding='utf-8')
    (root / 'app.py').write_text(
        'VISIBLE_MARKER = True\n',
        encoding='utf-8',
    )


def test_list_directory_sorts_directories_before_files(
    tmp_path: Path,
) -> None:
    create_repository(tmp_path)

    result = run(ListDirectoryTool(tmp_path).run({'path': '.'}))

    assert result.success is True
    assert result.content.splitlines() == [
        'node_modules/',
        'src/',
        'README.md',
    ]
    assert result.metadata['entry_count'] == 3
    assert result.metadata['total'] == 3
    assert result.metadata['truncated'] is False


def test_list_directory_accepts_bounded_max_results(
    tmp_path: Path,
) -> None:
    create_repository(tmp_path)

    result = run(
        ListDirectoryTool(tmp_path).run({'path': '.', 'max_results': 2})
    )

    assert result.success is True
    assert result.content.splitlines() == ['node_modules/', 'src/']
    assert result.metadata == {
        'path': '.',
        'entry_count': 2,
        'total': 3,
        'truncated': True,
    }


def test_list_directory_rejects_out_of_bounds_max_results(
    tmp_path: Path,
) -> None:
    create_repository(tmp_path)
    tool = ListDirectoryTool(tmp_path)

    for max_results in (0, 1_001):
        result = run(
            tool.run({'path': '.', 'max_results': max_results})
        )

        assert result.success is False
        assert result.error is not None
        assert result.error.code == 'invalid_arguments'


def test_list_directory_hides_control_and_environment_paths(
    tmp_path: Path,
) -> None:
    create_protected_repository(tmp_path)

    result = run(ListDirectoryTool(tmp_path).run({'path': '.'}))

    assert result.success is True
    assert result.content.splitlines() == [
        '.env.example',
        '.gitignore',
        'app.py',
    ]
    assert result.metadata['entry_count'] == 3


def test_read_file_supports_inclusive_line_ranges(tmp_path: Path) -> None:
    create_repository(tmp_path)

    result = run(
        ReadFileTool(tmp_path).run(
            {'path': 'src/app.py', 'start_line': 2, 'end_line': 3}
        )
    )

    assert result.success is True
    assert result.content == (
        '     2 | TODO: repair parser\n'
        '     3 | third'
    )
    source = (tmp_path / 'src' / 'app.py').read_bytes()
    assert result.metadata == {
        'path': 'src/app.py',
        'start_line': 2,
        'end_line': 3,
        'total_lines': 3,
        'sha256': hashlib.sha256(source).hexdigest(),
        'characters': len(source.decode('utf-8')),
    }


def test_read_file_rejects_an_inverted_range(tmp_path: Path) -> None:
    create_repository(tmp_path)

    result = run(
        ReadFileTool(tmp_path).run(
            {'path': 'src/app.py', 'start_line': 3, 'end_line': 2}
        )
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'invalid_arguments'


def test_read_file_rejects_control_and_sensitive_environment_paths(
    tmp_path: Path,
) -> None:
    create_protected_repository(tmp_path)
    tool = ReadFileTool(tmp_path)

    for path in (
        '.forge/trajectory.jsonl',
        '.git/config',
        '.env',
        '.env.local',
        '.env.production',
    ):
        result = run(tool.run({'path': path}))

        assert result.success is False
        assert result.error is not None
        assert result.error.code == 'protected_path'


def test_read_file_allows_public_env_example_and_gitignore(
    tmp_path: Path,
) -> None:
    create_protected_repository(tmp_path)
    tool = ReadFileTool(tmp_path)

    env_example = run(tool.run({'path': '.env.example'}))
    gitignore = run(tool.run({'path': '.gitignore'}))

    assert env_example.success is True
    assert 'SAFE_PLACEHOLDER=true' in env_example.content
    assert gitignore.success is True
    assert '.env.local' in gitignore.content


def test_write_file_creates_and_atomically_replaces_small_text(
    tmp_path: Path,
) -> None:
    tool = WriteFileTool(tmp_path)

    created = run(tool.run({'path': 'game.html', 'content': 'first'}))
    replaced = run(
        tool.run(
            {
                'path': 'game.html',
                'content': 'second',
                'expected_sha256': created.metadata['sha256'],
            }
        )
    )

    assert created.success is True
    assert created.metadata['created'] is True
    assert replaced.success is True
    assert replaced.metadata['created'] is False
    assert (tmp_path / 'game.html').read_text(encoding='utf-8') == 'second'
    assert not list(tmp_path.glob('*.forge-tmp'))


def test_write_file_requires_current_hash_for_existing_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'game.js'
    path.write_text('const value = 1;\n', encoding='utf-8')
    tool = WriteFileTool(tmp_path)

    missing = run(tool.run({'path': 'game.js', 'content': 'replacement\n'}))
    stale = run(
        tool.run(
            {
                'path': 'game.js',
                'content': 'replacement\n',
                'expected_sha256': '0' * 64,
            }
        )
    )

    assert missing.success is False
    assert missing.error is not None
    assert missing.error.code == 'write_requires_expected_sha256'
    assert stale.success is False
    assert stale.error is not None
    assert stale.error.code == 'write_file_changed'
    assert path.read_text(encoding='utf-8') == 'const value = 1;\n'


def test_write_file_rejects_placeholder_and_drastic_replacement(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'game.js'
    original = 'const value = 1;\n' * 100
    path.write_text(original, encoding='utf-8')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    tool = WriteFileTool(tmp_path)

    placeholder = run(
        tool.run(
            {
                'path': 'game.js',
                'content': '...',
                'expected_sha256': digest,
            }
        )
    )
    drastic = run(
        tool.run(
            {
                'path': 'game.js',
                'content': 'small replacement\n',
                'expected_sha256': digest,
            }
        )
    )

    assert placeholder.success is False
    assert placeholder.error is not None
    assert placeholder.error.code == 'suspicious_full_file_replacement'
    assert drastic.success is False
    assert drastic.error is not None
    assert drastic.error.code == 'suspicious_full_file_replacement'
    assert path.read_text(encoding='utf-8') == original


def test_write_file_rejects_content_over_30000_characters(
    tmp_path: Path,
) -> None:
    result = run(
        WriteFileTool(tmp_path).run(
            {'path': 'large.html', 'content': 'x' * 30_001}
        )
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'invalid_arguments'
    assert not (tmp_path / 'large.html').exists()


def test_write_file_chunk_assembles_ordered_chunks_atomically(
    tmp_path: Path,
) -> None:
    tool = WriteFileChunkTool(tmp_path)
    first_content = 'a' * 20_000
    second_content = '世界' * 6_000
    complete = first_content + second_content
    digest = hashlib.sha256(complete.encode('utf-8')).hexdigest()

    first = run(
        tool.run(
            {
                'path': 'large.js',
                'content': first_content,
                'offset': 0,
                'truncate': True,
            }
        )
    )
    second = run(
        tool.run(
            {
                'path': 'large.js',
                'content': second_content,
                'offset': first.metadata['next_offset'],
                'final': True,
                'expected_sha256': digest,
            }
        )
    )

    assert first.success is True
    assert first.metadata['next_offset'] == 20_000
    assert second.success is True
    assert second.metadata['next_offset'] == 32_000
    assert second.metadata['sha256'] == digest
    assert (tmp_path / 'large.js').read_text(encoding='utf-8') == complete
    assert not list(tmp_path.glob('*.forge-tmp'))


def test_write_file_chunk_rejects_offset_and_hash_without_writing(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'large.js'
    path.write_text('original', encoding='utf-8')
    tool = WriteFileChunkTool(tmp_path)

    wrong_offset = run(
        tool.run({'path': 'large.js', 'content': 'x', 'offset': 3})
    )
    wrong_hash = run(
        tool.run(
            {
                'path': 'new.js',
                'content': 'payload',
                'offset': 0,
                'truncate': True,
                'final': True,
                'expected_sha256': '0' * 64,
            }
        )
    )

    assert wrong_offset.success is False
    assert wrong_offset.error is not None
    assert wrong_offset.error.code == 'chunk_offset_mismatch'
    assert path.read_text(encoding='utf-8') == 'original'
    assert wrong_hash.success is False
    assert wrong_hash.error is not None
    assert wrong_hash.error.code == 'chunk_hash_mismatch'
    assert not (tmp_path / 'new.js').exists()


def test_replace_text_requires_one_exact_occurrence(tmp_path: Path) -> None:
    path = tmp_path / 'game.js'
    path.write_text('const gravity = 1;\n', encoding='utf-8')
    tool = ReplaceTextTool(tmp_path)

    replaced = run(
        tool.run(
            {
                'path': 'game.js',
                'old_text': 'gravity = 1',
                'new_text': 'gravity = 0.08',
            }
        )
    )
    missing = run(
        tool.run(
            {
                'path': 'game.js',
                'old_text': 'missing',
                'new_text': 'value',
            }
        )
    )

    assert replaced.success is True
    assert 'gravity = 0.08' in path.read_text(encoding='utf-8')
    assert missing.success is False
    assert missing.error is not None
    assert missing.error.code == 'text_not_found'
    assert missing.error.details['occurrences'] == 0


def test_replace_text_reports_whitespace_only_near_miss(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'game.js'
    path.write_text('  const hp = 130 + wave * 18;\n', encoding='utf-8')

    result = run(
        ReplaceTextTool(tmp_path).run(
            {
                'path': 'game.js',
                'old_text': 'const hp = 130 +wave * 18;',
                'new_text': 'const hp = 150 + wave * 18;',
            }
        )
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'text_not_found'
    assert result.error.details['whitespace_only_mismatch'] is True
    assert result.error.details['closest_start_line'] == 1
    assert result.error.details['closest_text'] == (
        '  const hp = 130 + wave * 18;'
    )
    assert 'copy exactly as the next old_text' in result.error.message
    assert '  const hp = 130 + wave * 18;' in result.error.message


def test_replace_text_preserves_crlf_and_normalizes_replacement_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'game.js'
    path.write_bytes(b'first\r\nold value\r\nlast\r\n')

    result = run(
        ReplaceTextTool(tmp_path).run(
            {
                'path': 'game.js',
                'old_text': 'first\nold value',
                'new_text': 'first\nnew value\ninserted',
            }
        )
    )

    assert result.success is True
    assert path.read_bytes() == (
        b'first\r\nnew value\r\ninserted\r\nlast\r\n'
    )


def test_find_files_uses_globs_and_ignores_generated_directories(
    tmp_path: Path,
) -> None:
    create_repository(tmp_path)

    result = run(FindFilesTool(tmp_path).run({'pattern': '*.py'}))

    assert result.success is True
    assert result.content == 'src/app.py'
    assert result.metadata['truncated'] is False


def test_find_files_hides_control_and_environment_paths(
    tmp_path: Path,
) -> None:
    create_protected_repository(tmp_path)

    result = run(FindFilesTool(tmp_path).run({'pattern': '*'}))

    assert result.success is True
    assert result.content.splitlines() == [
        '.env.example',
        '.gitignore',
        'app.py',
    ]


def test_grep_supports_path_and_file_type_filters(tmp_path: Path) -> None:
    create_repository(tmp_path)

    result = run(
        GrepTool(tmp_path).run(
            {
                'pattern': 'todo:',
                'path': 'src',
                'file_types': ['py'],
                'case_sensitive': False,
            }
        )
    )

    assert result.success is True
    assert result.content == 'src/app.py:2:TODO: repair parser'
    assert result.metadata['match_count'] == 1


def test_grep_does_not_scan_control_or_sensitive_environment_paths(
    tmp_path: Path,
) -> None:
    create_protected_repository(tmp_path)
    tool = GrepTool(tmp_path)

    protected = run(tool.run({'pattern': 'CONTROL_SECRET|ENV_SECRET'}))
    public = run(tool.run({'pattern': 'SAFE_PLACEHOLDER|VISIBLE_MARKER'}))

    assert protected.success is True
    assert protected.content == ''
    assert protected.metadata['match_count'] == 0
    assert public.success is True
    assert public.content.splitlines() == [
        '.env.example:1:SAFE_PLACEHOLDER=true',
        'app.py:1:VISIBLE_MARKER = True',
    ]


def test_direct_search_or_listing_of_protected_paths_is_rejected(
    tmp_path: Path,
) -> None:
    create_protected_repository(tmp_path)

    results = (
        run(ListDirectoryTool(tmp_path).run({'path': '.forge'})),
        run(FindFilesTool(tmp_path).run({'path': '.git', 'pattern': '*'})),
        run(GrepTool(tmp_path).run({'path': '.env', 'pattern': 'SECRET'})),
    )

    for result in results:
        assert result.success is False
        assert result.error is not None
        assert result.error.code == 'protected_path'


def test_grep_returns_invalid_regex_as_structured_error(
    tmp_path: Path,
) -> None:
    create_repository(tmp_path)

    result = run(GrepTool(tmp_path).run({'pattern': '['}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'invalid_pattern'
