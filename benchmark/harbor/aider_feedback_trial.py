'''Aider-compatible single repair turn after a real Harbor verifier failure.'''

from __future__ import annotations

import json
from typing import Any, override

from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.models.task.verifier_mode import (
    VerifierEnvironmentMode,
    resolve_task_verifier_mode,
)
from harbor.models.verifier.result import VerifierResult
from harbor.trial.errors import AgentTimeoutError
from harbor.trial.single_step import SingleStepTrial
from harbor.verifier.verifier import RewardFileNotFoundError


_MAX_FEEDBACK_CHARS = 60_000


def should_request_feedback_round(
    rewards: dict[str, float | int] | None,
) -> bool:
    return rewards is not None and rewards.get('reward') == 0


def should_request_feedback_after_missing_reward(test_output: str) -> bool:
    return (
        'CMake build failed' in test_output
        and 'error:' in test_output.lower()
    )


def build_aider_feedback(test_output: str) -> str:
    output = test_output[-_MAX_FEEDBACK_CHARS:]
    return (
        'The tests are correct. Do not modify the tests.\n'
        'Fix the code in the current workspace to resolve the testing errors '
        'below, then run the relevant tests if possible.\n\n'
        'Testing errors:\n'
        f'{output}'
    )


class AiderFeedbackTrial(SingleStepTrial):
    '''Run one same-session repair turn after a failed shared verifier.'''

    @override
    async def _run(self) -> None:
        if (
            resolve_task_verifier_mode(self.task.config)
            != VerifierEnvironmentMode.SHARED
        ):
            await super()._run()
            return

        await self._run_agent()
        await self._upload_agent_logs()
        await self._collect_artifacts()
        missing_reward_compile_failure = False
        try:
            await self._run_verifier()
        except RewardFileNotFoundError:
            missing_reward_compile_failure = (
                should_request_feedback_after_missing_reward(
                    self._read_verifier_output()
                )
            )
            if not missing_reward_compile_failure:
                raise

        failed_reward = should_request_feedback_round(
            self.result.verifier_result.rewards
            if self.result.verifier_result
            else None
        )
        self._write_attempt_record(
            first_reward=(
                self.result.verifier_result.rewards.get('reward')
                if self.result.verifier_result
                else None
            ),
            feedback_requested=(
                failed_reward or missing_reward_compile_failure
            ),
            missing_reward_compile_failure=missing_reward_compile_failure,
        )
        if self.result.exception_info is None and (
            failed_reward or missing_reward_compile_failure
        ):
            await self._run_agent_with_instruction(
                build_aider_feedback(self._read_verifier_output())
            )
            await self._upload_agent_logs()
            try:
                await self._run_verifier()
            except RewardFileNotFoundError:
                # The Aider C++ verifier reports compiler diagnostics but does
                # not write reward.txt.  Once the one permitted repair has
                # also failed to compile, this is a real final failure, not a
                # Harbor infrastructure error.  Normalize it to reward=0 so
                # pass@2 and the infra-failure count remain meaningful.
                if not should_request_feedback_after_missing_reward(
                    self._read_verifier_output()
                ):
                    raise
                self._record_compile_failure_reward()

        await self._stop_agent_environment()

    async def _run_agent_with_instruction(self, instruction: str) -> None:
        try:
            await self._run_agent_phase(
                target=self.result,
                instruction=instruction,
                timeout_sec=self._agent_timeout_sec,
                user=self.task.config.agent.user,
            )
        except (AgentTimeoutError, NonZeroAgentExitCodeError) as exc:
            self._record_exception(exc)
        finally:
            await self._sync_agent_output(self.result)

    def _read_verifier_output(self) -> str:
        output = self.paths.verifier_dir / 'test-stdout.txt'
        try:
            return output.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return (
                'The verifier reported reward=0 but did not provide test output.'
            )

    def _write_attempt_record(
        self,
        *,
        first_reward: float | int | None,
        feedback_requested: bool,
        missing_reward_compile_failure: bool,
    ) -> None:
        path = self.paths.verifier_dir / 'aider-attempts.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    'first_reward': first_reward,
                    'feedback_requested': feedback_requested,
                    'missing_reward_compile_failure': (
                        missing_reward_compile_failure
                    ),
                },
                indent=2,
            )
            + '\n',
            encoding='utf-8',
        )

    def _record_compile_failure_reward(self) -> None:
        reward_path = self.paths.verifier_dir / 'reward.txt'
        reward_path.parent.mkdir(parents=True, exist_ok=True)
        reward_path.write_text('0\n', encoding='utf-8')
        self.result.verifier_result = VerifierResult(
            rewards={'reward': 0.0}
        )


async def create_aider_feedback_trial(
    trial_class: Any,
    config: Any,
) -> AiderFeedbackTrial | None:
    task, download_result = await trial_class._load_task(config)
    if task.has_steps:
        return None
    return AiderFeedbackTrial(
        config,
        _task=task,
        _task_download_result=download_result,
    )
