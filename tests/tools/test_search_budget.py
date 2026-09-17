import asyncio
from threading import Event
from time import monotonic

from forge.tools.search import FindFilesInput, FindFilesTool, GrepInput, GrepTool, bounded_search


def test_result_limit_does_not_walk_descendants(tmp_path, monkeypatch):
    import forge.tools.search as search
    (tmp_path / 'hit.txt').write_text('hit')
    for index in range(30):
        directory = tmp_path / str(index)
        directory.mkdir()
        (directory / 'other.txt').write_text('hit')
    scanned = []
    original = search.os.scandir
    def observed(path):
        scanned.append(path)
        return original(path)
    monkeypatch.setattr(search.os, 'scandir', observed)
    result = asyncio.run(FindFilesTool(tmp_path).execute(FindFilesInput(pattern='*.txt', max_results=1)))
    assert result.content == 'hit.txt'
    assert scanned == [tmp_path]
    assert result.metadata['stop_reason'] == 'result_limit'
    assert not result.metadata['complete']


def test_no_match_still_has_enumeration_budget(tmp_path):
    for index in range(40):
        (tmp_path / f'{index}.txt').write_text('text')
    result = asyncio.run(FindFilesTool(tmp_path).execute(FindFilesInput(pattern='*.missing', max_scan_entries=5)))
    assert result.metadata['scanned_entries'] == 5
    assert result.metadata['stop_reason'] == 'scan_limit'
    assert not result.metadata['complete']


def test_grep_skips_oversized_files_and_reports_incomplete(tmp_path):
    (tmp_path / 'large.txt').write_text('needle' * 50)
    result = asyncio.run(GrepTool(tmp_path).execute(GrepInput(pattern='needle', max_file_bytes=10)))
    assert result.metadata['match_count'] == 0
    assert result.metadata['skipped_files'] == 1
    assert not result.metadata['complete']


def test_cancel_stops_cooperative_search_thread():
    started, stopped = Event(), Event()
    def worker(arguments, cancelled):
        started.set()
        cancelled.wait(3)
        stopped.set()
    async def run():
        task = asyncio.create_task(bounded_search(worker, FindFilesInput(pattern='*')))
        while not started.is_set():
            await asyncio.sleep(.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert await asyncio.to_thread(stopped.wait, 1)
    before = monotonic()
    asyncio.run(run())
    assert monotonic() - before < 2
