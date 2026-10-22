"""Offline Journal projection benchmark using real SQLite and fsynced events."""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forge.engine.persistence import Store
from forge.engine.journal_projection import JournalProjector
from forge.sessions.store import SessionJournal, SessionStore


def measure(root, count, incremental):
    project = root / 'project'
    project.mkdir(parents=True)
    decoded = scans = 0
    original = SessionStore._read_records
    def counted(path):
        nonlocal decoded, scans
        records = original(path)
        decoded += len(records)
        scans += 1
        return records
    SessionStore._read_records = staticmethod(counted)
    try:
        with Store(root / 'data') as store:
            workspace = store.register_workspace(project)
            session = store.create_session(workspace['id'])
            journal = SessionJournal(store.data_dir / 'harness' / 'journal.jsonl',
                session_id=uuid4().hex, project_root=project)
            journal.append('session_started', {'cwd': str(project)})
            projector = JournalProjector(store)
            projector.project(journal.path, session['id'], trusted=True)
            started = perf_counter()
            with journal.capture_appends() as records:
                for index in range(count):
                    journal.append('user_message', {'message': {'role': 'user', 'content': f'{index}: ' + 'x' * 256}})
                    if incremental:
                        projector.project_appends(journal, session['id'], records)
                    else:
                        projector.project(journal.path, session['id'], trusted=True)
                        records.clear()
            projector.project(journal.path, session['id'], trusted=True)
            elapsed = perf_counter() - started
            projected = store.connection.execute('SELECT count(*) FROM events').fetchone()[0]
            assert projected == count + 1
            return {'seconds': elapsed, 'full_log_scans': scans, 'decoded_records': decoded,
                    'projected_events': projected, 'final_full_integrity_replay': True}
    finally:
        SessionStore._read_records = staticmethod(original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', type=int, default=100)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 1 <= args.records <= 1000:
        parser.error('--records must be between 1 and 1000')
    with TemporaryDirectory(prefix='forge-projection-benchmark-') as directory:
        root = Path(directory)
        result = {'scope': 'offline storage microbenchmark, not native sandbox or model acceptance',
            'records': args.records, 'full_replay': measure(root / 'full', args.records, False),
            'incremental': measure(root / 'incremental', args.records, True)}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
