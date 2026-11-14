'''Bind preparation concurrency, metrics and the pinned Harbor phase deadline.'''

import asyncio
from functools import wraps
from importlib.metadata import version
from inspect import signature, Parameter
from harbor.models.job.plugin import BaseJobPlugin


class ForgeCodeDeadlinePlugin(BaseJobPlugin):
    async def on_job_start(self, job) -> None:
        from harbor.trial.trial import Trial
        from benchmark.harbor.forgecode_agent import ForgeCodeHarborAgent
        if version('harbor') != '0.18.0':
            raise RuntimeError('Deadline adapter requires the validated Harbor 0.18.0 release.')
        # Harbor 0.18.0 resolves metrics for datasets and "adhoc", but omits
        # source names on explicit frozen TaskConfigs. Preserve their identity.
        for task in job.config.tasks:
            if task.source and not job._metrics.get(task.source):
                job._metrics[task.source] = list(job._metrics['adhoc'])
        current = Trial._run_agent_phase
        if getattr(current, '_forge_deadline_adapter', False):
            raise RuntimeError('A ForgeCode deadline adapter is already installed in this process.')
        parameters = signature(current).parameters
        if 'timeout_sec' not in parameters and not any(
            item.kind == Parameter.VAR_KEYWORD for item in parameters.values()
        ):
            raise RuntimeError('Unsupported Harbor agent phase signature; deadline forwarding is unavailable.')
        self._original = Trial._run_agent_phase

        environment = getattr(job.config, 'environment', None)
        limit = (environment.kwargs if environment else {}).get('setup_concurrency', 4)
        if not isinstance(limit, int) or limit < 1:
            raise ValueError('setup_concurrency must be a positive integer')
        slots = asyncio.Semaphore(limit)
        self._original_prepare = Trial._prepare

        @wraps(self._original_prepare)
        async def prepare(trial):
            if not isinstance(trial.agent, ForgeCodeHarborAgent):
                return await self._original_prepare(trial)
            trial.logger.info('Waiting for an environment preparation slot (limit %s)', limit)
            # Queue outside Harbor's environment/install clocks; release before
            # the model phase, including when preparation fails or is cancelled.
            async with slots:
                trial.logger.info('Environment preparation slot acquired')
                return await self._original_prepare(trial)

        self._prepare_wrapper = prepare
        Trial._prepare = prepare

        @wraps(self._original)
        async def bounded_phase(trial, **kwargs):
            if isinstance(trial.agent, ForgeCodeHarborAgent):
                trial.agent.set_phase_timeout(kwargs.get('timeout_sec'))
            return await self._original(trial, **kwargs)

        self._wrapper = bounded_phase
        bounded_phase._forge_deadline_adapter = True
        Trial._run_agent_phase = bounded_phase
        # Harbor preserves only the first exception. Keep later verifier or
        # cleanup failures too, without replacing the official result.
        self._original_record = Trial._record_exception
        @wraps(self._original_record)
        def record_exception(trial, exc):
            if isinstance(trial.agent, ForgeCodeHarborAgent):
                import json
                import traceback
                from datetime import datetime, timezone
                from forge.sessions.trajectory import redact_text
                phases = {'_run_agent_phase':'agent_execution', '_setup_agent':'agent_setup',
                    '_setup_agent_environment':'environment_setup', '_start_agent_environment':'environment_setup',
                    '_run_shared_verifier':'verifier', '_run_separate_verifier':'verifier',
                    '_stop_agent_environment':'cleanup'}
                stage = next((phases[frame.name] for frame in reversed(traceback.extract_tb(exc.__traceback__))
                              if frame.name in phases), 'unknown')
                record = {'phase':stage, 'exception_type':type(exc).__name__,
                          'message':redact_text(str(exc))[:2000], 'at':datetime.now(timezone.utc).isoformat()}
                try:
                    with (trial.paths.trial_dir/'forgecode-phase-failures.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(record, ensure_ascii=False)+'\n')
                except OSError:
                    trial.logger.warning('ForgeCode phase failure persistence failed; official exception follows.')
            return self._original_record(trial, exc)
        self._record_wrapper = record_exception
        Trial._record_exception = record_exception

    async def on_job_end(self, job_result) -> None:
        from harbor.trial.trial import Trial
        if getattr(self, '_wrapper', None) is Trial._run_agent_phase:
            Trial._run_agent_phase = self._original
        if getattr(self, '_record_wrapper', None) is Trial._record_exception:
            Trial._record_exception = self._original_record
        if getattr(self, '_prepare_wrapper', None) is Trial._prepare:
            Trial._prepare = self._original_prepare
