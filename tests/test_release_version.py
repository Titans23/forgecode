"""One product version across packaging and runtime; historical formats keep their identity."""
import json
from pathlib import Path
import tomllib

from forge import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_product_versions_and_local_workspace_dependency_match():
    assert __version__ == tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    for name in ('package.json', 'packaging/npm/package.json', 'packages/contracts/package.json', 'sandbox_bridge/package.json'):
        assert json.loads((ROOT / name).read_text())['version'] == __version__
    bridge = json.loads((ROOT / 'sandbox_bridge/package.json').read_text())
    assert bridge['dependencies']['@forgecode/contracts'] == __version__
    lock = json.loads((ROOT / 'package-lock.json').read_text())
    assert lock['version'] == lock['packages']['']['version'] == __version__
    package = next(item for item in tomllib.loads((ROOT / 'uv.lock').read_text())['package'] if item['name'] == 'forge-code')
    assert package['version'] == __version__


def test_export_never_labels_old_or_mixed_sessions_as_current(tmp_path):
    from forge.sessions.store import SessionJournal
    from forge.sessions.trajectory import journal_to_atif
    journal = SessionJournal(tmp_path / 'session.jsonl', session_id='session-' + 'a' * 24, project_root=tmp_path)
    journal.append('session_started', {'cwd': str(tmp_path), 'model': 'fixture', 'forge_version': '0.1.2'})
    journal.record_turn_started('historical fixture', None)
    journal.record_turn_cancelled('historical cancellation')
    original = journal.path.read_bytes()
    exported = journal_to_atif(journal.path)
    assert exported['agent']['version'] == '0.1.2'
    assert exported['extra']['exporter_version'] == __version__
    assert journal.path.read_bytes() == original
    journal.record_resumed()
    exported = journal_to_atif(journal.path)
    assert exported['agent']['version'] == 'mixed'
    assert exported['extra']['recorded_agent_versions'] == ['0.1.2', __version__]
    unknown = SessionJournal(tmp_path / 'unknown.jsonl', session_id='session-' + 'b' * 24, project_root=tmp_path)
    unknown.append('session_started', {'cwd': str(tmp_path), 'model': 'fixture'})
    assert journal_to_atif(unknown.path)['agent']['version'] == 'unknown'
