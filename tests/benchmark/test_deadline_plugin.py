import asyncio
from types import SimpleNamespace

import pytest

from benchmark.harbor.forgecode_agent import ForgeCodeHarborAgent
from benchmark.harbor.deadline_plugin import ForgeCodeDeadlinePlugin


def test_resolved_deadline_reaches_kernel_and_reserves_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr('benchmark.harbor.forgecode_agent.monotonic', lambda: 100)
    agent = ForgeCodeHarborAgent(logs_dir=tmp_path, max_turn_seconds=1800)
    assert agent.effective_turn_seconds() == 1800
    agent.set_phase_timeout(750)
    assert agent.effective_turn_seconds() == 720
    assert '--max-turn-seconds 720 ' in agent._run_command('task')
    monkeypatch.setattr('benchmark.harbor.forgecode_agent.monotonic', lambda: 110)
    assert agent.effective_turn_seconds() == 710
    agent.set_phase_timeout(3600)
    assert agent.effective_turn_seconds() == 1800
    agent.set_phase_timeout(1)
    monkeypatch.setattr('benchmark.harbor.forgecode_agent.monotonic', lambda: 112)
    with pytest.raises(TimeoutError):
        agent.effective_turn_seconds()


def test_plugin_passes_actual_phase_timeout_and_restores_hook(tmp_path, monkeypatch):
    from harbor.trial.trial import Trial
    # Check forwarding independently of platform clock resolution/roundoff.
    monkeypatch.setattr('benchmark.harbor.forgecode_agent.monotonic', lambda: 100.0)
    seen = []
    async def original(trial, **kwargs):
        seen.append(trial.agent.effective_turn_seconds())
    monkeypatch.setattr(Trial, '_run_agent_phase', original)
    agent = ForgeCodeHarborAgent(logs_dir=tmp_path)
    plugin = ForgeCodeDeadlinePlugin()
    async def exercise():
        await plugin.on_job_start(None)
        await Trial._run_agent_phase(SimpleNamespace(agent=agent), timeout_sec=1200)
        await plugin.on_job_end(None)
    asyncio.run(exercise())
    assert seen == [1170.0]
    assert Trial._run_agent_phase is original


def test_deadline_adapter_rejects_duplicate_installation(monkeypatch):
    from harbor.trial.trial import Trial
    async def original(trial, **kwargs):
        pass
    monkeypatch.setattr(Trial, '_run_agent_phase', original)
    async def run():
        first, second = ForgeCodeDeadlinePlugin(), ForgeCodeDeadlinePlugin()
        await first.on_job_start(None)
        try:
            with pytest.raises(RuntimeError, match='already installed'):
                await second.on_job_start(None)
        finally:
            await first.on_job_end(None)
        assert Trial._run_agent_phase is original
    asyncio.run(run())
