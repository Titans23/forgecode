"""Controlled debug captures retain real, redacted bytes at long private paths."""
import json
from types import SimpleNamespace

from forge.observability.export_queue import ObservationOptions
from forge.observability.recorder import JournalRecorder
from forge.storage_paths import private_storage_path


def test_debug_capture_survives_long_private_storage_path(tmp_path):
    path = tmp_path / ('nested-' * 12) / ('private-' * 12) / 'journal.jsonl'
    journal = SimpleNamespace(path=path,
        observation_options=ObservationOptions(capture_mode='controlled_debug'),
        observation_secrets=('synthetic-debug-secret',))
    recorder = JournalRecorder(journal)
    result = recorder.capture_debug('model-request-example', {'text': 'synthetic-debug-secret'})
    assert result['state'] == 'captured', result
    directory = private_storage_path(path.parent / 'controlled-debug' / recorder.root.trace_id)
    captured = directory / 'model-request-example.json'
    assert len(str(captured)) > 260
    assert captured.stat().st_size == result['size_bytes']
    assert 'synthetic-debug-secret' not in captured.read_text(encoding='utf-8')
    assert isinstance(json.loads(captured.read_bytes()), dict)
