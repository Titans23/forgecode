"""Long explicit timeouts cross the real host backend without a turn ceiling."""
import asyncio
import shlex
import subprocess
import sys

import pytest
from pydantic import ValidationError

from forge.api import HarnessSession, SessionOptions, RunProfile
from forge.config import ForgeConfig
from forge.runtime.state import ModelResponseCompleted, ModelTextDelta
from forge.runtime.state import ToolCall
from forge.tools.shell import RunCommandInput
from forge.tools.verify import VerifyInput


@pytest.mark.parametrize('model', [RunCommandInput, VerifyInput])
@pytest.mark.parametrize('timeout', [0, -1, float('inf'), float('nan')])
def test_command_timeouts_must_be_positive_and_finite(model, timeout):
    with pytest.raises(ValidationError):
        model(command='echo done', timeout_seconds=timeout)


@pytest.mark.parametrize('name', ['run_command', 'verify'])
def test_two_hour_command_timeout_reaches_real_host_execution(tmp_path, monkeypatch, name):
    from forge.sandbox.file_client import FileWorkerClient
    original = FileWorkerClient._seconds
    observed = []
    def record(self, request):
        seconds = original(self, request)
        if request.get('name') == name:
            observed.append((request['arguments']['timeout_seconds'], seconds))
        return seconds
    monkeypatch.setattr(FileWorkerClient, '_seconds', record)
    class Model:
        model = 'offline'
        async def stream(self, **kwargs):
            yield ModelTextDelta('Done')
            yield ModelResponseCompleted('end_turn')
    async def run():
        root = tmp_path / 'project'
        root.mkdir()
        options = SessionOptions(root, data_root=tmp_path / 'data', sandbox='full-access',
                                 config=ForgeConfig(api_key='offline', model_id='offline'))
        async with await HarnessSession.open(options, model_client_factory=lambda *a, **k: Model()) as session:
            assert session.conversation.execution_policy['limits']['wall_time_seconds'] is None
            argv = [sys.executable, '-c', 'print("long timeout accepted")']
            command = subprocess.list2cmdline(argv) if sys.platform == 'win32' else shlex.join(argv)
            outcome = await session.conversation.tool_executor.execute(
                ToolCall(0, 'long-command', name, {'command': command, 'timeout_seconds': 7200}))
            assert outcome.result.success, outcome.result
            assert 'long timeout accepted' in outcome.result.content
        assert session.cleanup['state'] == 'clean'
    asyncio.run(run())
    assert observed == [(7200, 7205)]


@pytest.mark.parametrize('field', ['max_model_calls', 'max_tool_calls', 'wall_seconds'])
def test_profile_rejects_removed_budget_configuration(field):
    with pytest.raises(TypeError, match=field):
        RunProfile(**{field: 1})
