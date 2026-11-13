'''Bind frozen task metrics and forward the pinned Harbor phase deadline.'''

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
