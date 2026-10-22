"""Public entry behavior; native acceptance lives in test_terminal_native.py."""
import asyncio
import json
from pathlib import Path

import pytest

from forge.api import HarnessSession, SessionOptions, RunProfile, event_dict
from forge.config import ForgeConfig
from forge.permissions.policy import ApprovalResponse, PermissionManager, PermissionRequest
from forge.runtime.state import ModelTextDelta, ModelResponseCompleted


class Model:
    model = 'offline-fixture'

    async def stream(self, messages, tools=None, system=None):
        yield ModelTextDelta('hello')
        yield ModelResponseCompleted('end_turn')

    async def aclose(self):
        pass


def options(tmp_path, **changes):
    root = tmp_path / 'project'
    root.mkdir(exist_ok=True)
    return SessionOptions(root, data_root=tmp_path / 'data', sandbox='full-access',
        config=ForgeConfig(api_key='test-only', model_id='offline-fixture'), **changes)


def test_public_multiturn_result_events_and_resume(tmp_path):
    async def run():
        settings = options(tmp_path)
        async with await HarnessSession.open(settings, model_client_factory=lambda *a, **k: Model()) as session:
            first = await session.run('hello')
            assert first.text == 'hello'
            identity = session.session_id
            events = [event_dict(e) async for e in session.stream('hello again')]
            assert events[-1]['type'] == 'TurnCompleted'
            assert json.loads(json.dumps(events[-1]))['payload']['result']['text'] == 'hello'
        async with await HarnessSession.open(settings, resume_identifier=identity,
                model_client_factory=lambda *a, **k: Model()) as resumed:
            assert resumed.session_id == identity
            assert len(resumed.conversation.messages) >= 4
            assert (await resumed.run('third turn')).text == 'hello'
            assert resumed.cleanup is None
        assert resumed.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_cancel_closes_stream_and_allows_next_turn(tmp_path):
    class WaitingModel(Model):
        async def stream(self, *a, **k):
            await asyncio.Event().wait()
            yield ModelTextDelta('never')
    async def run():
        async with await HarnessSession.open(options(tmp_path),
                model_client_factory=lambda *a, **k: WaitingModel()) as session:
            turn = asyncio.create_task(session.run('wait'))
            await asyncio.sleep(0.05)
            assert session.cancel()
            with pytest.raises(asyncio.CancelledError):
                await turn
            assert not session.running
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_cancel_after_streamed_text_closes_in_context_and_resumes_pending_input(tmp_path):
    from contextlib import aclosing
    from forge.runtime.model_budget import request_observer, wire_observer

    class StreamingModel(Model):
        calls = 0
        closed = 0

        async def stream(self, *args, **kwargs):
            self.calls += 1
            try:
                yield ModelTextDelta('partial' if self.calls == 1 else 'recovered')
                yield ModelResponseCompleted('end_turn')
            finally:
                await asyncio.sleep(0)
                self.closed += 1

    async def run():
        model = StreamingModel()
        async with await HarnessSession.open(options(tmp_path, profile=RunProfile(intent_routing=False)),
                model_client_factory=lambda *a, **k: model) as session:
            async def consume():
                try:
                    async with aclosing(session.stream('first')) as stream:
                        async for event in stream:
                            if isinstance(event, ModelTextDelta):
                                session.queue_input('next', follow_up=True)
                                assert session.cancel()
                except asyncio.CancelledError:
                    pass
                else:
                    pytest.fail('Cancellation was not delivered')
                assert model.closed == 1
                assert request_observer.get() is None
                assert wire_observer.get() is None
                assert not session.running
                # The same consumer can recover without a second cancellation.
                await asyncio.sleep(0)
                assert (await session.run_next()).text == 'recovered'
                assert not session.pending_inputs
                assert model.closed == 2
            await asyncio.create_task(consume())
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())


def test_profile_is_independent_of_sandbox(tmp_path):
    assert RunProfile.evaluation().max_model_calls > 0
    assert options(tmp_path, profile=RunProfile.evaluation()).sandbox == 'full-access'
    with pytest.raises(ValueError):
        RunProfile(max_model_calls=0)
    with pytest.raises(ValueError):
        RunProfile(max_model_calls=True)
    with pytest.raises(ValueError):
        ApprovalResponse('unknown')


def test_readonly_and_plan_cannot_be_overridden_by_saved_grants(tmp_path):
    from forge.permissions.policy import PermissionRule
    for setting in ({'execution_mode': 'read-only'}, {'mode': 'plan'}):
        manager = PermissionManager(tmp_path, load_stored_rules=False, **setting)
        manager.session_rules.append(PermissionRule('allow', 'file.write', '*'))
        decision = asyncio.run(manager.authorize(PermissionRequest('write_file', 'file.write', 'low', ('a.py',))))
        assert decision.action == 'deny'


def test_workspace_edits_reuse_scope_but_delete_needs_approval(tmp_path):
    seen = []
    async def approve(request):
        seen.append(request)
        return ApprovalResponse('deny')
    manager = PermissionManager(tmp_path, mode='supervised', approval_handler=approve,
        authorized_write_roots=(tmp_path,), load_stored_rules=False)
    async def run():
        for target in ('a.py', 'b.py'):
            assert (await manager.authorize(PermissionRequest('write_file', 'file.write', 'low', (target,)))).action == 'allow'
        assert (await manager.authorize(PermissionRequest('remove_directory', 'file.delete', 'high', ('a.py',)))).action == 'deny'
        assert len(seen) == 1
    asyncio.run(run())


def test_failed_native_initialization_does_not_construct_model(tmp_path, monkeypatch):
    from forge.application.models import ContractError
    from forge.sandbox.application_backend import NativeBackendFactory
    called = []
    async def unavailable(*args, **kwargs):
        raise ContractError('native unavailable', kind='SANDBOX_UNAVAILABLE')
    monkeypatch.setattr(NativeBackendFactory, 'open', unavailable)
    settings = options(tmp_path)
    from dataclasses import replace
    with pytest.raises(ContractError, match='native unavailable'):
        asyncio.run(HarnessSession.open(replace(settings, sandbox='workspace-write'),
            model_client_factory=lambda *a, **k: called.append(True)))
    assert called == []


def test_launch_profiles_load_only_selected_surface(capsys):
    from forge.launch import main
    assert main(['--show-profile']) == 0
    terminal = json.loads(capsys.readouterr().out)
    assert terminal['bundles'] == ['core', 'terminal']
    assert not terminal['sandbox_selected_by_profile']
    assert main(['--profile', 'evaluation', 'list', '--json']) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) > 0


def test_launch_preserves_subcommand_profile_argument(monkeypatch):
    import forge.cli
    from forge.launch import main
    received = []
    monkeypatch.setattr(forge.cli, 'app', lambda **kw: received.append(kw['args']))
    assert main(['engine', '--profile', 'test']) == 0
    assert received == [['engine', '--profile', 'test']]


def test_event_export_redacts_bearer_and_opaque_provider_continuation():
    from forge.runtime.state import ModelProviderState
    exported = event_dict(ModelTextDelta('Authorization: Bearer private-token-value'))
    assert 'private-token-value' not in json.dumps(exported)
    continuation = event_dict(ModelProviderState('openai_responses', {'private': 'opaque-secret'}))
    assert 'opaque-secret' not in json.dumps(continuation)


def test_api_uses_requested_workspace_config_and_can_disable_routing(tmp_path, monkeypatch):
    from dataclasses import replace
    from forge.config import USER_MODEL_FIELDS
    for name in (*USER_MODEL_FIELDS, 'ANTHROPIC_API_KEY', 'MODEL_ID'):
        monkeypatch.delenv(name, raising=False)
    other = tmp_path / 'other'
    other.mkdir()
    (other / '.env').write_text('FORGE_MODEL=wrong-workspace\n')
    monkeypatch.chdir(other)
    settings = options(tmp_path)
    (settings.root / '.env').write_text('FORGE_MODEL=requested-workspace\nFORGE_PROVIDER=anthropic\nFORGE_API_KEY=offline-fixture\n')
    seen = []
    def factory(config, **kwargs):
        seen.append((config.model_id, kwargs))
        return Model()
    async def run():
        async with await HarnessSession.open(replace(settings, config=None,
                profile=RunProfile(intent_routing=False)), model_client_factory=factory) as session:
            assert (await session.run('hello')).model_calls == 1
        assert seen == [('requested-workspace', {})]
    asyncio.run(run())
    import os
    assert 'FORGE_API_KEY' not in os.environ
    assert 'FORGE_MODEL' not in os.environ
