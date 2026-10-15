'''Summarize Harbor rewards and ForgeCode turn telemetry without conflating infra failures.'''

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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


@dataclass(frozen=True, slots=True)
class TrialAssessment:
    primary_outcome: str
    verifier_state: str
    evaluation_eligible: bool
    failure_type: str = ''
    observed_faults: tuple[str, ...] = ()


def _valid_reward(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 1


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
    structured = trial_dir / 'agent/forgecode-result.json'
    if structured.is_file():
        return (_read_object(structured),)
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
    from benchmark.adapters.protocol import classify_result
    terminal = payloads[-1] if payloads else {}
    if _missing_result_type(trial_dir) == 'AgentTimeout':
        terminal = {**terminal, 'status': 'timed_out'}
    classified = classify_result(result, terminal, verifier_fault=_verifier_environment_failure(trial_dir))
    legacy = {'passed': 'pass', 'acceptance_failed': 'agent_failure',
              'model_failure': 'model_protocol_failure', 'budget_exhausted': 'agent_timeout',
              'grader_failure': 'verifier_environment_failure',
              'environment_failure': 'infrastructure_failure', 'harness_failure': 'infrastructure_failure',
              'cancelled_or_incomplete': 'unfinished', 'unknown': 'infrastructure_failure' if result.get('exception_info') else 'unknown'}
    verifier = ('passed' if classified['grade_result'] == 'pass' else 'failed') if classified['grading_state'] == 'graded' else ('environment_failed' if classified['grading_state'] == 'grader_error' else 'unknown')
    eligible = classified['grading_state'] == 'graded' and classified['category'] in {'passed', 'acceptance_failed', 'budget_exhausted'}
    return TrialAssessment(legacy[classified['category']], verifier, eligible,
        classified['termination_reason'] if classified['category'] not in {'passed', 'acceptance_failed'} else '',
        tuple(classified['observed_faults']))


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


def evaluation_report(run_dir: Path) -> dict:
    """Project raw official grades and terminal evidence onto the frozen task set."""
    from benchmark.adapters.protocol import CATEGORIES, classify_result
    from benchmark.adapters.docker import cleanup_state
    from benchmark.harbor.controller import controller_status
    experiment = _read_object(run_dir / 'experiment.json')
    job_dir = run_dir / 'job'
    official = _read_object(job_dir / 'result.json') if (job_dir / 'result.json').is_file() else None
    controller = controller_status(run_dir)
    tasks = experiment['taskset']['tasks']
    trials = {}
    model_calls = tool_calls = input_tokens = output_tokens = 0
    for directory in sorted(job_dir.iterdir()) if job_dir.is_dir() else []:
        if not directory.is_dir() or not (directory / 'config.json').is_file():
            continue
        result = _read_object(directory / 'result.json') if (directory / 'result.json').is_file() else {}
        config = _read_object(directory / 'config.json')
        name = result.get('task_name') or Path((config.get('task') or {}).get('path', '')).name
        matches = [task for task, record in tasks.items()
                   if task == name or Path(record['directory']).name == name]
        if len(matches) != 1:
            raise ValueError(f'Trial identity is outside the frozen task set: {name}')
        payloads = _trial_payloads(directory)
        terminal = payloads[-1] if payloads else {}
        if _missing_result_type(directory) == 'AgentTimeout':
            terminal = {**terminal, 'status': 'timed_out'}
        classification = classify_result(result, terminal, verifier_fault=_verifier_environment_failure(directory),
            cancelled=controller['status'] == 'cancelled' and not result.get('finished_at'))
        trials.setdefault(matches[0], []).append({**classification, 'trial': directory.name,
            'official_result': str((directory / 'result.json').relative_to(run_dir)),
            'evidence': {'exception': result.get('exception_info'), 'agent_terminal': terminal}})
        for payload in payloads:
            model_calls += _safe_int(payload.get('model_calls'))
            tool_calls += _safe_int(payload.get('tool_calls'))
            usage = payload.get('usage') or {}
            input_tokens += sum(_safe_int(usage.get(key)) for key in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'))
            output_tokens += _safe_int(usage.get('output_tokens'))
    outcomes = {}
    for task in tasks:
        attempts = trials.get(task, [])
        outcomes[task] = (attempts[-1] if attempts else classify_result({}, cancelled=controller['status'] == 'cancelled'))
        outcomes[task] = {**outcomes[task], 'attempts': attempts}
    receipts = run_dir / 'resources.jsonl'
    resource_state = cleanup_state([json.loads(line) for line in receipts.read_text(encoding='utf-8').splitlines()]) if receipts.exists() else 'unknown'
    counts = Counter(item['category'] for item in outcomes.values())
    graded = sum(item['grading_state'] == 'graded' for item in outcomes.values())
    elapsed = None
    if controller.get('started_at'):
        end = datetime.fromisoformat(controller['finished_at']) if controller.get('finished_at') else datetime.now(timezone.utc)
        elapsed = max(0, (end - datetime.fromisoformat(controller['started_at'])).total_seconds())
    try:
        trace = evaluation_trace_index(run_dir)
    except (OSError, ValueError) as error:
        trace = {'available': False, 'reason': str(error)}
    return {'schema_version': 'forge.evaluation.report.v1', 'run_id': experiment['run_id'],
            'dataset': experiment['dataset'], 'usage': experiment['usage'], 'controller': controller,
            'cleanup_state': resource_state, 'official_job_result': official, 'tasks': outcomes, 'trajectory': trace,
            'classification_counts': {key: counts[key] for key in CATEGORIES},
            'task_count': len(tasks), 'valid_grading_rate': graded / len(tasks) if tasks else 0,
            'elapsed_seconds': elapsed, 'model_calls': model_calls, 'tool_calls': tool_calls,
            'input_tokens': input_tokens, 'output_tokens': output_tokens,
            'score_policy': 'Official raw results are preserved; failures remain in the frozen task denominator.'}


def evaluation_trace_index(run_dir: Path) -> dict:
    """Index private Journal facts and official outputs without copying a second log."""
    from hashlib import sha256
    from forge.sessions.trajectory import journal_events, journal_completeness
    from benchmark.adapters.protocol import file_bytes
    journals, artifacts, seen = [], [], set()
    for trial in sorted((run_dir / 'job').glob('*')):
        if not trial.is_dir(): continue
        for path in sorted(trial.rglob('*')):
            relative = path.relative_to(run_dir).as_posix()
            if not path.is_file() or 'forgecode-source' in path.parts or path.suffix == '.lock': continue
            raw = file_bytes(path)
            digest = sha256(raw).hexdigest()
            artifacts.append({'path': relative, 'sha256': digest, 'bytes': len(raw)})
            if path.name.startswith('session-') and path.suffix == '.jsonl':
                if digest in seen: continue
                seen.add(digest)
                events = journal_events(path)
                journals.append({'path': relative, 'sha256': digest,
                    **journal_completeness(events, truncated_tail=not raw.endswith(b'\n'))})
    return {'schema_version': 'forge.evaluation.trace-index.v1', 'journals': journals,
            'artifacts': artifacts, 'private': True, 'fact_source': 'Journal'}


def export_atif(run_dir: Path, destination: Path) -> dict:
    from forge.sessions.trajectory import journal_to_atif
    from forge.config import model_environment
    experiment = _read_object(run_dir / 'experiment.json')
    index = evaluation_trace_index(run_dir)
    if not index['journals']:
        raise ValueError('No available Journal trajectory to export.')
    values = model_environment()
    secrets = tuple(value for key, value in values.items() if any(part in key.upper() for part in ('KEY', 'SECRET', 'PASSWORD', 'TOKEN')))
    trajectories = []
    for journal in index['journals']:
        trajectory = journal_to_atif(run_dir / journal['path'], secrets=secrets)
        trajectory['extra'].update(run_id=experiment['run_id'], dataset=experiment['dataset'], usage='evaluation-only')
        trajectories.append(trajectory)
    destination.mkdir(parents=True, exist_ok=False)
    for i, trajectory in enumerate(trajectories):
        (destination / f'trajectory-{i + 1}.json').write_text(json.dumps(trajectory, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # The share index is deliberately limited to hashes and file associations.
    share_index = {'schema_version': 'forge.evaluation.atif-export.v1', 'run_id': experiment['run_id'],
        'usage': 'evaluation-only', 'files': [f'trajectory-{i + 1}.json' for i in range(len(trajectories))],
        'source_journals': index['journals']}
    (destination / 'index.json').write_text(json.dumps(share_index, indent=2) + '\n', encoding='utf-8')
    return share_index


def comparison_conditions(run_dir: Path) -> dict:
    experiment = _read_object(run_dir / 'experiment.json')
    source = experiment.get('source_manifest', {}).get('files', {})
    # Only Harness package bytes may change in a Harness comparison. Runner,
    # verifier, dependency and protocol code remain controlled conditions.
    harness_files = {key: value for key, value in source.items() if key.startswith('forge/')}
    fixed_files = {key: value for key, value in source.items() if not key.startswith('forge/') and key != 'README.md'}
    driver = experiment.get('driver_manifest', {}).get('files', {})
    driver_files = {key: value for key, value in driver.items() if not key.startswith('forge/') and key != 'README.md'}
    tasks = experiment.get('taskset', {}).get('tasks', {})
    task_identities = {key: {name: value for name, value in item.items() if name != 'directory'} for key, item in tasks.items()}
    receipts = run_dir / 'resources.jsonl'
    environment = {}
    if receipts.is_file():
        for line in receipts.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if row.get('phase') == 'environment':
                environment.setdefault(row['task'], []).append(row['identity'])
    environment = {key: sorted(values, key=lambda value: json.dumps(value, sort_keys=True)) for key, values in environment.items()}
    return {'dataset': {'name': experiment.get('dataset'), 'version': experiment.get('taskset', {}).get('version'), 'tasks': task_identities},
        'budget': experiment.get('budget'), 'protocol': {key: experiment.get(key) for key in
            ('harbor_version', 'attempts', 'task_retries', 'concurrency', 'timeout_multiplier', 'agent_setup_timeout_multiplier')},
        'grader': experiment.get('grader'), 'dependencies_and_runner': fixed_files or None,
        'driver': driver_files or None,
        'environment': environment or None,
        'model': {'configuration': experiment.get('model'), 'parameters': experiment.get('parameters')},
        'harness': {'agent': experiment.get('agent'), 'configuration': experiment.get('harness'), 'files': harness_files}}


def compare_runs(baseline: Path, candidate: Path, *, change: str) -> dict:
    from benchmark.core.metrics import compare_conditions
    from decimal import Decimal
    conditions = [comparison_conditions(path) for path in (baseline, candidate)]
    compared = compare_conditions(*conditions, change=change)
    reports = [evaluation_report(path) for path in (baseline, candidate)]
    for side, path, condition in zip(('baseline', 'candidate'), (baseline, candidate), conditions, strict=True):
        if not condition['dataset']['tasks'] or not condition['dataset']['version']:
            compared['missing_evidence'].append(f'{side}.frozen_taskset')
        for key in ('harbor_version', 'attempts', 'task_retries', 'concurrency', 'timeout_multiplier', 'agent_setup_timeout_multiplier'):
            if condition['protocol'].get(key) is None:
                compared['missing_evidence'].append(f'{side}.protocol.{key}')
        receipts = path / 'resources.jsonl'
        identified = {row.get('session') for row in (json.loads(line) for line in receipts.read_text(encoding='utf-8').splitlines())
                      if row.get('phase') == 'environment' and row.get('identity')} if receipts.is_file() else set()
        report = reports[0 if side == 'baseline' else 1]
        for task, result in report['tasks'].items():
            if result.get('trial', '') + '__env' not in identified:
                compared['missing_evidence'].append(f'{side}.{task}.environment')
    changes = {}
    for task in sorted(reports[0]['tasks'].keys() | reports[1]['tasks'].keys()):
        left, right = (report['tasks'].get(task, {}) for report in reports)
        rewards = [item.get('raw_reward_decimal') for item in (left, right)]
        changes[task] = {'baseline_category': left.get('category'), 'candidate_category': right.get('category'),
            'baseline_reward': rewards[0], 'candidate_reward': rewards[1],
            'reward_delta': str(Decimal(rewards[1]) - Decimal(rewards[0])) if all(value is not None for value in rewards) else None,
            'baseline_attempts': left.get('attempts', []), 'candidate_attempts': right.get('attempts', [])}
        for side, result in (('baseline', left), ('candidate', right)):
            if len(result.get('attempts', [])) != 1 or result.get('grading_state') != 'graded':
                compared['missing_evidence'].append(f'{side}.{task}.single_completed_grade')
    if compared['missing_evidence']: compared['controlled_conditions'] = False
    return {'schema_version': 'forge.evaluation.comparison.v1', **compared, 'tasks': changes,
        'official_results': [report['official_job_result'] for report in reports],
        'interpretation': 'Descriptive per-task changes only; no statistical significance or causal improvement claim.'}


def export_sft(run_dir: Path, destination: Path, *, tokenizer_dir: Path | None) -> dict:
    """Offline export contract for qualified training inputs; public tests never qualify."""
    from hashlib import sha256
    from extensions.qwen_tool_distillation import (evaluation_only_source, TraceRejected,
        journal_training_records, clean_episode, validate_training_episode, tokenizer_identity,
        qwen_token_counter, build_sft_rows, write_sft_jsonl)
    experiment = _read_object(run_dir / 'experiment.json')
    taskset_path = run_dir / 'tasks/taskset.json'
    taskset = _read_object(taskset_path) if taskset_path.is_file() else experiment.get('taskset', {})
    if evaluation_only_source({key: experiment.get(key) for key in ('dataset', 'usage', 'taskset')}) or evaluation_only_source(taskset):
        raise TraceRejected(['Terminal-Bench and SWE-bench test inputs are permanently evaluation-only.'])
    training = experiment.get('training') or {}
    expected_tokenizer = training.get('tokenizer') or {}
    if not training.get('sources') or not expected_tokenizer or tokenizer_dir is None:
        raise TraceRejected(['Training source, overlap evidence and a local target tokenizer are required.'])
    identity = tokenizer_identity(tokenizer_dir, model_id=expected_tokenizer.get('id', ''), revision=expected_tokenizer.get('revision', ''))
    if identity != expected_tokenizer:
        raise TraceRejected(['Target tokenizer bytes differ from the declared snapshot.'])
    report = evaluation_report(run_dir)
    index = evaluation_trace_index(run_dir)
    if not index['journals']: raise TraceRejected(['No Journal is available for training export.'])
    episodes = []
    for journal in index['journals']:
        source = training['sources'].get(journal['sha256'])
        if not source: raise TraceRejected(['Journal is not bound to training provenance.'])
        frozen_task = taskset.get('tasks', {}).get(source.get('task_id'), {})
        if not frozen_task.get('content_sha256') or frozen_task['content_sha256'] != (source.get('metadata') or {}).get('task_content_sha256'):
            raise TraceRejected(['Training source does not match frozen task content.'])
        task = report['tasks'].get(source.get('task_id'), {})
        if task.get('category') != 'passed' or len(task.get('attempts', [])) != 1:
            raise TraceRejected(['Training requires one independently successful task attempt.'])
        trial = Path(journal['path']).parts[1]
        if trial != task.get('trial'):
            raise TraceRejected(['Journal and independent acceptance belong to different trials.'])
        evidence = run_dir / task['official_result']
        outcome = {'success': True, 'validated': True, 'completion_gate_accepted': True,
                   'validator': experiment.get('grader'), 'evidence_sha256': sha256(evidence.read_bytes()).hexdigest()}
        records = clean_episode(journal_training_records(run_dir / journal['path'], source=source, outcome=outcome))
        validate_training_episode(records, target_tokenizer=identity)
        episodes.append(records)
    # Validate every episode before loading optional libraries or creating output.
    counter = qwen_token_counter(str(tokenizer_dir))
    rows = [row for records in episodes for row in build_sft_rows(records, token_counter=counter, target_tokenizer=identity)]
    if not rows: raise TraceRejected(['No complete training targets were produced.'])
    destination.mkdir(parents=True, exist_ok=False)
    write_sft_jsonl(destination / 'samples.jsonl', rows)
    manifest = {'schema_version': 'forge.training.export.v1', 'rows': len(rows), 'tokenizer': identity,
                'source_journals': [{**item, 'training_eligible': True,
                    'training_reason': 'Source, split, overlap, acceptance and tokenizer qualified at export.'} for item in index['journals']],
                'run_id': experiment['run_id']}
    (destination / 'index.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


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
