'''Summarize Harbor rewards and ForgeCode turn telemetry without conflating infra failures.'''

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
from typing import Any


_RESULT_PREFIX = 'FORGECODE_BENCHMARK_RESULT='


@dataclass(frozen=True, slots=True)
class RunSummary:
    total_trials: int
    missing_results: tuple[str, ...]
    infrastructure_failures: int
    infrastructure_failure_types: dict[str, int]
    agent_failures: int
    agent_timeouts: int
    model_service_failures: int
    verifier_environment_failures: int
    scored_trials: int
    pass_at_1: int
    pass_at_2: int
    repaired: int
    first_attempt_known: int
    internal_statuses: dict[str, int]
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    language_stats: dict[str, dict[str, int | float]]
    unfinished_trials: tuple[str, ...] = ()
    job_finished: bool | None = None
    raw_rewards: dict[str, float] = field(default_factory=dict)
    trial_assessments: dict[str, dict[str, Any]] = field(default_factory=dict)
    telemetry_missing: tuple[str, ...] = ()
    controller: dict[str, Any] = field(default_factory=dict)

    @property
    def pass_at_1_rate(self) -> float:
        return self.pass_at_1 / self.first_attempt_known if self.first_attempt_known else 0.0

    @property
    def pass_at_2_rate(self) -> float:
        return self.pass_at_2 / self.scored_trials if self.scored_trials else 0.0

    @property
    def final_pass_rate(self) -> float:
        '''Observed official successes divided by all trials, including failures.'''
        return sum(reward == 1 for reward in self.raw_rewards.values()) / self.total_trials if self.total_trials else 0.0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value['pass_at_1_rate'] = self.pass_at_1_rate
        value['pass_at_2_rate'] = self.pass_at_2_rate
        # ``pass_at_2`` is the historical field used by the Aider adapter.
        # It remains a diagnostic over eligible trials. The official aggregate
        # uses every observed reward and the complete trial denominator.
        value['final_pass_count'] = sum(reward == 1 for reward in self.raw_rewards.values())
        value['final_pass_rate'] = self.final_pass_rate
        value['final_pass_denominator'] = self.total_trials
        value['eligible_final_pass_count'] = self.pass_at_2
        value['eligible_final_pass_rate'] = self.pass_at_2_rate
        value['raw_pass_count'] = sum(reward == 1 for reward in self.raw_rewards.values())
        value['verifier_scored_count'] = sum(
            item['verifier_state'] in {'passed', 'failed'} for item in self.trial_assessments.values()
        )
        value['eligible_trial_count'] = self.scored_trials
        value['primary_outcome_counts'] = dict(Counter(
            item['primary_outcome'] for item in self.trial_assessments.values()
        ))
        value['infrastructure_failures_semantics'] = 'Legacy aggregate of model, verifier, infrastructure and unknown failures; do not add it to subcategories.'
        for stats in value['language_stats'].values():
            first_known = int(stats.get('first_attempt_known', 0))
            scored = int(stats.get('scored_trials', 0))
            stats['pass_at_1_rate'] = (
                int(stats.get('pass_at_1', 0)) / first_known
                if first_known
                else 0.0
            )
            stats['pass_at_2_rate'] = (
                int(stats.get('pass_at_2', 0)) / scored
                if scored
                else 0.0
            )
        return value


def summarize_run(run_dir: Path) -> RunSummary:
    job_path = run_dir / 'result.json'
    job = _read_object(job_path) if job_path.is_file() else {}
    job_finished = bool(job.get('finished_at')) if 'finished_at' in job else None
    trial_dirs = tuple(
        path
        for path in run_dir.iterdir()
        if path.is_dir()
        and (
            (path / 'config.json').is_file()
            or (path / 'result.json').is_file()
        )
    )
    trial_results = tuple(
        path / 'result.json'
        for path in trial_dirs
        if (path / 'result.json').is_file()
    )
    missing_dirs = tuple(
        path for path in trial_dirs if not (path / 'result.json').is_file()
    )
    unfinished = tuple(
        path for path in missing_dirs
        if job_finished is False and _missing_result_type(path) == 'MissingResult'
        and not any(p.get('stop_reason') for p in _trial_payloads(path))
    )
    failed_missing = tuple(path for path in missing_dirs if path not in unfinished)
    infrastructure_failures = 0
    infrastructure_failure_types: Counter[str] = Counter()
    scored_trials = 0
    pass_at_1 = 0
    pass_at_2 = 0
    repaired = 0
    first_attempt_known = 0
    statuses: Counter[str] = Counter()
    agent_failures = 0
    agent_timeouts = 0
    model_service_failures = 0
    verifier_environment_failures = 0
    raw_rewards: dict[str, float] = {}
    assessments: dict[str, dict[str, Any]] = {}
    for path in unfinished:
        assessments[path.name] = asdict(TrialAssessment('unfinished', 'not_run', False))
    for path in failed_missing:
        payloads = _trial_payloads(path)
        assessment = assess_trial(path, {}, payloads)
        if assessment.primary_outcome in {'agent_timeout', 'model_protocol_failure'}:
            agent_timeouts += int(assessment.primary_outcome == 'agent_timeout')
            model_service_failures += int(assessment.primary_outcome == 'model_protocol_failure')
            infrastructure_failures += int(assessment.primary_outcome == 'model_protocol_failure')
            if assessment.primary_outcome == 'model_protocol_failure':
                infrastructure_failure_types[assessment.failure_type] += 1
            assessments[path.name] = asdict(assessment)
        elif _missing_result_type(path) == 'AgentTimeout':
            agent_timeouts += 1
            assessments[path.name] = asdict(TrialAssessment('agent_timeout', 'unknown', False, 'AgentTimeout'))
        else:
            infrastructure_failures += 1
            infrastructure_failure_types[_missing_result_type(path)] += 1
            assessments[path.name] = asdict(TrialAssessment('infrastructure_failure', 'unknown', False, _missing_result_type(path)))
    model_calls = tool_calls = input_tokens = output_tokens = 0
    language_stats: dict[str, dict[str, int | float]] = {}

    for result_path in trial_results:
        try:
            result = _read_object(result_path)
        except (OSError, ValueError):
            result = {'exception_info': {'exception_type': 'InvalidResult'}}
        payloads = _trial_payloads(result_path.parent)
        assessment = assess_trial(result_path.parent, result, payloads)
        assessments[result_path.parent.name] = asdict(assessment)
        outcome = assessment.primary_outcome
        agent_failures += int(outcome == 'agent_failure')
        agent_timeouts += int(outcome == 'agent_timeout')
        model_service_failures += int(outcome == 'model_protocol_failure')
        verifier_environment_failures += int(outcome == 'verifier_environment_failure')
        is_infrastructure = outcome in {'infrastructure_failure', 'verifier_environment_failure', 'model_protocol_failure', 'unknown'}
        if is_infrastructure:
            infrastructure_failures += 1
            infrastructure_failure_types[assessment.failure_type] += 1
        language = _language_for_result(result)
        if language is not None:
            stats = language_stats.setdefault(language, _empty_language_stats())
            stats['total_trials'] += 1
        if is_infrastructure and language is not None:
            language_stats[language]['infrastructure_failures'] += 1
        verifier = result.get('verifier_result')
        rewards = verifier.get('rewards') if isinstance(verifier, dict) else None
        final_reward = rewards.get('reward') if isinstance(rewards, dict) else None
        if _valid_reward(final_reward):
            raw_rewards[result_path.parent.name] = float(final_reward)
        # A verifier reward can coexist with a provider/setup exception;
        # such a record is not a scored code result.
        if assessment.evaluation_eligible:
            scored_trials += 1
            pass_at_2 += int(final_reward == 1)
            if language is not None:
                stats = language_stats[language]
                stats['scored_trials'] += 1
                stats['pass_at_2'] += int(final_reward == 1)

        attempt_path = result_path.parent / 'verifier' / 'aider-attempts.json'
        if attempt_path.is_file():
            attempt = _read_object(attempt_path)
            first_reward = attempt.get('first_reward')
            compile_failure_without_reward = bool(
                attempt.get('missing_reward_compile_failure')
            )
            if (
                assessment.evaluation_eligible
                and (
                    _valid_reward(first_reward)
                    or compile_failure_without_reward
                )
            ):
                first_attempt_known += 1
                if language is not None:
                    stats = language_stats[language]
                    stats['first_attempt_known'] += 1
                first_passed = (
                    _valid_reward(first_reward)
                    and first_reward == 1
                )
                pass_at_1 += int(first_passed)
                if language is not None:
                    stats['pass_at_1'] += int(first_passed)
                repaired += int(
                    not first_passed
                    and final_reward == 1
                    and bool(attempt.get('feedback_requested'))
                )
                if language is not None:
                    stats['repaired'] += int(
                        not first_passed
                        and final_reward == 1
                        and bool(attempt.get('feedback_requested'))
                    )
        elif assessment.evaluation_eligible:
            # Terminal-Bench and SWE-bench do not use the Aider feedback
            # plugin.  Their final verifier result is therefore also their
            # first-attempt result; counting it here keeps pass@1 meaningful
            # instead of reporting a misleading zero.
            first_attempt_known += 1
            first_passed = final_reward == 1
            pass_at_1 += int(first_passed)
            if language is not None:
                language_stats[language]['first_attempt_known'] += 1
                language_stats[language]['pass_at_1'] += int(first_passed)

    telemetry_missing = []
    for trial_dir in trial_dirs:
        payloads = _trial_payloads(trial_dir)
        if not payloads:
            telemetry_missing.append(trial_dir.name)
        for payload in payloads:
            statuses[str(payload.get('status') or 'unknown')] += 1
            model_calls += _safe_int(payload.get('model_calls'))
            tool_calls += _safe_int(payload.get('tool_calls'))
            usage = payload.get('usage')
            if isinstance(usage, dict):
                input_tokens += _safe_int(usage.get('input_tokens'))
                input_tokens += _safe_int(
                    usage.get('cache_creation_input_tokens')
                )
                input_tokens += _safe_int(usage.get('cache_read_input_tokens'))
                output_tokens += _safe_int(usage.get('output_tokens'))

    from benchmark.harbor.controller import controller_status
    return RunSummary(
        total_trials=len(trial_dirs),
        missing_results=tuple(path.name for path in missing_dirs),
        infrastructure_failures=infrastructure_failures,
        infrastructure_failure_types=dict(infrastructure_failure_types),
        agent_failures=agent_failures,
        agent_timeouts=agent_timeouts,
        model_service_failures=model_service_failures,
        verifier_environment_failures=verifier_environment_failures,
        scored_trials=scored_trials,
        pass_at_1=pass_at_1,
        pass_at_2=pass_at_2,
        repaired=repaired,
        first_attempt_known=first_attempt_known,
        internal_statuses=dict(statuses),
        model_calls=model_calls,
        tool_calls=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        language_stats=language_stats,
        unfinished_trials=tuple(path.name for path in unfinished),
        job_finished=job_finished,
        raw_rewards=raw_rewards,
        trial_assessments=assessments,
        telemetry_missing=tuple(telemetry_missing),
        controller=controller_status(run_dir.parent),
    )


_MODEL_FAILURE_REASONS = {
    'server_error', 'stream_interrupted', 'empty_model_response', 'incomplete_tool_call',
    'stream_termination_missing', 'connection_error', 'timeout', 'rate_limit',
    'authentication_error', 'invalid_model_protocol', 'conflicting_model_content',
    'provider_protocol_error', 'overloaded', 'provider_error', 'http_401', 'http_403', 'http_429',
}


@dataclass(frozen=True, slots=True)
class TrialAssessment:
    primary_outcome: str
    verifier_state: str
    evaluation_eligible: bool
    failure_type: str = ''
    observed_faults: tuple[str, ...] = ()


def _valid_reward(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _trial_payloads(trial_dir: Path) -> tuple[dict[str, Any], ...]:
    # Exact legacy copies have no attempt identity; deduplicate those bytes.
    # Different legacy payloads remain visible rather than guessing chronology.
    unique: dict[str, dict[str, Any]] = {}
    for log in sorted((trial_dir / 'agent').glob('forgecode*.txt')):
        payload = _last_forgecode_result(log)
        if payload is not None:
            identity = str(payload.get('turn_id') or json.dumps(payload, sort_keys=True))
            unique[identity] = payload
    if unique:
        return tuple(unique.values())
    # A host timeout can interrupt the final stdout payload/export. Journals
    # live directly on the log mount, so account for observed requests there.
    turns: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for log in sorted((trial_dir / 'agent').rglob('session-*.jsonl')):
        for line in log.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue  # A concurrently written final line may be incomplete.
            identity = event.get('uuid') or json.dumps(event, sort_keys=True)
            if identity in seen:
                continue
            seen.add(identity)
            turn_id = event.get('turn_id')
            if not turn_id:
                continue
            turn = turns.setdefault(turn_id, {'status': 'unknown', 'model_calls': 0, 'tool_calls': 0, 'usage': {}})
            payload = event.get('payload', {})
            if event.get('type') == 'model_request_started':
                turn['model_calls'] += 1
            elif event.get('type') == 'model_request_finished':
                for key, amount in (payload.get('usage') or {}).items():
                    turn['usage'][key] = turn['usage'].get(key, 0) + _safe_int(amount)
            elif event.get('type') == 'tool_requested':
                turn['tool_calls'] += 1
            elif event.get('type') == 'turn_completed':
                turn['status'] = payload.get('status', 'unknown')
                turn['stop_reason'] = payload.get('stop_reason', '')
    return tuple(turns.values())


def assess_trial(trial_dir: Path, result: dict[str, Any], payloads: tuple[dict[str, Any], ...]) -> TrialAssessment:
    '''Choose one outcome only after all independent observations are loaded.'''
    exception = result.get('exception_info')
    exception_type = _exception_type(exception)
    verifier = result.get('verifier_result')
    rewards = verifier.get('rewards') if isinstance(verifier, dict) else None
    reward = rewards.get('reward') if isinstance(rewards, dict) else None
    environment = _verifier_environment_failure(trial_dir)
    reasons = {str(p.get('stop_reason')) for p in payloads if p.get('stop_reason')}
    model_reasons = reasons & _MODEL_FAILURE_REASONS
    timeout = ('time_budget_exhausted' in reasons or _is_agent_timeout(exception)
               or _missing_result_type(trial_dir) == 'AgentTimeout')
    faults = sorted(model_reasons | (reasons & {'time_budget_exhausted'}) | ({'agent_timeout'} if timeout else set())
                    | ({exception_type} if exception else set()) | ({environment} if environment else set()))
    if environment or 'verifier' in exception_type.casefold():
        return TrialAssessment('verifier_environment_failure', 'environment_failed', False,
                               environment or exception_type, tuple(faults))
    verifier_state = ('passed' if reward == 1 else 'failed') if _valid_reward(reward) else 'unknown'
    if model_reasons or _is_model_service_failure(exception):
        return TrialAssessment('model_protocol_failure', verifier_state, False,
                               sorted(model_reasons)[0] if model_reasons else exception_type, tuple(faults))
    if timeout:
        # A deadline is an agent outcome; a contradictory reward remains raw.
        return TrialAssessment('agent_timeout', verifier_state, _valid_reward(reward),
                               'AgentTimeout', tuple(faults))
    if exception is not None:
        return TrialAssessment('infrastructure_failure', 'unknown', False, exception_type, tuple(faults))
    if not _valid_reward(reward):
        return TrialAssessment('unknown', 'unknown', False, 'MissingOrInvalidReward', tuple(faults))
    return TrialAssessment('pass' if reward == 1 else 'agent_failure', verifier_state, True)


def _empty_language_stats() -> dict[str, int | float]:
    return {
        'total_trials': 0,
        'infrastructure_failures': 0,
        'scored_trials': 0,
        'pass_at_1': 0,
        'pass_at_2': 0,
        'repaired': 0,
        'first_attempt_known': 0,
    }


def _exception_type(exception_info: object) -> str:
    if isinstance(exception_info, dict):
        value = exception_info.get('exception_type')
        if isinstance(value, str) and value:
            return value
    return 'unknown'


def _is_agent_timeout(exception_info: object) -> bool:
    exception_type = _exception_type(exception_info).casefold()
    return 'agent' in exception_type and 'timeout' in exception_type


def _is_model_service_failure(exception_info: object) -> bool:
    if not isinstance(exception_info, dict):
        return False
    exception_type = _exception_type(exception_info).casefold()
    if exception_type.startswith(('api', 'anthropic', 'provider', 'modelcall', 'modelprotocol')) or 'ratelimit' in exception_type:
        return True
    text = ' '.join(
        str(exception_info.get(key) or '')
        for key in ('exception_type', 'exception_message')
    ).casefold()
    markers = (
        'rate limit',
        'ratelimit',
        'connection refused',
        'connection reset',
        'service unavailable',
        'http 5',
    )
    return any(marker in text for marker in markers)


_VERIFIER_ENVIRONMENT_MARKERS: tuple[tuple[str, str], ...] = (
    ('/root/.local/bin/env: no such file or directory', 'uv_bootstrap'),
    ('uvx: command not found', 'uv_bootstrap'),
    ('command not found: uvx', 'uv_bootstrap'),
    ('curl: (35) openssl ssl_connect', 'network_bootstrap'),
    ('temporary failure resolving', 'network_bootstrap'),
    ('network is unreachable', 'network_bootstrap'),
)


def _verifier_environment_failure(trial_dir: Path) -> str | None:
    '''Classify only strong verifier bootstrap signals, not task failures.'''
    candidates = (
        trial_dir / 'verifier' / 'test-stdout.txt',
        trial_dir / 'verifier' / 'test-stderr.txt',
        trial_dir / 'verifier' / 'verifier.log',
    )
    text_parts: list[str] = []
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text_parts.append(path.read_text(encoding='utf-8', errors='replace'))
        except OSError:
            continue
    text = '\n'.join(text_parts).casefold()
    # uv can write reward=0 after failing before pytest even starts. Require
    # the paired installer diagnostics, not an arbitrary task timeout string.
    if ('failed to download `' in text
            and 'failed to download distribution due to network timeout' in text
            and 'short test summary info' not in text):
        return 'VerifierEnvironment:dependency_download_timeout'
    for marker, kind in _VERIFIER_ENVIRONMENT_MARKERS:
        if marker in text:
            return f'VerifierEnvironment:{kind}'
    return None


def _missing_result_type(trial_dir: Path) -> str:
    status_path = trial_dir / 'agent' / 'forgecode-status.json'
    if not status_path.is_file():
        return 'MissingResult'
    try:
        status = _read_object(status_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return 'InvalidAgentStatus'
    if status.get('timed_out') is True:
        return 'AgentTimeout'
    exit_code = status.get('exit_code')
    if isinstance(exit_code, int):
        return f'AgentExit{exit_code}'
    return 'MissingResult'


def _language_for_result(result: dict[str, Any]) -> str | None:
    task_name = result.get('task_name')
    if not isinstance(task_name, str) or not task_name.startswith('polyglot_'):
        return None
    parts = task_name.split('_', 2)
    return parts[1] if len(parts) == 3 and parts[1] else None


def _last_forgecode_result(path: Path) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        if not line.startswith(_RESULT_PREFIX):
            continue
        try:
            value = json.loads(line[len(_RESULT_PREFIX):])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            latest = value
    return latest


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected a JSON object: {path}')
    return value


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    text = json.dumps(
        summarize_run(args.run_dir).to_dict(),
        ensure_ascii=False,
        indent=2,
    ) + '\n'
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(text, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
