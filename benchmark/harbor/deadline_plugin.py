'''Forward Harbor's resolved per-phase deadline without changing its timeout policy.'''

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

    async def on_job_end(self, job_result) -> None:
        from harbor.trial.trial import Trial
        if getattr(self, '_wrapper', None) is Trial._run_agent_phase:
            Trial._run_agent_phase = self._original
