'''Tests for the working evidence actually included in model context.'''

import pytest

from forge.context.working import FileEvidence, WorkingState
from forge.runtime.state import ToolCall
from forge.tools.base import ToolResult


def observe_read(state, path, *, revision=0, start=1, end=20, total=251):
    call = ToolCall(0, path, 'read_file', {'path': path})
    state.observe(call, ToolResult.ok('Read file.', content='source text belongs in tool history',
        metadata={'path': path, 'start_line': start, 'end_line': end, 'total_lines': total}), revision)


@pytest.mark.parametrize('second_start', [40, 41])
def test_adjacent_and_overlapping_reads_merge_visible_ranges(second_start):
    state = WorkingState()
    observe_read(state, 'play/js/player.js', end=40, total=316)
    observe_read(state, 'play/js/player.js', start=second_start, end=316, total=316)
    observe_read(state, 'play/js/player.js', start=21, end=120, total=316)

    suffix = state.system_suffix()
    assert suffix.count('play/js/player.js') == 1
    assert 'lines 1-316; total 316' in suffix
    assert 'source text belongs in tool history' not in suffix
    assert state.evidence_paths == ('play/js/player.js',)


def test_working_evidence_suffix_is_bounded():
    state = WorkingState()
    for index in range(75):
        state.files[(0, f'src/file_{index}.py')] = FileEvidence(
            path=f'src/file_{index}.py', revision=0, total_lines=1, covered_ranges=[(1, 1)])
    for index in range(30):
        state.directories.add((0, f'src/dir_{index}'))

    suffix = state.system_suffix()
    assert '25 older file entries omitted' in suffix
    assert '10 older directory entries omitted' in suffix
    assert suffix.count('@ revision') == 70


def test_observation_releases_result_and_source_text_but_keeps_range_evidence():
    import gc
    import weakref

    class ObservedResult(ToolResult):
        pass

    class SourceText(str):
        pass

    state = WorkingState()
    source = SourceText('large source content\n' * 10_000)
    result = ObservedResult.ok('Read file.', content=source,
        metadata={'path': 'src/app.py', 'start_line': 1, 'end_line': 10_000, 'total_lines': 10_000})
    result_ref, source_ref = weakref.ref(result), weakref.ref(source)
    state.observe(ToolCall(0, 'read', 'read_file', {'path': 'src/app.py'}), result, 0)

    del result, source
    gc.collect()

    assert result_ref() is None
    assert source_ref() is None
    assert state.evidence_paths == ('src/app.py',)
    assert 'src/app.py @ revision 0: lines 1-10000; total 10000' in state.system_suffix()


def test_grep_records_only_unique_unread_hits():
    state = WorkingState()
    observe_read(state, 'src/app.py')
    call = ToolCall(0, 'grep', 'grep', {'path': 'src', 'pattern': 'main'})
    result = ToolResult.ok('Found matches.',
        content='src/app.py:10:def main():\nsrc/app.py:25:main()\nsrc/lib.py:20:main()\nmalformed')
    state.observe(call, result, 0)
    before = state.system_suffix()
    state.observe(call, result, 0)
    state.observe(call, ToolResult.ok('No matches.'), 0)

    assert state.system_suffix() == before
    assert state.evidence_paths == ('src/app.py', 'src/lib.py')
    assert 'search hit src/app.py:10' not in before
    assert 'search hit src/app.py:25' in before
    assert 'search hit src/lib.py:20' in before


def test_find_files_does_not_rediscover_an_already_read_path():
    state = WorkingState()
    observe_read(state, 'play/js/player.js')
    call = ToolCall(0, 'find', 'find_files', {'path': 'play/js', 'pattern': '*.js'})
    result = ToolResult.ok('Found files.', content='play/js/player.js\nplay/js/world.js\nplay/js/world.js\n.')
    state.observe(call, result, 0)
    state.observe(call, result, 0)

    assert state.evidence_paths == ('play/js/player.js', 'play/js/world.js')
    assert 'discovered play/js/player.js' not in state.system_suffix()
    assert state.system_suffix().count('discovered play/js/world.js') == 1


def test_process_success_clears_external_blocker_without_inventing_file_evidence():
    state = WorkingState()
    call = ToolCall(0, 'process', 'run_command', {'command': 'printf ready'})
    state.observe(call, ToolResult.fail('dependency_unavailable', 'Missing tool'), 0)
    assert state.has_external_blocker
    state.observe(call, ToolResult.ok('Command completed.', content='ready'), 0)

    assert not state.has_external_blocker
    assert state.evidence_paths == ()
    assert 'Recent tool failures:' in state.system_suffix()
    assert 'dependency_unavailable' in state.system_suffix()


@pytest.mark.parametrize(('code', 'blocked'), [
    ('invalid_arguments', False), ('approval_required', True),
    ('authentication_required', True), ('permission_denied', True),
    ('network_unavailable', True), ('dependency_unavailable', True),
])
def test_only_explicit_external_error_codes_create_a_blocker(code, blocked):
    state = WorkingState()
    call = ToolCall(0, 'failure', 'read_file', {'path': 'sample.txt'})
    state.observe(call, ToolResult.fail(code, 'Failure'), 0)
    assert state.has_external_blocker is blocked
    state.observe(call, ToolResult.ok('Recovered with an available repository path.'), 0)
    assert not state.has_external_blocker


def test_failure_context_retains_only_five_recent_diagnostics():
    state = WorkingState()
    call = ToolCall(0, 'failure', 'read_file', {'path': 'sample.txt'})
    for index in range(8):
        state.observe(call, ToolResult.fail(f'failure_{index}', 'Failure'), 0)
    assert state.failures == [f'read_file: failure_{index}' for index in range(3, 8)]


def test_revision_change_invalidates_only_changed_paths():
    state = WorkingState()
    for path in ('src/changed.py', 'lib/unchanged.py'):
        observe_read(state, path)
    state.directories.update({(0, 'src'), (0, 'lib')})
    state.search_hits.update({(0, 'src/changed.py', 25), (0, 'lib/unchanged.py', 25)})
    state.discovered_files.update({(0, 'src/changed.py'), (0, 'lib/unchanged.py')})

    state.advance_revision(1, ('src/changed.py',))

    suffix = state.system_suffix()
    assert 'src/changed.py' not in suffix
    assert 'listed src' not in suffix
    assert 'lib/unchanged.py @ revision 1: lines 1-20' in suffix
    assert 'listed lib @ revision 1' in suffix
    assert 'search hit lib/unchanged.py:25 @ revision 1' in suffix
    assert 'discovered lib/unchanged.py @ revision 1' in suffix
    assert '@ revision 0' not in suffix


def test_process_invalidation_keeps_file_ranges_and_forgets_search_indexes():
    state = WorkingState()
    observe_read(state, 'src/app.py')
    state.directories.add((0, 'src'))
    state.search_hits.add((0, 'src/app.py', 25))
    state.discovered_files.add((0, 'src/lib.py'))

    state.invalidate_process_caches()

    assert state.evidence_paths == ('src/app.py',)
    assert 'lines 1-20' in state.system_suffix()
    assert 'listed ' not in state.system_suffix()
    assert 'search hit ' not in state.system_suffix()
    assert 'discovered ' not in state.system_suffix()
