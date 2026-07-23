'''Tests for shell, Git, and patch tools against temporary repositories.'''

import asyncio
import os
from pathlib import Path
import subprocess
import sys

from forge.tools.base import ToolResult
from forge.tools.git import GitDiffTool, GitLogTool, GitStatusTool
from forge.tools.patch import ApplyPatchTool
from forge.tools.shell import RunCommandTool, run_process
from forge.tools.verify import VerifyTool
from forge.runtime.workspace import WorkspaceTracker


def run(coroutine: object) -> ToolResult:
    return asyncio.run(coroutine)  # type: ignore[arg-type]


def initialize_git_repository(root: Path) -> None:
    subprocess.run(
        ['git', 'init', '--quiet'],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ['git', 'config', 'user.email', 'forge@example.test'],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ['git', 'config', 'user.name', 'ForgeCode Tests'],
        cwd=root,
        check=True,
    )
    (root / 'sample.txt').write_text('old\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'sample.txt'], cwd=root, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'baseline'],
        cwd=root,
        check=True,
    )


def test_run_command_returns_stdout_stderr_exit_code_and_duration(
    tmp_path: Path,
) -> None:
    command = subprocess.list2cmdline(
        [
            sys.executable,
            '-c',
            'import sys; print("out"); print("err", file=sys.stderr)',
        ]
    )

    result = run(RunCommandTool(tmp_path).run({'command': command}))

    assert result.success is True
    assert result.metadata['exit_code'] == 0
    assert result.metadata['stdout'].strip() == 'out'
    assert result.metadata['stderr'].strip() == 'err'
    assert result.metadata['duration_seconds'] >= 0
    assert 'stdout:\nout' in result.content
    assert 'stderr:\nerr' in result.content


def test_run_command_returns_nonzero_exit_as_structured_error(
    tmp_path: Path,
) -> None:
    command = subprocess.list2cmdline(
        [sys.executable, '-c', 'raise SystemExit(7)']
    )

    result = run(RunCommandTool(tmp_path).run({'command': command}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'command_failed'
    assert result.metadata['exit_code'] == 7


def test_run_command_accepts_multiline_script_through_stdin(
    tmp_path: Path,
) -> None:
    command = subprocess.list2cmdline([sys.executable, '-'])
    script = 'values = [1, 2, 3]\nprint(sum(values))\n'

    result = run(
        RunCommandTool(tmp_path).run(
            {'command': command, 'stdin': script}
        )
    )

    assert result.success is True
    assert result.metadata['stdout'].strip() == '6'
    assert result.metadata['stdin_characters'] == len(script)


def test_run_command_does_not_treat_quoted_bit_shift_as_heredoc(
    tmp_path: Path,
) -> None:
    command = subprocess.list2cmdline(
        [sys.executable, '-c', 'print(1 << 2)']
    )

    result = run(RunCommandTool(tmp_path).run({'command': command}))

    assert result.success is True
    assert result.metadata['stdout'].strip() == '4'


def test_run_command_rejects_windows_posix_heredoc(
    tmp_path: Path,
) -> None:
    if os.name != 'nt':
        return

    result = run(
        RunCommandTool(tmp_path).run(
            {'command': "python - <<'PY'\nprint('bad')\nPY"}
        )
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'unsupported_shell_syntax'
    assert 'stdin field' in result.error.message


def test_run_command_rejects_destructive_git_commands(
    tmp_path: Path,
) -> None:
    commands = (
        'git checkout -- game/app.js',
        'git restore game/app.js',
        'git reset --hard',
        'git clean -fd',
        'git -C . restore game/app.js',
    )

    for command in commands:
        result = run(RunCommandTool(tmp_path).run({'command': command}))

        assert result.success is False
        assert result.error is not None
        assert result.error.code == 'destructive_git_command_denied'


def test_run_process_sanitizes_credentials_and_bounds_output(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setenv('FORGE_TEST_API_KEY', 'secret')  # type: ignore[attr-defined]
    environment_command = [
        sys.executable,
        '-c',
        "import os; print(os.getenv('FORGE_TEST_API_KEY', 'missing'))",
    ]
    environment = asyncio.run(
        run_process(
            environment_command,
            cwd=tmp_path,
            timeout_seconds=10,
        )
    )
    output = asyncio.run(
        run_process(
            [sys.executable, '-c', "print('x' * 1000)"],
            cwd=tmp_path,
            timeout_seconds=10,
            max_output_bytes=100,
        )
    )

    assert environment.stdout.strip() == 'missing'
    assert output.stdout_truncated is True
    assert len(output.stdout.encode('utf-8')) == 100
    assert output.stdout_bytes > 100


def test_run_command_stdin_cannot_bypass_write_policy(
    tmp_path: Path,
) -> None:
    command = subprocess.list2cmdline([sys.executable, '-'])
    script = (
        'from pathlib import Path\n'
        "Path('unexpected.txt').write_text('bad')\n"
    )

    result = run(
        RunCommandTool(tmp_path).run(
            {'command': command, 'stdin': script}
        )
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'shell_file_write_denied'
    assert not (tmp_path / 'unexpected.txt').exists()


def test_run_command_allows_stderr_merge_redirection(tmp_path: Path) -> None:
    redirect = '2' + chr(62) + chr(38) + '1'
    result = run(
        RunCommandTool(tmp_path).run(
            {'command': f'echo inspected {redirect}'}
        )
    )

    assert result.success is True


def test_run_command_rejects_repository_reading_shortcuts(
    tmp_path: Path,
) -> None:
    commands = (
        'powershell Get-Content src/app.py',
        'powershell Get-ChildItem src',
        'head -n 20 src/app.py',
        'sed -n 1,20p src/app.py',
    )
    for command in commands:
        result = run(RunCommandTool(tmp_path).run({'command': command}))

        assert result.success is False
        assert result.error is not None
        assert result.error.code == 'shell_file_read_denied'


def test_verify_returns_revision_bound_evidence(tmp_path: Path) -> None:
    denied_commands = [
        'node -e writeFileSync(',
        'python -c Path.write_text(',
        'powershell Set-Content marker',
        'echo changed ' + chr(62) + ' game.html',
    ]
    for denied_command in denied_commands:
        denied = run(
            RunCommandTool(tmp_path).run({'command': denied_command})
        )
        assert denied.success is False
        assert denied.error is not None
        assert denied.error.code == 'shell_file_write_denied'

    initialize_git_repository(tmp_path)
    tracker = WorkspaceTracker(tmp_path)
    asyncio.run(tracker.begin_turn())
    script = 'print(' + repr('verified') + ')'
    command = subprocess.list2cmdline([sys.executable, '-c', script])

    result = run(VerifyTool(tmp_path, tracker).run({'command': command}))

    assert result.success is True
    assert result.metadata['verification'] is True
    assert result.metadata['workspace_revision'] == 0
    assert result.metadata['exit_code'] == 0
    assert 'verified' in result.content


def test_verify_failure_is_structured(tmp_path: Path) -> None:
    tracker = WorkspaceTracker(tmp_path)
    asyncio.run(tracker.begin_turn())
    command = subprocess.list2cmdline(
        [sys.executable, '-c', 'raise SystemExit(3)']
    )

    result = run(VerifyTool(tmp_path, tracker).run({'command': command}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'verification_failed'
    assert result.metadata['exit_code'] == 3


def test_git_log_returns_bounded_history_and_supports_path(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)

    result = run(GitLogTool(tmp_path).run(
        {'max_count': 1, 'path': 'sample.txt'}
    ))

    assert result.success is True
    assert 'baseline' in result.content
    assert result.metadata['max_count'] == 1
    assert result.metadata['path'] == 'sample.txt'


def test_git_status_and_diff_return_real_working_tree_state(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    (tmp_path / 'sample.txt').write_text('changed\n', encoding='utf-8')

    status = run(GitStatusTool(tmp_path).run({}))
    diff = run(GitDiffTool(tmp_path).run({'path': 'sample.txt'}))

    assert status.success is True
    assert ' M sample.txt' in status.content
    assert diff.success is True
    assert '-old' in diff.content
    assert '+changed' in diff.content
    assert 'stdout' not in diff.metadata


def test_git_diff_rejects_large_unscoped_output_without_echoing_it(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    (tmp_path / 'sample.txt').write_text(
        'changed content\n' * 5_000,
        encoding='utf-8',
    )

    result = run(GitDiffTool(tmp_path).run({}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'diff_too_large'
    assert result.error.details['required_argument'] == 'path'
    assert result.metadata['diff_characters'] > 30_000
    assert len(result.content) < 500
    assert 'changed content' not in result.content
    assert 'stdout' not in result.metadata


def test_git_diff_renders_path_limited_untracked_utf8_file(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    untracked = tmp_path / 'play' / 'world.js'
    untracked.parent.mkdir()
    untracked.write_text(
        'const block = 1;\nconst faceCount = 6;\n',
        encoding='utf-8',
    )

    result = run(
        GitDiffTool(tmp_path).run({'path': 'play/world.js'})
    )

    assert result.success is True
    assert result.summary == 'Read untracked file as a new-file Git diff.'
    assert 'diff --git a/play/world.js b/play/world.js' in result.content
    assert '--- /dev/null' in result.content
    assert '+++ b/play/world.js' in result.content
    assert '+const block = 1;' in result.content
    assert '+const faceCount = 6;' in result.content
    assert result.metadata['untracked'] is True
    assert result.metadata['synthetic_diff'] is True


def test_git_diff_rejects_directory_path_instead_of_returning_empty_diff(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    untracked = tmp_path / 'play' / 'world.js'
    untracked.parent.mkdir()
    untracked.write_text('const block = 1;\n', encoding='utf-8')

    result = run(GitDiffTool(tmp_path).run({'path': 'play'}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'git_diff_path_is_directory'
    assert result.error.details['path'] == 'play'
    assert 'concrete changed file' in result.error.message


def test_git_diff_pages_large_path_scoped_untracked_file(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    untracked = tmp_path / 'large.txt'
    untracked.write_text('large content\n' * 3_000, encoding='utf-8')
    tool = GitDiffTool(tmp_path)

    first = run(tool.run({'path': 'large.txt'}))
    second = run(
        tool.run(
            {
                'path': 'large.txt',
                'offset': first.metadata['next_offset'],
                'expected_sha256': first.metadata['diff_sha256'],
            }
        )
    )

    assert first.success is True
    assert first.metadata['paged_diff'] is True
    assert first.metadata['diff_complete'] is False
    assert first.metadata['page_start'] == 0
    assert first.metadata['page_end'] == first.metadata['next_offset']
    assert len(first.content) <= 30_000
    assert second.success is True
    assert second.metadata['diff_complete'] is True
    assert second.metadata['page_start'] == first.metadata['next_offset']
    assert second.metadata['next_offset'] is None
    assert len(second.content) <= 30_000
    assert first.content + second.content
    assert len(first.content + second.content) == first.metadata['diff_characters']


def test_git_diff_rejects_out_of_order_large_file_page(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    (tmp_path / 'sample.txt').write_text(
        'changed content\n' * 5_000,
        encoding='utf-8',
    )
    tool = GitDiffTool(tmp_path)
    first = run(tool.run({'path': 'sample.txt'}))

    result = run(
        tool.run(
            {
                'path': 'sample.txt',
                'offset': first.metadata['next_offset'] + 1,
                'expected_sha256': first.metadata['diff_sha256'],
            }
        )
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'git_diff_page_out_of_order'


def test_git_diff_rejects_continuation_after_diff_changes(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    sample = tmp_path / 'sample.txt'
    sample.write_text('changed content\n' * 5_000, encoding='utf-8')
    tool = GitDiffTool(tmp_path)
    first = run(tool.run({'path': 'sample.txt'}))
    sample.write_text('newer content\n' * 5_000, encoding='utf-8')

    result = run(
        tool.run(
            {
                'path': 'sample.txt',
                'offset': first.metadata['next_offset'],
                'expected_sha256': first.metadata['diff_sha256'],
            }
        )
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'git_diff_changed'


def test_apply_patch_changes_the_file_and_reports_status(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    patch = (
        'diff --git a/sample.txt b/sample.txt\n'
        '--- a/sample.txt\n'
        '+++ b/sample.txt\n'
        '@@ -1 +1 @@\n'
        '-old\n'
        '+new\n'
    )

    result = run(ApplyPatchTool(tmp_path).run({'patch': patch}))

    assert result.success is True
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'new\n'
    assert result.metadata['changed_files'] == ['sample.txt']
    assert 'M sample.txt' in result.content


def test_apply_patch_reports_only_its_targets_in_dirty_repository(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    unrelated = tmp_path / 'unrelated.txt'
    unrelated.write_text('baseline\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'unrelated.txt'], cwd=tmp_path, check=True)
    subprocess.run(
        ['git', 'commit', '--quiet', '-m', 'add unrelated'],
        cwd=tmp_path,
        check=True,
    )
    unrelated.write_text('user change\n', encoding='utf-8')
    patch = (
        '--- a/sample.txt\n'
        '+++ b/sample.txt\n'
        '@@ -1 +1 @@\n'
        '-old\n'
        '+new\n'
    )

    result = run(ApplyPatchTool(tmp_path).run({'patch': patch}))

    assert result.success is True
    assert result.summary == 'Applied patch to 1 target path(s).'
    assert result.metadata['target_paths'] == ['sample.txt']
    assert result.metadata['changed_files'] == ['sample.txt']
    assert 'sample.txt' in result.content
    assert 'unrelated.txt' not in result.content
    assert unrelated.read_text(encoding='utf-8') == 'user change\n'


def test_apply_patch_accepts_codex_envelope_with_multiple_bare_hunks(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    world = tmp_path / 'world.js'
    world.write_text(
        'import { FACE_COLORS } from ./constants.js;\n'
        'const blockDB = new Map();\n'
        'let scene = null;\n',
        encoding='utf-8',
    )
    envelope = (
        '*** Begin ' 'Patch\n'
        '*** Update File: world.js\n'
        '@@\n'
        '-import { FACE_COLORS } from ./constants.js;\n'
        '+import { BT } from ./constants.js;\n'
        '@@\n'
        ' const blockDB = new Map();\n'
        '+const atlasTexture = createAtlasTexture();\n'
        '@@\n'
        ' let scene = null;\n'
        '+const chunkMaterial = createChunkMaterial(atlasTexture);\n'
        '*** End ' 'Patch'
    )

    result = run(ApplyPatchTool(tmp_path).run({'patch': envelope}))

    assert result.success is True
    content = world.read_text(encoding='utf-8')
    assert 'import { BT }' in content
    assert 'const atlasTexture = createAtlasTexture();' in content
    assert 'const chunkMaterial = createChunkMaterial(atlasTexture);' in content
    assert result.metadata['format'] == 'codex_envelope'


def test_apply_patch_codex_envelope_preserves_crlf(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    world = tmp_path / 'world-crlf.js'
    world.write_bytes(
        b'const first = 1;\r\n'
        b'const target = 2;\r\n'
        b'const last = 3;\r\n'
    )
    envelope = (
        '*** Begin ' 'Patch\n'
        '*** Update File: world-crlf.js\n'
        '@@\n'
        ' const first = 1;\n'
        '-const target = 2;\n'
        '+const target = 6;\n'
        '+const added = true;\n'
        ' const last = 3;\n'
        '*** End ' 'Patch'
    )

    result = run(ApplyPatchTool(tmp_path).run({'patch': envelope}))

    assert result.success is True
    assert world.read_bytes() == (
        b'const first = 1;\r\n'
        b'const target = 6;\r\n'
        b'const added = true;\r\n'
        b'const last = 3;\r\n'
    )


def test_apply_patch_classifies_missing_codex_context(tmp_path: Path) -> None:
    initialize_git_repository(tmp_path)
    envelope = (
        '*** Begin ' 'Patch\n'
        '*** Update File: sample.txt\n'
        '@@\n'
        '-not current\n'
        '+new\n'
        '*** End ' 'Patch'
    )

    result = run(ApplyPatchTool(tmp_path).run({'patch': envelope}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'patch_context_not_found'
    assert result.error.details['recommended_tool'] == 'read_file'


def test_apply_patch_detects_copied_read_file_line_numbers(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    envelope = (
        '*** Begin ' 'Patch\n'
        '*** Update File: sample.txt\n'
        '@@\n'
        '-    99 | old\n'
        '+    99 | new\n'
        '*** End ' 'Patch'
    )

    result = run(ApplyPatchTool(tmp_path).run({'patch': envelope}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'patch_contains_read_line_numbers'
    assert result.error.details['prefixed_lines'] == 1
    assert 'Remove the line number' in result.content


def test_apply_patch_classifies_ambiguous_codex_context(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    repeated = tmp_path / 'repeated.txt'
    repeated.write_text('same\nsame\n', encoding='utf-8')
    envelope = (
        '*** Begin ' 'Patch\n'
        '*** Update File: repeated.txt\n'
        '@@\n'
        '-same\n'
        '+changed\n'
        '*** End ' 'Patch'
    )

    result = run(ApplyPatchTool(tmp_path).run({'patch': envelope}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'patch_context_ambiguous'
    assert result.error.details['occurrences'] == 2


def test_apply_patch_codex_envelope_supports_add_and_delete(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    envelope = (
        '*** Begin ' 'Patch\n'
        '*** Delete File: sample.txt\n'
        '*** Add File: added.txt\n'
        '+first\n'
        '+second\n'
        '*** End ' 'Patch'
    )

    result = run(ApplyPatchTool(tmp_path).run({'patch': envelope}))

    assert result.success is True
    assert not (tmp_path / 'sample.txt').exists()
    assert (tmp_path / 'added.txt').read_text(encoding='utf-8') == (
        'first\nsecond\n'
    )


def test_apply_patch_classifies_empty_update_hunk_as_protocol_failure(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    envelope = (
        '*** Begin Patch\n'
        '*** Update File: sample.txt\n'
        '@@\n'
        '*** End Patch\n'
    )

    result = run(ApplyPatchTool(tmp_path).run({'patch': envelope}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'patch_empty_hunk'
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'old\n'


def test_apply_patch_validates_entire_codex_envelope_before_applying(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    envelope = (
        '*** Begin ' 'Patch\n'
        '*** Update File: sample.txt\n'
        '@@\n'
        '-old\n'
        '+new\n'
        '*** Update File: missing.txt\n'
        '@@\n'
        '-missing\n'
        '+changed\n'
        '*** End ' 'Patch'
    )

    result = run(ApplyPatchTool(tmp_path).run({'patch': envelope}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'patch_rejected'
    assert result.metadata['format'] == 'codex_envelope'
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'old\n'


def test_apply_patch_codex_envelope_reuses_repository_path_safety(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    outside = tmp_path.parent / 'outside.txt'
    envelope = (
        '*** Begin ' 'Patch\n'
        '*** Add File: ../outside.txt\n'
        '+unsafe\n'
        '*** End ' 'Patch'
    )

    result = run(ApplyPatchTool(tmp_path).run({'patch': envelope}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'patch_rejected'
    assert not outside.exists()


def test_apply_patch_rejection_does_not_modify_files(tmp_path: Path) -> None:
    initialize_git_repository(tmp_path)

    result = run(
        ApplyPatchTool(tmp_path).run(
            {
                'patch': (
                    '--- a/sample.txt\n'
                    '+++ b/sample.txt\n'
                    '@@ -1 +1 @@\n'
                    '-not-the-current-content\n'
                    '+new\n'
                )
            }
        )
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'patch_rejected'
    assert (tmp_path / 'sample.txt').read_text(encoding='utf-8') == 'old\n'


def test_apply_patch_standard_diff_rejects_protected_env_file(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    patch = (
        'diff --git a/.env b/.env\n'
        'new file mode 100644\n'
        '--- /dev/null\n'
        '+++ b/.env\n'
        '@@ -0,0 +1 @@\n'
        '+SECRET=value\n'
    )

    result = run(ApplyPatchTool(tmp_path).run({'patch': patch}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'patch_rejected'
    assert 'protected' in result.content
    assert not (tmp_path / '.env').exists()


def test_apply_patch_standard_diff_rejects_repository_escape(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    outside = tmp_path.parent / 'standard-outside.txt'
    patch = (
        'diff --git a/../standard-outside.txt '
        'b/../standard-outside.txt\n'
        'new file mode 100644\n'
        '--- /dev/null\n'
        '+++ b/../standard-outside.txt\n'
        '@@ -0,0 +1 @@\n'
        '+unsafe\n'
    )

    result = run(ApplyPatchTool(tmp_path).run({'patch': patch}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'patch_rejected'
    assert 'outside the repository' in result.content
    assert not outside.exists()


def test_apply_patch_description_requires_small_focused_writes(
    tmp_path: Path,
) -> None:
    description = ApplyPatchTool(tmp_path).definition['description']

    assert 'limited to 30000 characters' in description
    assert 'split large changes across focused calls' in description
    assert 'Codex envelope' in description
    assert 'Use write_file only to create a new small file' in description


def test_apply_patch_rejects_payload_over_30000_characters(
    tmp_path: Path,
) -> None:
    result = run(ApplyPatchTool(tmp_path).run({'patch': 'x' * 30_001}))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == 'invalid_arguments'
