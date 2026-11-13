"""Public entry and benchmark use the same real host tools, without model APIs."""
import asyncio
import pytest

from benchmark.harbor.run_forge import run_turn
from forge.api import HarnessSession, SessionOptions
from forge.config import ForgeConfig
from forge.runtime.dependencies import RuntimeBindings
from forge.runtime.state import ModelResponseCompleted, ModelTextDelta, ModelToolCallCompleted, ToolCall
from forge.sessions.store import SessionStore


@pytest.mark.parametrize('entry', ['public', 'benchmark'])
def test_entries_share_policy_git_and_outside_file_edits(tmp_path, entry):
    project = tmp_path / 'project'
    project.mkdir()
    data = tmp_path / 'private'
    outside = tmp_path / 'allowed-outside.txt'
    requests = [
        ('run_command', {'command': 'git init -q && git checkout -b harness-fixture'}),
        ('write_file', {'path': str(outside), 'content': 'before\n'}),
        ('apply_patch', {'patch': f'*** Begin Patch\n*** Update File: {outside.as_posix()}\n@@\n-before\n+after\n*** End Patch'}),
    ]
    class Model:
        model = 'offline-entry-fixture'
        calls = 0
        closed = 0
        async def stream(self, messages, **kwargs):
            assert 'Approval policy: never. Sandbox mode: full-access.' in messages[-1]['content']
            if self.calls < len(requests):
                name, arguments = requests[self.calls]
                self.calls += 1
                yield ModelToolCallCompleted(ToolCall(0, f'call-{self.calls}', name, arguments))
                yield ModelResponseCompleted('tool_use')
            else:
                yield ModelTextDelta('Fixture complete.')
                yield ModelResponseCompleted('end_turn')
        async def aclose(self):
            self.closed += 1
    model = Model()
    config = ForgeConfig(api_key='offline', model_id=model.model)
    async def run():
        if entry == 'benchmark':
            return await run_turn(project, 'Apply the three authorized fixture operations.', resume=False,
                runtime_bindings=RuntimeBindings(config=config, data_root=data, model_client_factory=lambda *a, **k: model))
        async with await HarnessSession.open(SessionOptions(project, config=config, data_root=data),
                model_client_factory=lambda *a, **k: model) as handle:
            return await handle.run('Apply the three authorized fixture operations.')
    result = asyncio.run(run())
    assert result.status == 'completed' and outside.read_text() == 'after\n' and model.closed == 1
    store = SessionStore(project, data_root=data)
    state = store.latest()
    records = store._read_records(state.info.path)
    completed = [store._payload(row, state.info.path) for row in records if row['type'] == 'tool_completed']
    assert len(completed) == 3 and all(row['success'] for row in completed)
    assert state.sandbox_mode == 'full-access' and state.approval_policy == 'never'


def test_evaluation_does_not_load_unfrozen_local_permissions(tmp_path):
    from forge.api import RunProfile
    project = tmp_path / 'project'
    project.mkdir()
    (project / '.forge').mkdir()
    (project / '.forge/permissions.json').write_text('invalid local policy; must not be loaded')
    class Model:
        model = 'offline'
        async def aclose(self):
            pass
    async def run():
        async with await HarnessSession.open(SessionOptions(project, config=ForgeConfig(api_key='offline', model_id='offline'),
                data_root=tmp_path / 'private', profile=RunProfile.evaluation()),
                model_client_factory=lambda *a, **k: Model()) as handle:
            manager = handle.conversation.permission_manager
            assert not manager.load_stored_rules and not manager.project_rules
            assert handle.conversation.hook_manager is None and handle.conversation.mcp_manager is None
    asyncio.run(run())


def test_frozen_execution_cannot_be_silently_overridden(tmp_path):
    with pytest.raises(ValueError, match='Frozen evaluation execution'):
        asyncio.run(run_turn(tmp_path, 'No model call', resume=False,
            frozen_configuration={'execution': {'sandbox': 'read-only', 'approval': 'never', 'profile': 'evaluation'}}))
