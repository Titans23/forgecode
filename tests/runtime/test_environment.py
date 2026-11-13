import asyncio
import json

import pytest

from forge.api import HarnessSession, SessionOptions
from forge.config import ForgeConfig
from forge.runtime.environment import cgroup_limits, resource_facts
from forge.runtime.state import ModelTextDelta, ModelResponseCompleted, ModelToolCallCompleted, ToolCall


@pytest.mark.parametrize('version', [1, 2])
def test_container_limits_override_host_visible_cpu_count(tmp_path, monkeypatch, version):
    membership = tmp_path/'membership'
    if version == 2:
        membership.write_text('0::/task\n')
        (tmp_path/'cpu.max').write_text('100000 100000')
        (tmp_path/'memory.max').write_text('1073741824')
        (tmp_path/'task').mkdir()
        (tmp_path/'task/cpu.max').write_text('200000 100000')
        (tmp_path/'task/memory.max').write_text('max')
    else:
        membership.write_text('4:cpu:/\n5:memory:/\n')
        (tmp_path/'cpu').mkdir()
        (tmp_path/'memory').mkdir()
        (tmp_path/'cpu/cpu.cfs_quota_us').write_text('100000')
        (tmp_path/'cpu/cpu.cfs_period_us').write_text('100000')
        (tmp_path/'memory/memory.limit_in_bytes').write_text('1073741824')
    limits = cgroup_limits(tmp_path, membership)
    assert limits == {'cpu_quota': 1.0, 'memory_limit_bytes': 1073741824}
    monkeypatch.setattr('forge.runtime.environment.sys.platform', 'linux')
    monkeypatch.setattr('forge.runtime.environment.os.sched_getaffinity', lambda _: set(range(64)), raising=False)
    monkeypatch.setattr('forge.runtime.environment.cgroup_limits', lambda: limits)
    assert resource_facts()['effective_cpu_capacity'] == 1


def test_missing_or_malformed_cgroup_limits_stay_unknown(tmp_path):
    membership = tmp_path/'membership'
    membership.write_text('0::/\n')
    assert cgroup_limits(tmp_path, membership) == {'cpu_quota':None, 'memory_limit_bytes':None}
    (tmp_path/'cpu.max').write_text('bad 0')
    (tmp_path/'memory.max').write_text('max')
    assert cgroup_limits(tmp_path, membership) == {'cpu_quota':None, 'memory_limit_bytes':None}


def test_external_deadline_is_request_context_and_warns_once(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('forge.runtime.runner.monotonic', lambda: clock[0])
    root = tmp_path/'project'
    root.mkdir()
    contexts, systems = [], []
    class Model:
        model = 'offline'
        async def stream(self, messages, system, **kwargs):
            contexts.append(messages[-1]['content'])
            systems.append(system)
            if len(contexts) == 1:
                yield ModelToolCallCompleted(ToolCall(0, 'inspect', 'list_directory', {'path':'.'}))
                clock[0] = 150
                yield ModelResponseCompleted('tool_use')
            else:
                yield ModelTextDelta('Done')
                yield ModelResponseCompleted('end_turn')
    async def run():
        async with await HarnessSession.open(SessionOptions(root, data_root=tmp_path/'private',
            caller_deadline=200, config=ForgeConfig(api_key='offline', model_id='offline')),
            model_client_factory=lambda *a, **kw: Model()) as session:
            await session.run('Respond briefly.')
            assert session.conversation.command_jobs.caller_deadline == 200
            records = [json.loads(row) for row in session.journal.path.read_text().splitlines()]
            assert len([r for r in records if r['type'] == 'caller_deadline_warning']) == 1
        assert '100 seconds' in contexts[0] and 'nearly exhausted' in contexts[0]
        assert '50 seconds' in contexts[1] and 'nearly exhausted' not in contexts[1]
        assert '[Caller deadline]' in contexts[0] and '[Execution resources]' in contexts[0]
        assert '[Caller deadline]' not in systems[0] and systems[0] == systems[1]
    asyncio.run(run())


def test_cli_default_has_no_caller_deadline(tmp_path):
    assert SessionOptions(tmp_path).caller_deadline is None
    with pytest.raises(ValueError, match='finite'):
        SessionOptions(tmp_path, caller_deadline=float('inf'))
