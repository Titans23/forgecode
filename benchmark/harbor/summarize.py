'''Summarize Harbor rewards and ForgeCode turn telemetry without conflating infra failures.'''

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any


_RESULT_PREFIX = 'FORGECODE_BENCHMARK_RESULT='


@dataclass(frozen=True, slots=True)
class RunSummary:
    total_trials: int
    missing_results: tuple[str, ...]
    infrastructure_failures: int
    infrastructure_failure_types: dict[str, int]
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

    @property
    def pass_at_1_rate(self) -> float:
        return self.pass_at_1 / self.first_attempt_known if self.first_attempt_known else 0.0

    @property
    def pass_at_2_rate(self) -> float:
        return self.pass_at_2 / self.scored_trials if self.scored_trials else 0.0

    @property
    def final_pass_rate(self) -> float:
        '''Final verifier success rate for both one- and two-attempt jobs.'''
        return self.pass_at_2_rate

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value['pass_at_1_rate'] = self.pass_at_1_rate
        value['pass_at_2_rate'] = self.pass_at_2_rate
        # ``pass_at_2`` is the historical field used by the Aider adapter.
        # For benchmarks without a repair protocol it is simply the final
        # (and only) verifier result.  The explicit aliases make that fact
        # clear to downstream dashboards.
        value['final_pass_count'] = self.pass_at_2
        value['final_pass_rate'] = self.final_pass_rate
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
    infrastructure_failures = len(missing_dirs)
    infrastructure_failure_types: Counter[str] = Counter(
        _missing_result_type(path) for path in missing_dirs
    )
    scored_trials = 0
    pass_at_1 = 0
    pass_at_2 = 0
    repaired = 0
    first_attempt_known = 0
    statuses: Counter[str] = Counter()
    model_calls = tool_calls = input_tokens = output_tokens = 0
    language_stats: dict[str, dict[str, int | float]] = {}

    for result_path in trial_results:
        result = _read_object(result_path)
        language = _language_for_result(result)
        if language is not None:
            stats = language_stats.setdefault(language, _empty_language_stats())
            stats['total_trials'] += 1
        exception_info = result.get('exception_info')
        if exception_info is not None:
            infrastructure_failures += 1
            infrastructure_failure_types[_exception_type(exception_info)] += 1
            if language is not None:
                language_stats[language]['infrastructure_failures'] += 1
        verifier = result.get('verifier_result')
        rewards = verifier.get('rewards') if isinstance(verifier, dict) else None
        final_reward = rewards.get('reward') if isinstance(rewards, dict) else None
        # A verifier reward can coexist with a provider/setup exception;
        # such a record is not a scored code result.
        if exception_info is None and isinstance(final_reward, int | float):
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
            if exception_info is None and (
                isinstance(first_reward, int | float)
                or compile_failure_without_reward
            ):
                first_attempt_known += 1
                if language is not None:
                    stats = language_stats[language]
                    stats['first_attempt_known'] += 1
                first_passed = (
                    isinstance(first_reward, int | float)
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
        elif exception_info is None and isinstance(final_reward, int | float):
            # Terminal-Bench and SWE-bench do not use the Aider feedback
            # plugin.  Their final verifier result is therefore also their
            # first-attempt result; counting it here keeps pass@1 meaningful
            # instead of reporting a misleading zero.
            first_attempt_known += 1
            first_passed = final_reward == 1
            pass_at_1 += int(first_passed)

        for log in sorted((result_path.parent / 'agent').glob('forgecode*.txt')):
            payload = _last_forgecode_result(log)
            if payload is None:
                continue
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

    return RunSummary(
        total_trials=len(trial_dirs),
        missing_results=tuple(path.name for path in missing_dirs),
        infrastructure_failures=infrastructure_failures,
        infrastructure_failure_types=dict(infrastructure_failure_types),
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
    )


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
