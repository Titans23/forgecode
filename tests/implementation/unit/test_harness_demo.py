"""Demo failure receipts only; actual native execution is verified separately."""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_demo():
    spec = importlib.util.spec_from_file_location('harness_demo',
        Path(__file__).resolve().parents[3] / 'examples/harness_demo.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('state,delete_fails', [('unknown', False), ('clean', False), ('clean', True)])
def test_failed_demo_preserves_unknown_fixture_and_always_writes_receipt(tmp_path, monkeypatch, state, delete_fails):
    demo = load_demo()
    fixture = tmp_path / 'owned-fixture'
    fixture.mkdir()
    monkeypatch.setattr(demo, 'mkdtemp', lambda **kwargs: str(fixture))
    monkeypatch.setattr(demo, 'os', SimpleNamespace(name='posix'))
    async def fail_open(*args, **kwargs):
        error = RuntimeError('Injected startup failure')
        error.cleanup_report = {'state': state}
        raise error
    monkeypatch.setattr(demo.HarnessSession, 'open', fail_open)
    if delete_fails:
        def fail_delete(path):
            raise OSError('Injected deletion failure')
        monkeypatch.setattr(demo.shutil, 'rmtree', fail_delete)
    output = tmp_path / 'output'
    with pytest.raises(RuntimeError, match='Injected startup failure'):
        asyncio.run(demo.run(output))
    receipt = json.loads((output / 'summary.json').read_text())
    assert receipt['status'] == 'failed' and receipt['error_type'] == 'RuntimeError'
    assert receipt['cleanup']['state'] == state and receipt['provider_api_calls'] == 0
    assert receipt['fixture_retained'] == fixture.exists() == (state == 'unknown' or delete_fails)
    assert receipt['scripted_requests'] == 0
    if delete_fails:
        assert receipt['fixture_cleanup_error'] == 'OSError'


def test_scripted_model_emits_explicit_synthetic_usage():
    demo = load_demo()
    async def collect():
        return [event async for event in demo.ScriptedClient().stream([])]
    usage, call = asyncio.run(collect())
    assert isinstance(usage, demo.ModelUsageUpdate)
    assert usage.usage == demo.TokenUsage(10, 2)
    assert call.tool_call.name == 'replace_text'


def test_successful_demo_with_failed_fixture_deletion_exits_with_error(tmp_path, monkeypatch):
    demo = load_demo()
    fixture = tmp_path / 'owned-fixture'
    fixture.mkdir()
    journal = tmp_path / 'journal.jsonl'
    journal.write_text('unchanged')
    monkeypatch.setattr(demo, 'mkdtemp', lambda **kwargs: str(fixture))
    monkeypatch.setattr(demo, 'os', SimpleNamespace(name='posix'))
    class ClosedSession:
        cleanup = {'state': 'clean'}
        session_id = 'scripted-fixture'
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def stream(self, prompt):
            (fixture / 'project/value.txt').write_text('new')
            client.calls, client.closed = 6, True
            yield {'type': 'TurnCompleted', 'payload': {'result': {
                'status': 'completed', 'model_calls': 6, 'changed_paths': ['value.txt']}}}
    session = ClosedSession()
    session.journal = SimpleNamespace(path=journal)
    async def open_session(*args, model_client_factory, **kwargs):
        nonlocal client
        client = model_client_factory()
        return session
    client = None
    monkeypatch.setattr(demo.HarnessSession, 'open', open_session)
    monkeypatch.setattr(demo, 'event_dict', lambda event: event)
    monkeypatch.setattr(demo, 'SessionStore', lambda *args, **kwargs: SimpleNamespace(
        load=lambda session_id: SimpleNamespace(indeterminate_tools=[], messages=[])))
    def fail_delete(path):
        raise OSError('Injected deletion failure')
    monkeypatch.setattr(demo.shutil, 'rmtree', fail_delete)
    output = tmp_path / 'output'
    with pytest.raises(RuntimeError, match='Owned fixture cleanup failed'):
        asyncio.run(demo.run(output))
    receipt = json.loads((output / 'summary.json').read_text())
    assert receipt['status'] == 'failed' and receipt['fixture_cleanup_error'] == 'OSError'
    assert receipt['fixture_retained'] and fixture.exists()
