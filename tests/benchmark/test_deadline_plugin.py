import asyncio
from types import SimpleNamespace

import pytest

from benchmark.harbor.forgecode_agent import ForgeCodeHarborAgent
from benchmark.harbor.deadline_plugin import ForgeCodeDeadlinePlugin


def test_only_official_deadline_controls_supervisor_and_reserves_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr('benchmark.harbor.forgecode_agent.monotonic', lambda: 100)
    agent = ForgeCodeHarborAgent(logs_dir=tmp_path)
    with pytest.raises(ValueError, match='official Harbor'):
        agent.effective_turn_seconds()
    agent.set_phase_timeout(750)
    assert agent.effective_turn_seconds() == 720
    assert '--timeout-seconds 720 ' in agent._run_command('task')
    assert '--max-turn-seconds' not in agent._run_command('task')
    monkeypatch.setattr('benchmark.harbor.forgecode_agent.monotonic', lambda: 110)
    assert agent.effective_turn_seconds() == 710
    agent.set_phase_timeout(3600)
    assert agent.effective_turn_seconds() == 3570
    agent.set_phase_timeout(1)
    monkeypatch.setattr('benchmark.harbor.forgecode_agent.monotonic', lambda: 112)
    with pytest.raises(TimeoutError):
        agent.effective_turn_seconds()
    agent.set_phase_timeout(3000)
    command = agent._run_command('fixture task')
    assert '--max-model-calls' not in command and '--max-tool-calls' not in command
    assert '--timeout-seconds 2970 ' in command
    from benchmark.harbor.run_forge import build_parser
    with pytest.raises(SystemExit):
        build_parser().parse_args(['--message', 'fixture', '--max-model-calls', 'unlimited'])


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
        await plugin.on_job_start(SimpleNamespace(config=SimpleNamespace(tasks=[]), _metrics={}))
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
        job = SimpleNamespace(config=SimpleNamespace(tasks=[]), _metrics={})
        await first.on_job_start(job)
        try:
            with pytest.raises(RuntimeError, match='already installed'):
                await second.on_job_start(job)
        finally:
            await first.on_job_end(None)
        assert Trial._run_agent_phase is original
    asyncio.run(run())


def test_phase_journal_preserves_both_exceptions_without_changing_harbor_result(tmp_path, monkeypatch):
    import json
    from harbor.trial.trial import Trial
    from harbor.trial.errors import AgentTimeoutError
    original_first = []
    def first_only(trial, exc):
        if not original_first: original_first.append(type(exc).__name__)
    monkeypatch.setattr(Trial, '_record_exception', first_only)
    trial = SimpleNamespace(agent=ForgeCodeHarborAgent(logs_dir=tmp_path/'agent'),
                            paths=SimpleNamespace(trial_dir=tmp_path))
    def _run_agent_phase(): raise AgentTimeoutError('fixture agent deadline')
    def _run_shared_verifier(): raise RuntimeError('fixture verifier dependency; token=private-token')
    async def run():
        plugin = ForgeCodeDeadlinePlugin()
        await plugin.on_job_start(SimpleNamespace(config=SimpleNamespace(tasks=[]), _metrics={}))
        try:
            for phase in (_run_agent_phase, _run_shared_verifier):
                try: phase()
                except Exception as error: Trial._record_exception(trial, error)
        finally:
            await plugin.on_job_end(None)
    asyncio.run(run())
    assert Trial._record_exception is first_only and original_first == ['AgentTimeoutError']
    content = (tmp_path/'forgecode-phase-failures.jsonl').read_text()
    records = [json.loads(line) for line in content.splitlines()]
    assert [r['phase'] for r in records] == ['agent_execution','verifier']
    assert 'private-token' not in content
