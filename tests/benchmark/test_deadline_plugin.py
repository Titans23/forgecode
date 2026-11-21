import asyncio
import logging
from types import SimpleNamespace

import pytest
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.trial.errors import AgentTimeoutError

from benchmark.harbor.forgecode_agent import ForgeCodeHarborAgent
from benchmark.harbor.deadline_plugin import ForgeCodeDeadlinePlugin


@pytest.mark.parametrize('drain', [False, True])
def test_real_queue_drains_before_creation_and_preserves_active_results(tmp_path, monkeypatch, drain):
    import json
    from harbor.trial.queue import TrialQueue
    from harbor.trial.trial import Trial
    async def exercise():
        queue = TrialQueue(n_concurrent=2)
        original = queue._execute_trial_with_retries
        entered, release = asyncio.Event(), asyncio.Event()
        created = []
        async def create(config):
            created.append(config.trial_name)
            if len(created) == 2: entered.set()
            async def run():
                await release.wait()
                directory = tmp_path / config.trial_name
                directory.mkdir()
                (directory / 'result.json').write_text('{"reward":1}')
                return SimpleNamespace(exception_info=None, task=config.trial_name)
            return SimpleNamespace(config=config, run=run)
        monkeypatch.setattr(Trial, 'create', create)
        plugin = ForgeCodeDeadlinePlugin()
        job = SimpleNamespace(config=SimpleNamespace(tasks=[], environment=SimpleNamespace(
            kwargs={'receipt_path': str(tmp_path / 'resources.jsonl')})), _metrics={}, _trial_queue=queue)
        await plugin.on_job_start(job)
        configs = [SimpleNamespace(trial_name=f'task-{i}', agent=SimpleNamespace(n_concurrent=None)) for i in range(5)]
        try:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(coro) for coro in queue.submit_batch(configs)]
                await asyncio.wait_for(entered.wait(), 2)
                if drain: (tmp_path / 'drain-request.json').write_text('{}')
                release.set()
            assert all(not t.cancelled() and t.result().task == f'task-{i}' for i, t in enumerate(tasks[:2]))
            assert len(list(tmp_path.glob('*/result.json'))) == (2 if drain else 5)
            assert created == [f'task-{i}' for i in range(2 if drain else 5)]
            if drain:
                assert all(t.cancelled() for t in tasks[2:])
                state = json.loads((tmp_path / 'drain-state.json').read_text())
                assert state == {'status':'drained', 'active':[], 'deferred':['task-2','task-3','task-4']}
            else:
                assert not (tmp_path / 'drain-state.json').exists()
        finally:
            await plugin.on_job_end(None)
        assert queue._execute_trial_with_retries == original
    asyncio.run(exercise())


def test_preparation_limit_releases_before_twenty_agents_run(tmp_path, monkeypatch):
    from harbor.trial.trial import Trial
    async def exercise():
        active = 0
        peak = 0
        entered = []
        agents = []
        four_ready, release, twenty_ready, finish = (asyncio.Event() for _ in range(4))

        async def prepare(trial):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            entered.append(trial)
            if active == 4:
                four_ready.set()
            try:
                await release.wait()
            finally:
                active -= 1

        async def agent_phase(trial, **kwargs):
            agents.append(trial)
            if len(agents) == 20:
                twenty_ready.set()
            await finish.wait()

        monkeypatch.setattr(Trial, '_prepare', prepare)
        monkeypatch.setattr(Trial, '_run_agent_phase', agent_phase)
        plugin = ForgeCodeDeadlinePlugin()
        job = SimpleNamespace(config=SimpleNamespace(tasks=[], environment=SimpleNamespace(
            kwargs={'setup_concurrency': 4})), _metrics={})
        await plugin.on_job_start(job)
        trials = [SimpleNamespace(agent=ForgeCodeHarborAgent(logs_dir=tmp_path/str(i)),
                                  logger=logging.getLogger(__name__)) for i in range(20)]
        async def run(trial):
            await Trial._prepare(trial)
            await Trial._run_agent_phase(trial, timeout_sec=1200)
        workers = [asyncio.create_task(run(trial)) for trial in trials]
        try:
            await asyncio.wait_for(four_ready.wait(), 2)
            assert len(entered) == 4 and not agents
            release.set()
            await asyncio.wait_for(twenty_ready.wait(), 2)
            assert peak == 4 and active == 0
            finish.set()
            await asyncio.gather(*workers)
        finally:
            for worker in workers: worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            await plugin.on_job_end(None)
        assert Trial._prepare is prepare and Trial._run_agent_phase is agent_phase
    asyncio.run(exercise())


@pytest.mark.parametrize('cancel_active', [False, True])
def test_preparation_failure_or_cancellation_releases_slot(tmp_path, monkeypatch, cancel_active):
    from harbor.trial.trial import Trial
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        async def prepare(trial):
            seen.append(trial.name)
            if trial.name == 'first':
                entered.set()
                await release.wait()
                raise RuntimeError('setup failed')
        monkeypatch.setattr(Trial, '_prepare', prepare)
        plugin = ForgeCodeDeadlinePlugin()
        job = SimpleNamespace(config=SimpleNamespace(tasks=[], environment=SimpleNamespace(
            kwargs={'setup_concurrency': 1})), _metrics={})
        await plugin.on_job_start(job)
        def trial(name):
            return SimpleNamespace(name=name, agent=ForgeCodeHarborAgent(logs_dir=tmp_path/name),
                                   logger=logging.getLogger(__name__))
        first = asyncio.create_task(Trial._prepare(trial('first')))
        await entered.wait()
        queued = asyncio.create_task(Trial._prepare(trial('cancelled_queue')))
        next_task = asyncio.create_task(Trial._prepare(trial('next')))
        await asyncio.sleep(0)
        queued.cancel()
        if cancel_active: first.cancel()
        else: release.set()
        try:
            results = await asyncio.wait_for(asyncio.gather(first, queued, next_task,
                                                          return_exceptions=True), 2)
            assert seen == ['first', 'next']
            assert isinstance(results[0], asyncio.CancelledError if cancel_active else RuntimeError)
            assert isinstance(results[1], asyncio.CancelledError) and results[2] is None
        finally:
            await plugin.on_job_end(None)
    asyncio.run(exercise())


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


@pytest.mark.parametrize('agent_error', [None, NonZeroAgentExitCodeError('agent exit 1'),
                                       AgentTimeoutError('official deadline')])
def test_capture_is_after_agent_clock_and_cannot_replace_agent_result(tmp_path, monkeypatch, agent_error):
    from contextlib import nullcontext
    from harbor.trial.trial import Trial
    import json
    order = []
    agent = ForgeCodeHarborAgent(logs_dir=tmp_path)
    async def original(trial, **kwargs):
        async def work():
            trial.agent._patch_baseline = 'a' * 40
            order.append('agent')
        await asyncio.wait_for(work(), kwargs['timeout_sec'])
        order.append('agent_clock_ended')
        if agent_error:
            raise agent_error
    async def execute(**kwargs):
        assert order == ['agent', 'agent_clock_ended']
        # Longer than the entire official phase: only the capture clock owns it.
        await asyncio.sleep(0.06)
        order.append('capture')
        raise TimeoutError('patch transport deadline')
    monkeypatch.setattr(Trial, '_run_agent_phase', original)
    trial = SimpleNamespace(agent=agent, agent_environment=SimpleNamespace(
        exec=execute, with_default_user=lambda user: nullcontext()))
    async def exercise():
        plugin = ForgeCodeDeadlinePlugin()
        await plugin.on_job_start(SimpleNamespace(config=SimpleNamespace(tasks=[]), _metrics={}))
        try:
            if agent_error:
                with pytest.raises(type(agent_error)) as caught:
                    await Trial._run_agent_phase(trial, timeout_sec=0.03)
                assert caught.value is agent_error
            else:
                await Trial._run_agent_phase(trial, timeout_sec=0.03)
            order.append('verifier')
        finally:
            await plugin.on_job_end(None)
    asyncio.run(exercise())
    assert order == ['agent', 'agent_clock_ended', 'capture', 'verifier']
    diagnostic = json.loads((tmp_path / 'forgecode-patch-capture.json').read_text())
    assert diagnostic['phase'] == 'patch_capture' and diagnostic['status'] == 'timed_out'
    assert not (tmp_path / 'forgecode-pregrade.patch').exists()


def test_capture_cancellation_remains_cancellation_and_removes_stale_patch(tmp_path):
    import json
    agent = ForgeCodeHarborAgent(logs_dir=tmp_path)
    agent._patch_baseline = 'a' * 40
    patch = tmp_path / 'forgecode-pregrade.patch'
    patch.write_text('previous attempt')
    async def exercise():
        started = asyncio.Event()
        async def execute(**kwargs):
            started.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(agent.capture_patch(SimpleNamespace(exec=execute)))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(exercise())
    assert not patch.exists()
    assert json.loads((tmp_path / 'forgecode-patch-capture.json').read_text())['status'] == 'cancelled'


def test_phase_journal_preserves_both_exceptions_without_changing_harbor_result(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock
    from harbor.models.task.config import VerifierConfig
    from harbor.trial.errors import VerifierTimeoutError
    from harbor.trial.single_step import SingleStepTrial
    from harbor.trial.trial import Trial
    original_first = []
    def first_only(trial, exc):
        if not original_first: original_first.append(type(exc).__name__)
    monkeypatch.setattr(Trial, '_record_exception', first_only)
    trial = SimpleNamespace(
        agent=ForgeCodeHarborAgent(logs_dir=tmp_path/'agent'),
        paths=SimpleNamespace(trial_dir=tmp_path),
        config=SimpleNamespace(verifier=SimpleNamespace(disable=False)),
        task=SimpleNamespace(config=SimpleNamespace(verifier=VerifierConfig())),
        result=SimpleNamespace(verifier_result=None),
        _now=lambda: datetime.now(timezone.utc),
        _emit=AsyncMock(),
        _verifier_timeout_sec=360,
        _run_shared_verifier=AsyncMock(side_effect=TimeoutError('verifier deadline')),
    )
    def _run_agent_phase():
        raise AgentTimeoutError('fixture agent deadline; token=private-token')
    async def run():
        plugin = ForgeCodeDeadlinePlugin()
        await plugin.on_job_start(SimpleNamespace(config=SimpleNamespace(tasks=[]), _metrics={}))
        try:
            with pytest.raises(AgentTimeoutError) as agent_error:
                _run_agent_phase()
            Trial._record_exception(trial, agent_error.value)
            with pytest.raises(VerifierTimeoutError) as verifier_error:
                await SingleStepTrial._run_verifier(trial)
            Trial._record_exception(trial, verifier_error.value)
        finally:
            await plugin.on_job_end(None)
    asyncio.run(run())
    trial._run_shared_verifier.assert_awaited_once_with(timeout_sec=360, user=None)
    assert trial.result.verifier_result is None
    assert Trial._record_exception is first_only and original_first == ['AgentTimeoutError']
    content = (tmp_path/'forgecode-phase-failures.jsonl').read_text()
    records = [json.loads(line) for line in content.splitlines()]
    assert [(r['phase'], r['exception_type']) for r in records] == [
        ('agent_execution', 'AgentTimeoutError'),
        ('verifier', 'VerifierTimeoutError'),
    ]
    assert 'private-token' not in content
